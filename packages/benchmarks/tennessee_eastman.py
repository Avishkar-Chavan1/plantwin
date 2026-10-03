"""Tennessee Eastman benchmark ingestion and time-series surrogate validation.

This module intentionally models the Tennessee Eastman Process (TEP) files as a
public *simulation benchmark*.  It is not an adapter for a real historian and it
does not convert TEP observations into evidence for the reference CSTR model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

import numpy as np

from packages.ml.drift import DriftResult, compare_drift
from packages.ml.pipeline import ModelMetrics, TimeSplit, metrics

TEP_SOURCE_REPOSITORY = "https://github.com/camaramm/tennessee-eastman-profBraatz"
TEP_RAW_BASE_URL = "https://raw.githubusercontent.com/camaramm/tennessee-eastman-profBraatz/master"
TEP_SAMPLE_INTERVAL_S = 180
TEP_CONTINUOUS_MEASUREMENTS = 22
TEP_MEASUREMENTS = 41
TEP_MANIPULATED_VARIABLES = 11
TEP_VARIABLE_COUNT = TEP_MEASUREMENTS + TEP_MANIPULATED_VARIABLES
REACTOR_TEMPERATURE_INDEX = 8  # XMEAS(9), zero based.

# The files named *_te are separately published test trajectories.  Fault 13 is
# documented by the source as a slow reaction-kinetics drift, making it a useful
# challenge condition for drift and envelope checks.
TEP_PUBLIC_SOURCES: dict[str, str] = {
    "nominal_calibration": f"{TEP_RAW_BASE_URL}/d00.dat",
    "nominal_official_test": f"{TEP_RAW_BASE_URL}/d00_te.dat",
    "fault_13_kinetics_drift": f"{TEP_RAW_BASE_URL}/d13_te.dat",
}

TEP_VARIABLE_NAMES: tuple[str, ...] = tuple(
    [f"XMEAS({index})" for index in range(1, TEP_MEASUREMENTS + 1)]
    + [f"XMV({index})" for index in range(1, TEP_MANIPULATED_VARIABLES + 1)]
)


@dataclass(frozen=True)
class TEPDataset:
    """A row-per-sample, finite TEP trajectory with a synthetic elapsed-time axis."""

    name: str
    source_url: str
    values: np.ndarray
    source_sha256: str
    sampling_interval_s: int = TEP_SAMPLE_INTERVAL_S

    def __post_init__(self) -> None:
        matrix = np.asarray(self.values, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != TEP_VARIABLE_COUNT:
            raise ValueError(
                f"TEP data must be a 2-D matrix with {TEP_VARIABLE_COUNT} variables; got {matrix.shape}"
            )
        if len(matrix) < 10 or not np.isfinite(matrix).all():
            raise ValueError("TEP data needs at least 10 finite chronological samples")
        if self.sampling_interval_s <= 0:
            raise ValueError("TEP sampling interval must be positive")
        object.__setattr__(self, "values", matrix)

    @property
    def sample_count(self) -> int:
        return len(self.values)

    @property
    def elapsed_seconds(self) -> np.ndarray:
        return np.arange(self.sample_count, dtype=float) * self.sampling_interval_s

    @property
    def variable_names(self) -> tuple[str, ...]:
        return TEP_VARIABLE_NAMES


@dataclass(frozen=True)
class TEPSurrogate:
    """Standardized ridge one-step forecast for XMEAS(9), reactor temperature."""

    feature_mean: np.ndarray
    feature_scale: np.ndarray
    target_mean: float
    target_scale: float
    coefficients: np.ndarray
    training_feature_minimum: np.ndarray
    training_feature_maximum: np.ndarray
    envelope_margin_fraction: float
    ridge_alpha: float
    train_sample_count: int
    source_sha256: str
    training_features: np.ndarray
    training_target: np.ndarray

    def __post_init__(self) -> None:
        expected = (TEP_VARIABLE_COUNT,)
        for name in (
            "feature_mean",
            "feature_scale",
            "coefficients",
            "training_feature_minimum",
            "training_feature_maximum",
        ):
            values = np.asarray(getattr(self, name), dtype=float)
            if values.shape != expected or not np.isfinite(values).all():
                raise ValueError(f"{name} must contain {TEP_VARIABLE_COUNT} finite values")
            object.__setattr__(self, name, values)
        if np.any(self.feature_scale <= 0) or self.target_scale <= 0:
            raise ValueError("Feature and target scales must be positive")
        if self.ridge_alpha < 0 or self.envelope_margin_fraction < 0:
            raise ValueError("Ridge alpha and envelope margin must be non-negative")
        training_features = np.asarray(self.training_features, dtype=float)
        training_target = np.asarray(self.training_target, dtype=float)
        if (
            training_features.shape != (self.train_sample_count, TEP_VARIABLE_COUNT)
            or training_target.shape != (self.train_sample_count,)
            or not np.isfinite(training_features).all()
            or not np.isfinite(training_target).all()
        ):
            raise ValueError("Saved calibration reference samples do not match the model schema")
        object.__setattr__(self, "training_features", training_features)
        object.__setattr__(self, "training_target", training_target)

    @property
    def target_name(self) -> str:
        return TEP_VARIABLE_NAMES[REACTOR_TEMPERATURE_INDEX]

    def predict(self, features: np.ndarray) -> np.ndarray:
        matrix = np.asarray(features, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != TEP_VARIABLE_COUNT:
            raise ValueError(f"Expected a feature matrix with {TEP_VARIABLE_COUNT} columns")
        if not np.isfinite(matrix).all():
            raise ValueError("Features must be finite")
        standardized = (matrix - self.feature_mean) / self.feature_scale
        return cast(
            np.ndarray,
            (standardized @ self.coefficients) * self.target_scale + self.target_mean,
        )

    def outside_envelope(self, features: np.ndarray) -> np.ndarray:
        """Return rows outside the calibration feature min/max envelope plus tolerance.

        This is a deployment gate, not a physical safety limit.  It deliberately
        uses only the calibration partition and applies a 10% span tolerance to
        avoid treating small sensor-noise excursions as a new operating regime.
        """

        matrix = np.asarray(features, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != TEP_VARIABLE_COUNT:
            raise ValueError(f"Expected a feature matrix with {TEP_VARIABLE_COUNT} columns")
        span = self.training_feature_maximum - self.training_feature_minimum
        tolerance = np.maximum(span * self.envelope_margin_fraction, self.feature_scale * 0.1)
        lower = self.training_feature_minimum - tolerance
        upper = self.training_feature_maximum + tolerance
        return np.any((matrix < lower) | (matrix > upper), axis=1)


@dataclass(frozen=True)
class TEPEvaluation:
    """Metrics plus gates for one sequential trajectory evaluation."""

    dataset_name: str
    source_sha256: str
    observation_count: int
    all_observations_metrics: ModelMetrics
    in_envelope_metrics: ModelMetrics | None
    outside_envelope_count: int
    outside_envelope_fraction: float
    deployment_disposition: str
    drift: DriftResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "source_sha256": self.source_sha256,
            "observation_count": self.observation_count,
            "all_observations_metrics": self.all_observations_metrics.as_dict(),
            "in_envelope_metrics": (
                self.in_envelope_metrics.as_dict() if self.in_envelope_metrics is not None else None
            ),
            "outside_envelope_count": self.outside_envelope_count,
            "outside_envelope_fraction": self.outside_envelope_fraction,
            "deployment_disposition": self.deployment_disposition,
            "drift": {
                "status": self.drift.status,
                "reasons": list(self.drift.reasons),
                "metrics": self.drift.metrics,
            },
        }


@dataclass(frozen=True)
class TEPCalibration:
    """A calibration result with all chronological partitions evaluated."""

    model: TEPSurrogate
    split: TimeSplit
    train: TEPEvaluation
    validation: TEPEvaluation
    held_out_test: TEPEvaluation


def _read_tep_matrix(raw: bytes) -> np.ndarray:
    """Read numeric whitespace data and normalize either published orientation.

    ``d00.dat`` is variable-by-time (52 x 480), while the published ``*_te``
    test files are time-by-variable (960 x 52).  Both result in chronological
    rows with the 52 documented variables.
    """

    try:
        rows = [
            [float(token.replace("D", "E").replace("d", "e")) for token in line.split()]
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("TEP source must be UTF-8 whitespace-delimited finite numbers") from exc
    if not rows or len({len(row) for row in rows}) != 1:
        raise ValueError("TEP source has empty or ragged numeric rows")
    matrix = np.asarray(rows, dtype=float)
    if matrix.shape[1] == TEP_VARIABLE_COUNT:
        normalized = matrix
    elif matrix.shape[0] == TEP_VARIABLE_COUNT:
        normalized = matrix.T
    else:
        raise ValueError(
            f"TEP source needs a dimension of {TEP_VARIABLE_COUNT}; got {matrix.shape}"
        )
    if not np.isfinite(normalized).all():
        raise ValueError("TEP source contains non-finite values")
    return normalized


def load_tep_dataset(
    path: Path,
    *,
    name: str,
    source_url: str,
    sampling_interval_s: int = TEP_SAMPLE_INTERVAL_S,
) -> TEPDataset:
    """Ingest a downloaded TEP file with orientation and finite-value checks."""

    raw = path.read_bytes()
    return TEPDataset(
        name=name,
        source_url=source_url,
        values=_read_tep_matrix(raw),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        sampling_interval_s=sampling_interval_s,
    )


def download_public_tep_dataset(
    dataset_name: str,
    destination: Path,
    *,
    timeout_s: float = 30.0,
) -> Path:
    """Download a named allow-listed public source into a user-selected cache path."""

    try:
        source_url = TEP_PUBLIC_SOURCES[dataset_name]
    except KeyError as exc:
        available = ", ".join(sorted(TEP_PUBLIC_SOURCES))
        raise ValueError(
            f"Unknown public TEP dataset {dataset_name!r}; available: {available}"
        ) from exc
    request = Request(source_url, headers={"User-Agent": "ProcessTwin-public-benchmark/0.1"})
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed public allow-list above.
        raw = response.read()
    _read_tep_matrix(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return destination


def _forecast_pairs(dataset: TEPDataset) -> tuple[np.ndarray, np.ndarray]:
    return dataset.values[:-1], dataset.values[1:, REACTOR_TEMPERATURE_INDEX]


def _fit_ridge(
    features: np.ndarray,
    target: np.ndarray,
    *,
    source_sha256: str,
    ridge_alpha: float,
    envelope_margin_fraction: float,
) -> TEPSurrogate:
    if ridge_alpha < 0:
        raise ValueError("ridge_alpha must be non-negative")
    feature_mean = np.mean(features, axis=0)
    feature_scale = np.std(features, axis=0)
    feature_scale = np.where(feature_scale > 1e-12, feature_scale, 1.0)
    target_mean = float(np.mean(target))
    target_scale = float(np.std(target))
    if target_scale <= 1e-12:
        target_scale = 1.0
    standardized_features = (features - feature_mean) / feature_scale
    standardized_target = (target - target_mean) / target_scale
    penalty = np.eye(TEP_VARIABLE_COUNT) * ridge_alpha
    coefficients = np.linalg.solve(
        standardized_features.T @ standardized_features + penalty,
        standardized_features.T @ standardized_target,
    )
    return TEPSurrogate(
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        target_mean=target_mean,
        target_scale=target_scale,
        coefficients=coefficients,
        training_feature_minimum=np.min(features, axis=0),
        training_feature_maximum=np.max(features, axis=0),
        envelope_margin_fraction=envelope_margin_fraction,
        ridge_alpha=ridge_alpha,
        train_sample_count=len(features),
        source_sha256=source_sha256,
        training_features=features.copy(),
        training_target=target.copy(),
    )


def _drift_for_evaluation(
    model: TEPSurrogate,
    train_features: np.ndarray,
    train_target: np.ndarray,
    features: np.ndarray,
    target: np.ndarray,
) -> DriftResult:
    reference_prediction = model.predict(train_features)
    current_prediction = model.predict(features)
    return compare_drift(
        {
            "XMEAS(9)_input": train_features[:, REACTOR_TEMPERATURE_INDEX],
            "XMEAS(9)_target": train_target,
            "prediction_error": train_target - reference_prediction,
        },
        {
            "XMEAS(9)_input": features[:, REACTOR_TEMPERATURE_INDEX],
            "XMEAS(9)_target": target,
            "prediction_error": target - current_prediction,
        },
    )


def _evaluate_pairs(
    model: TEPSurrogate,
    *,
    dataset_name: str,
    source_sha256: str,
    train_features: np.ndarray,
    train_target: np.ndarray,
    features: np.ndarray,
    target: np.ndarray,
) -> TEPEvaluation:
    prediction = model.predict(features)
    outside = model.outside_envelope(features)
    inside = ~outside
    outside_count = int(np.sum(outside))
    if outside_count:
        disposition = "ABSTAIN_OUTSIDE_CALIBRATION_ENVELOPE"
    else:
        disposition = "IN_ENVELOPE_RESEARCH_ONLY"
    return TEPEvaluation(
        dataset_name=dataset_name,
        source_sha256=source_sha256,
        observation_count=len(target),
        all_observations_metrics=metrics(target, prediction),
        in_envelope_metrics=metrics(target[inside], prediction[inside]) if np.any(inside) else None,
        outside_envelope_count=outside_count,
        outside_envelope_fraction=outside_count / len(target),
        deployment_disposition=disposition,
        drift=_drift_for_evaluation(model, train_features, train_target, features, target),
    )


def calibrate_tep_surrogate(
    dataset: TEPDataset,
    *,
    ridge_alpha: float = 1.0,
    envelope_margin_fraction: float = 0.10,
) -> TEPCalibration:
    """Calibrate on first 60% and evaluate later 20%/20% without shuffling."""

    features, target = _forecast_pairs(dataset)
    split = TimeSplit.ordered(len(target))
    train_slice, validation_slice, test_slice = split.indices()
    train_features, train_target = features[train_slice], target[train_slice]
    model = _fit_ridge(
        train_features,
        train_target,
        source_sha256=dataset.source_sha256,
        ridge_alpha=ridge_alpha,
        envelope_margin_fraction=envelope_margin_fraction,
    )
    return TEPCalibration(
        model=model,
        split=split,
        train=_evaluate_pairs(
            model,
            dataset_name=f"{dataset.name}:train",
            source_sha256=dataset.source_sha256,
            train_features=train_features,
            train_target=train_target,
            features=train_features,
            target=train_target,
        ),
        validation=_evaluate_pairs(
            model,
            dataset_name=f"{dataset.name}:validation",
            source_sha256=dataset.source_sha256,
            train_features=train_features,
            train_target=train_target,
            features=features[validation_slice],
            target=target[validation_slice],
        ),
        held_out_test=_evaluate_pairs(
            model,
            dataset_name=f"{dataset.name}:held_out_test",
            source_sha256=dataset.source_sha256,
            train_features=train_features,
            train_target=train_target,
            features=features[test_slice],
            target=target[test_slice],
        ),
    )


def evaluate_tep_surrogate(model: TEPSurrogate, dataset: TEPDataset) -> TEPEvaluation:
    """Evaluate a frozen surrogate on a separately supplied chronological trajectory."""

    features, target = _forecast_pairs(dataset)
    return _evaluate_pairs(
        model,
        dataset_name=dataset.name,
        source_sha256=dataset.source_sha256,
        train_features=model.training_features,
        train_target=model.training_target,
        features=features,
        target=target,
    )


def _metric_row(name: str, evaluation: TEPEvaluation) -> str:
    current = evaluation.all_observations_metrics
    in_envelope = evaluation.in_envelope_metrics
    in_envelope_rmse = f"{in_envelope.rmse:.4f}" if in_envelope is not None else "n/a"
    return (
        f"| {name} | {evaluation.observation_count} | {current.mae:.4f} | {current.rmse:.4f} | "
        f"{in_envelope_rmse} | {evaluation.outside_envelope_count} "
        f"({evaluation.outside_envelope_fraction:.1%}) | {evaluation.drift.status} | "
        f"{evaluation.deployment_disposition} |"
    )


def render_validation_report(
    calibration_dataset: TEPDataset,
    nominal_test_dataset: TEPDataset,
    drift_test_dataset: TEPDataset,
    calibration: TEPCalibration,
    nominal_test: TEPEvaluation,
    drift_test: TEPEvaluation,
) -> str:
    """Render a report containing both results and the limits of their meaning."""

    source_rows = "\n".join(
        [
            f"| `d00.dat` nominal calibration | {calibration_dataset.sample_count} | `{calibration_dataset.source_sha256}` |",
            f"| `d00_te.dat` nominal official test | {nominal_test_dataset.sample_count} | `{nominal_test_dataset.source_sha256}` |",
            f"| `d13_te.dat` fault 13 / slow kinetics drift | {drift_test_dataset.sample_count} | `{drift_test_dataset.source_sha256}` |",
        ]
    )
    metric_rows = "\n".join(
        [
            _metric_row("Chronological calibration train", calibration.train),
            _metric_row("Chronological validation", calibration.validation),
            _metric_row("Chronological held-out test", calibration.held_out_test),
            _metric_row("Separate nominal official test", nominal_test),
            _metric_row("Fault-13 slow-kinetics-drift challenge", drift_test),
        ]
    )
    return f"""# Tennessee Eastman benchmark validation report

