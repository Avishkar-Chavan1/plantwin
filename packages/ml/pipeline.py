"""Time-ordered residual-learning pipeline; never random-shuffles temporal observations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import joblib
import numpy as np

try:  # A fresh declared environment uses scikit-learn; constrained local environments retain a real fallback.
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor

    SKLEARN_AVAILABLE = True
except (
    ImportError,
    ValueError,
):  # pragma: no cover - exercised only with incompatible external binary wheels
    GradientBoostingRegressor = None  # type: ignore[assignment,misc]
    RandomForestRegressor = None  # type: ignore[assignment,misc]
    SKLEARN_AVAILABLE = False


class ResidualEstimator(Protocol):
    def fit(self, features: np.ndarray, target: np.ndarray) -> ResidualEstimator: ...
    def predict(self, features: np.ndarray) -> np.ndarray: ...


@dataclass
class RidgeResidualRegressor:
    """Regularized linear residual learner and extrapolation-aware validation candidate."""

    regularization: float = 1e-6
    coefficients: np.ndarray | None = None
    intercept: float = 0.0

    def fit(self, features: np.ndarray, target: np.ndarray) -> RidgeResidualRegressor:
        design = np.column_stack((np.ones(len(features)), features))
        penalty = np.eye(design.shape[1]) * self.regularization
        penalty[0, 0] = 0.0
        solution = np.linalg.solve(design.T @ design + penalty, design.T @ target)
        self.intercept = float(solution[0])
        self.coefficients = solution[1:]
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        if self.coefficients is None:
            raise ValueError("Model has not been trained")
        return self.intercept + np.asarray(features, dtype=float) @ self.coefficients

    @property
    def feature_importances_(self) -> np.ndarray:
        if self.coefficients is None:
            return np.array([])
        values = np.abs(self.coefficients)
        return values / values.sum() if values.sum() else values


@dataclass(frozen=True)
class TimeSplit:
    train_end: int
    validation_end: int
    total: int

    @classmethod
    def ordered(
        cls, observations: int, train_fraction: float = 0.6, validation_fraction: float = 0.2
    ) -> TimeSplit:
        if observations < 10:
            raise ValueError("At least 10 time-ordered observations are required")
        if (
            not 0 < train_fraction < 1
            or not 0 < validation_fraction < 1
            or train_fraction + validation_fraction >= 1
        ):
            raise ValueError("Invalid ordered split fractions")
        train_end = int(observations * train_fraction)
        validation_end = train_end + int(observations * validation_fraction)
        if train_end < 1 or validation_end >= observations:
            raise ValueError("Split creates an empty partition")
        return cls(train_end, validation_end, observations)

    def indices(self) -> tuple[slice, slice, slice]:
        return (
            slice(0, self.train_end),
            slice(self.train_end, self.validation_end),
            slice(self.validation_end, self.total),
        )


@dataclass(frozen=True)
class ModelMetrics:
    mae: float
    rmse: float
    r2: float | None
    mape: float | None
    bias: float

    def as_dict(self) -> dict[str, float | None]:
        return {
            "mae": self.mae,
            "rmse": self.rmse,
            "r2": self.r2,
            "mape": self.mape,
            "bias": self.bias,
        }


def metrics(actual: np.ndarray, predicted: np.ndarray) -> ModelMetrics:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    nonzero = np.abs(actual) > 1e-6
    mape = (
        float(np.mean(np.abs((actual[nonzero] - predicted[nonzero]) / actual[nonzero])) * 100)
        if nonzero.any()
        else None
    )
    r2 = (
        float(1 - np.sum((actual - predicted) ** 2) / np.sum((actual - np.mean(actual)) ** 2))
        if len(actual) >= 2 and np.std(actual) > 1e-12
        else None
    )
    return ModelMetrics(
        mae=float(np.mean(np.abs(actual - predicted))),
        rmse=float(np.mean((actual - predicted) ** 2) ** 0.5),
        r2=r2,
        mape=mape,
        bias=float(np.mean(predicted - actual)),
    )


@dataclass
class HybridResidualModel:
    feature_names: tuple[str, ...]
    residual_model: ResidualEstimator
    residual_standard_deviation: float
    algorithm: str

    def predict(
        self, features: np.ndarray, physics_prediction: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        matrix = np.asarray(features, dtype=float)
        baseline = np.asarray(physics_prediction, dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] != len(self.feature_names):
            raise ValueError("Feature matrix does not match saved feature schema")
        correction = self.residual_model.predict(matrix)
        hybrid = baseline + correction
        interval = 1.96 * self.residual_standard_deviation
        return baseline, hybrid, np.full_like(hybrid, interval, dtype=float)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @classmethod
    def load(cls, path: Path) -> HybridResidualModel:
        loaded = joblib.load(path)
        if not isinstance(loaded, cls):
            raise ValueError("Artifact is not a ProcessTwin hybrid residual model")
        return loaded


@dataclass(frozen=True)
class TrainingResult:
    model: HybridResidualModel
    split: TimeSplit
    training_comparisons: dict[str, ModelMetrics]
    validation_metrics: ModelMetrics
    test_metrics: ModelMetrics
    validation_comparisons: dict[str, ModelMetrics]
    test_comparisons: dict[str, ModelMetrics]


def _new_estimator(algorithm: str) -> ResidualEstimator:
    if algorithm == "ridge":
        return RidgeResidualRegressor()
    if algorithm not in {"random_forest", "gradient_boosting"}:
        raise ValueError("Supported algorithms: gradient_boosting, random_forest, ridge")
    if SKLEARN_AVAILABLE and algorithm == "random_forest":
        assert RandomForestRegressor is not None
        return cast(
            ResidualEstimator,
            RandomForestRegressor(
                n_estimators=250, min_samples_leaf=3, random_state=42, n_jobs=1
            ),
        )
    if SKLEARN_AVAILABLE:
        assert GradientBoostingRegressor is not None
        return cast(
            ResidualEstimator,
            GradientBoostingRegressor(
                n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42, loss="huber"
            ),
        )
    return RidgeResidualRegressor()


def _select_residual_estimator(
    *,
    algorithm: str,
    features: np.ndarray,
    actual: np.ndarray,
    baseline: np.ndarray,
    train: slice,
    validation: slice,
) -> tuple[str, ResidualEstimator]:
    """Choose a residual learner using only the chronological validation window.

    Tree ensembles do not extrapolate beyond their fitted feature ranges. A ridge residual
    candidate is therefore evaluated alongside them; this is particularly important when
    validation represents a later operating window. The held-out test window is never used
    for selection.
    """
    candidate_names = [algorithm] if algorithm == "ridge" else [algorithm, "ridge"]
    residual = actual - baseline
    scored: list[tuple[float, str, ResidualEstimator]] = []
    for candidate_name in candidate_names:
        estimator = _new_estimator(candidate_name)
        estimator.fit(features[train], residual[train])
        prediction = baseline[validation] + estimator.predict(features[validation])
        scored.append((metrics(actual[validation], prediction).mae, candidate_name, estimator))
    _, selected_name, selected = min(scored, key=lambda item: item[0])
    return selected_name, selected


def train_residual_model(
    features: np.ndarray,
    actual_target: np.ndarray,
    physics_prediction: np.ndarray,
    feature_names: Sequence[str],
    algorithm: str = "gradient_boosting",
) -> TrainingResult:
    """Fit residual only on historical train partition, then evaluate future windows."""
    matrix = np.asarray(features, dtype=float)
    actual = np.asarray(actual_target, dtype=float)
    baseline = np.asarray(physics_prediction, dtype=float)
    if matrix.ndim != 2 or len(actual) != len(matrix) or len(baseline) != len(matrix):
        raise ValueError(
            "Features, target and physics baseline must have matching observation count"
        )
    if (
        matrix.shape[1] != len(feature_names)
        or not np.isfinite(matrix).all()
        or not np.isfinite(actual).all()
        or not np.isfinite(baseline).all()
    ):
        raise ValueError("Feature schema mismatch or non-finite training data")
    split = TimeSplit.ordered(len(matrix))
    train, validation, test = split.indices()
    residual = actual - baseline
    selected_algorithm, estimator = _select_residual_estimator(
        algorithm=algorithm,
        features=matrix,
        actual=actual,
        baseline=baseline,
        train=train,
        validation=validation,
    )
    training_error = residual[train] - estimator.predict(matrix[train])
    model = HybridResidualModel(
        tuple(feature_names), estimator, float(np.std(training_error, ddof=1)), selected_algorithm
    )
    ml_only = _new_estimator(algorithm)
    ml_only.fit(matrix[train], actual[train])
    _, training_hybrid, _ = model.predict(matrix[train], baseline[train])
    training_comparisons = {
        "physics_only": metrics(actual[train], baseline[train]),
        "ml_only": metrics(actual[train], ml_only.predict(matrix[train])),
        "physics_plus_ml_residual": metrics(actual[train], training_hybrid),
    }
    _, validation_hybrid, _ = model.predict(matrix[validation], baseline[validation])
    _, test_hybrid, _ = model.predict(matrix[test], baseline[test])
    validation_comparisons = {
        "physics_only": metrics(actual[validation], baseline[validation]),
        "ml_only": metrics(actual[validation], ml_only.predict(matrix[validation])),
        "physics_plus_ml_residual": metrics(actual[validation], validation_hybrid),
    }
    test_comparisons = {
        "physics_only": metrics(actual[test], baseline[test]),
        "ml_only": metrics(actual[test], ml_only.predict(matrix[test])),
        "physics_plus_ml_residual": metrics(actual[test], test_hybrid),
    }
    return TrainingResult(
        model=model,
        split=split,
        training_comparisons=training_comparisons,
        validation_metrics=validation_comparisons["physics_plus_ml_residual"],
        test_metrics=test_comparisons["physics_plus_ml_residual"],
        validation_comparisons=validation_comparisons,
        test_comparisons=test_comparisons,
    )
