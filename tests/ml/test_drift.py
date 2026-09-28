from __future__ import annotations

import numpy as np
import pytest
from packages.ml.drift import compare_drift, population_stability_index
from packages.ml.pipeline import TimeSplit, metrics, train_residual_model


def test_shifted_feature_and_target_distributions_flag_drift() -> None:
    rng = np.random.default_rng(37)
    reference = rng.normal(0.0, 1.0, 500)
    current = rng.normal(2.0, 1.2, 500)
    result = compare_drift(
        {"feature:temperature": reference, "target:yield": reference},
        {"feature:temperature": current, "target:yield": current},
    )
    assert result.status == "MODEL_DRIFT_DETECTED"
    assert all(item["psi_drift"] for item in result.metrics.values())
    assert result.metrics["feature:temperature"]["ks_pvalue"] < 0.01
    assert result.metrics["feature:temperature"]["jensen_shannon_divergence"] > 0.1
    assert result.reasons


def test_stable_distribution_does_not_trigger_drift() -> None:
    rng = np.random.default_rng(12)
    reference = rng.normal(0.0, 1.0, 1000)
    current = rng.normal(0.0, 1.0, 1000)
    result = compare_drift({"physics_residual": reference}, {"physics_residual": current})
    assert result.status == "NO_DRIFT_DETECTED"


def test_drift_inputs_validate_sample_count_and_schema() -> None:
    with pytest.raises(ValueError, match="identical"):
        compare_drift({"feature": np.arange(4)}, {"target": np.arange(4)})
    with pytest.raises(ValueError, match="at least two"):
        population_stability_index(np.array([1.0]), np.array([2.0]))


def test_hybrid_comparison_scores_three_models_on_ordered_windows() -> None:
    x = np.linspace(0, 1, 80).reshape(-1, 1)
    physics = 0.5 + 0.1 * x[:, 0]
    target = physics + 0.04 * np.sin(np.arange(80) / 6)
    result = train_residual_model(x, target, physics, ["temperature"])
    assert result.split == TimeSplit.ordered(80)
    expected = {"physics_only", "ml_only", "physics_plus_ml_residual"}
    assert set(result.training_comparisons) == expected
    assert set(result.validation_comparisons) == expected
    assert set(result.test_comparisons) == expected
    assert result.test_metrics.bias == pytest.approx(
        result.test_comparisons["physics_plus_ml_residual"].bias
    )


def test_metrics_report_signed_prediction_bias() -> None:
    result = metrics(np.array([1.0, 2.0, 3.0]), np.array([2.0, 3.0, 4.0]))
    assert result.bias == pytest.approx(1.0)
    assert result.mae == pytest.approx(1.0)


def test_future_targets_cannot_leak_into_residual_estimator_fit() -> None:
    x = np.linspace(0, 1, 60).reshape(-1, 1)
    physics = 0.25 + 0.5 * x[:, 0]
    target = physics + 0.03 * np.sin(np.arange(60) / 5)
    original = train_residual_model(x, target, physics, ["temperature"])
    changed_future = target.copy()
    changed_future[original.split.train_end :] += 1000
    altered = train_residual_model(x, changed_future, physics, ["temperature"])
    assert np.allclose(
        original.model.residual_model.predict(x[: original.split.train_end]),
        altered.model.residual_model.predict(x[: altered.split.train_end]),
    )