Generated by `python -m packages.benchmarks.tennessee_eastman --write-report docs/validation/tennessee-eastman.md`.

## Scope and data provenance

This report evaluates one **public simulated process benchmark**, not plant historian data. The [Tennessee Eastman Process source]({TEP_SOURCE_REPOSITORY}) publishes nominal and fault trajectories for process-control and fault-detection research. Each observation has 41 process measurements (`XMEAS(1:41)`) and 11 manipulated variables (`XMV(1:11)`); its source documentation says records are written every 180 seconds. This workflow uses reactor temperature, `XMEAS(9)` (°C), as the one-step-ahead target and the full observation at time *t* as the feature vector. It forecasts `XMEAS(9)` at *t + 180 s*.

| Source file | Samples after ingestion | SHA-256 of downloaded source |
| --- | ---: | --- |
{source_rows}

The ingestion checks finite numeric values and normalizes both published orientations to samples × 52 variables. In this exact download, `d00.dat` is 52 × {calibration_dataset.sample_count}; the `*_te` files are 960 × 52. The repository README describes 480-row training files, so the pipeline records the downloaded source hash and actual shape rather than silently assuming the documentation's row count. The record index is converted to an elapsed-time axis at 180-second spacing because these files do not contain absolute timestamps.

## Calibration protocol

