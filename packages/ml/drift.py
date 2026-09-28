from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from math import isfinite, log
from typing import Any


@dataclass(frozen=True)
class DriftResult:
    status: str
    reasons: tuple[str, ...]
    metrics: dict[str, dict[str, float | bool]]


def _finite_sample(values: Any, name: str) -> list[float]:
    raw = values.ravel().tolist() if hasattr(values, "ravel") else list(values)
    sample = [float(value) for value in raw if isfinite(float(value))]
    if len(sample) < 2:
        raise ValueError(f"{name} drift comparison requires at least two finite values per window")
    return sample


def _quantile(sorted_values: list[float], fraction: float) -> float:
    index = (len(sorted_values) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def _histogram(values: list[float], edges: list[float]) -> list[int]:
    counts = [0] * (len(edges) - 1)
    for value in values:
        index = len(counts) - 1
        for bin_index in range(len(counts)):
            if edges[bin_index] <= value < edges[bin_index + 1]:
                index = bin_index
                break
        counts[index] += 1
    return counts


def _quantile_edges(reference: list[float], bins: int) -> list[float]:
    ordered = sorted(reference)
    candidates = [_quantile(ordered, index / bins) for index in range(bins + 1)]
    edges: list[float] = []
    for edge in candidates:
        if not edges or edge > edges[-1]:
            edges.append(edge)
    if len(edges) < 3:
        spread = max(abs(ordered[0]) * 1e-6, 1e-9)
        edges = [ordered[0] - spread, ordered[-1] + spread]
    edges[0] = float("-inf")
    edges[-1] = float("inf")
    return edges


def population_stability_index(reference: Any, current: Any, bins: int = 10) -> float:
    """Calculate PSI using reference-quantile bins and smoothed distributions."""
    baseline = _finite_sample(reference, "reference")
    observed = _finite_sample(current, "current")
    if bins < 2:
        raise ValueError("PSI requires at least two bins")
    edges = _quantile_edges(baseline, bins)
    expected_counts = _histogram(baseline, edges)
    actual_counts = _histogram(observed, edges)
    alpha = 1e-6
    expected_total = sum(expected_counts) + alpha * len(expected_counts)
    actual_total = sum(actual_counts) + alpha * len(actual_counts)
    expected = [(count + alpha) / expected_total for count in expected_counts]
    actual = [(count + alpha) / actual_total for count in actual_counts]
    return sum((right - left) * log(right / left) for left, right in zip(expected, actual, strict=True))


def _jensen_shannon(reference: list[float], current: list[float], bins: int) -> float:
    minimum = min(min(reference), min(current))
    maximum = max(max(reference), max(current))
    if maximum <= minimum:
        return 0.0
    edges = [minimum + (maximum - minimum) * index / bins for index in range(bins + 1)]
    edges[-1] = float("inf")
    left_counts = _histogram(reference, edges)
    right_counts = _histogram(current, edges)
    alpha = 1e-6
    left_total = sum(left_counts) + bins * alpha
    right_total = sum(right_counts) + bins * alpha
    left = [(count + alpha) / left_total for count in left_counts]
    right = [(count + alpha) / right_total for count in right_counts]
    midpoint = [(a + b) / 2 for a, b in zip(left, right, strict=True)]
    divergence = 0.0
    for a, b, middle in zip(left, right, midpoint, strict=True):
        divergence += 0.5 * a * log(a / middle) + 0.5 * b * log(b / middle)
    return divergence


def compare_drift(
    reference: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    psi_threshold: float = 0.2,
    ks_pvalue_threshold: float = 0.01,
    js_threshold: float = 0.1,
    bins: int = 10,
) -> DriftResult:
    """Detect covariate/target/residual distribution changes; does not retrain models."""
    if set(reference) != set(current) or not reference:
        raise ValueError("Reference and current drift windows must have identical non-empty signals")
    if not 0 < ks_pvalue_threshold < 1 or psi_threshold <= 0 or js_threshold <= 0 or bins < 2:
        raise ValueError("Drift thresholds and bin count are invalid")
    ks_2samp = import_module("scipy.stats").ks_2samp
    results: dict[str, dict[str, float | bool]] = {}
    reasons: list[str] = []
    for name in sorted(reference):
        baseline = _finite_sample(reference[name], f"reference {name}")
        observed = _finite_sample(current[name], f"current {name}")
        psi = population_stability_index(baseline, observed, bins)
        ks = ks_2samp(baseline, observed, alternative="two-sided", method="auto")
        ks_statistic = float(ks.statistic)
        ks_pvalue = float(ks.pvalue)
        js = _jensen_shannon(baseline, observed, bins)
        psi_drift = psi >= psi_threshold
        ks_drift = ks_pvalue < ks_pvalue_threshold
        js_drift = js >= js_threshold
        results[name] = {
            "psi": psi,
            "ks_statistic": ks_statistic,
            "ks_pvalue": ks_pvalue,
            "jensen_shannon_divergence": js,
            "psi_drift": psi_drift,
            "ks_drift": ks_drift,
            "js_drift": js_drift,
            "reference_count": float(len(baseline)),
            "current_count": float(len(observed)),
        }
        if psi_drift or (ks_drift and js_drift):
            reasons.append(f"{name}:PSI={psi:.4g},KS_p={ks_pvalue:.4g},JS={js:.4g}")
    return DriftResult(
        status="MODEL_DRIFT_DETECTED" if reasons else "NO_DRIFT_DETECTED",
        reasons=tuple(reasons),
        metrics=results,
    )