The calibrated model is a standardized ridge, one-step **data-driven surrogate**. It is not a mechanistic Tennessee Eastman model, not a fitted version of ProcessTwin's CSTR physics, and it cannot identify CSTR parameters.

- Calibration trajectory: `d00.dat` nominal condition.
- Supervised examples: {calibration.split.total} (`x[t]` → `XMEAS(9)[t+1]`).
- Chronological split: first {calibration.split.train_end} ({calibration.split.train_end / calibration.split.total:.1%}) train; next {calibration.split.validation_end - calibration.split.train_end} ({(calibration.split.validation_end - calibration.split.train_end) / calibration.split.total:.1%}) validation; final {calibration.split.total - calibration.split.validation_end} ({(calibration.split.total - calibration.split.validation_end) / calibration.split.total:.1%}) held out. No shuffling, future inputs, future targets, or test-window hyperparameter selection were used.
- Regularization: ridge α = {calibration.model.ridge_alpha:g}; variables are standardized using the training partition only.
- Envelope: training-feature min/max expanded by 10% of its observed span (with a small scale floor). This is a data-coverage gate, not a process-safety limit.

## Forecast and drift results

RMSE and MAE are in °C for `XMEAS(9)` one 180-second step ahead. “All RMSE” is a research diagnostic even when a row is outside the calibration envelope. “In-envelope RMSE” is omitted when no rows pass the gate. The deployment disposition is intentionally conservative: any out-of-envelope trajectory should be withheld from use rather than treated as a validated forecast.

| Evaluation window | n | MAE | All RMSE | In-envelope RMSE | Outside envelope | Drift result | Deployment disposition |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
{metric_rows}

The drift screen compares the reactor-temperature input, one-step target, and prediction-error distributions using PSI, two-sample KS, and Jensen–Shannon divergence. Its default flags are PSI ≥ 0.2 or both KS *p* < 0.01 and JS ≥ 0.1. These are analytical screening thresholds, not alarm limits or an approval to retrain.

## Outside-envelope behavior

`d13_te.dat` is Fault 13, documented in the source as a slow reaction-kinetics drift. Its metrics are retained to show what the frozen nominal surrogate does under the challenge condition, but `{drift_test.outside_envelope_count}` of `{drift_test.observation_count}` forecast rows are outside its nominal calibration coverage. The correct operational behavior for this pipeline is `{drift_test.deployment_disposition}`; the all-row error is not a claim that the model is usable under that fault.

## What this does and does not prove

This work proves that ProcessTwin can reproducibly ingest a documented public process benchmark, maintain time ordering, fit a bounded-by-data surrogate, calculate MAE/RMSE, screen distribution shift, and gate extrapolation. The downloaded-source hashes above make this exact run auditable.

It does **not** prove predictive performance on an industrial plant, adequacy of the reference CSTR equations, sensor calibration or timing, causal correctness, safe control actions, fault diagnosis accuracy, fault detection sensitivity, long-horizon performance, robustness to missing/bad historian data, or validity outside the observed nominal envelope. TEP is simulated, and `d00_te`/`d13_te` are benchmark trajectories—not independent site validation. Any real deployment still needs site data provenance, units/tag mapping, instrument-quality review, an approved operating envelope, independent holdouts, acceptance criteria, and human authorization.
"""


def run_public_validation(cache_dir: Path) -> tuple[str, dict[str, Any]]:
    """Load three cached source files, run the pipeline, and return a report and JSON summary."""

    paths = {
        name: cache_dir / source_url.rsplit("/", maxsplit=1)[-1]
        for name, source_url in TEP_PUBLIC_SOURCES.items()
    }
    for name, path in paths.items():
        if not path.exists():
            download_public_tep_dataset(name, path)
    calibration_dataset = load_tep_dataset(
        paths["nominal_calibration"],
        name="nominal_calibration",
        source_url=TEP_PUBLIC_SOURCES["nominal_calibration"],
    )
    nominal_test_dataset = load_tep_dataset(
        paths["nominal_official_test"],
        name="nominal_official_test",
        source_url=TEP_PUBLIC_SOURCES["nominal_official_test"],
    )
    drift_test_dataset = load_tep_dataset(
        paths["fault_13_kinetics_drift"],
        name="fault_13_kinetics_drift",
        source_url=TEP_PUBLIC_SOURCES["fault_13_kinetics_drift"],
    )
    calibration = calibrate_tep_surrogate(calibration_dataset)
    nominal_test = evaluate_tep_surrogate(calibration.model, nominal_test_dataset)
    drift_test = evaluate_tep_surrogate(calibration.model, drift_test_dataset)
    report = render_validation_report(
        calibration_dataset,
        nominal_test_dataset,
        drift_test_dataset,
        calibration,
        nominal_test,
        drift_test,
    )
    summary = {
        "calibration": {
            "split": asdict(calibration.split),
            "train": calibration.train.as_dict(),
            "validation": calibration.validation.as_dict(),
            "held_out_test": calibration.held_out_test.as_dict(),
        },
        "nominal_official_test": nominal_test.as_dict(),
        "fault_13_kinetics_drift": drift_test.as_dict(),
    }
    return report, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the public Tennessee Eastman validation pipeline"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/public/tennessee_eastman"),
        help="local cache for downloaded public files (default: %(default)s)",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        default=Path("docs/validation/tennessee-eastman.md"),
        help="path to write the generated Markdown report (default: %(default)s)",
    )
    parser.add_argument(
        "--write-summary",
        type=Path,
        default=Path("docs/validation/tennessee-eastman.json"),
        help="path to write the generated JSON results (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    report, summary = run_public_validation(args.cache_dir)
    args.write_report.parent.mkdir(parents=True, exist_ok=True)
    args.write_report.write_text(report, encoding="utf-8")
    args.write_summary.parent.mkdir(parents=True, exist_ok=True)
    args.write_summary.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.write_report} and {args.write_summary}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint.
    raise SystemExit(main())
