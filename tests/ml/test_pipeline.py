from pathlib import Path

import numpy as np
import pytest
from packages.ml.pipeline import HybridResidualModel, TimeSplit, train_residual_model


def synthetic_training_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = np.column_stack((np.linspace(0, 1, 100), np.linspace(1, 2, 100)))
    physics = 0.5 + x[:, 0] * 0.2
    actual = physics + 0.05 * x[:, 1]
    return x, actual, physics


def test_ordered_split_never_places_future_rows_in_training() -> None:
    split = TimeSplit.ordered(100)
    train, validation, test = split.indices()
    assert train.stop == 60
    assert validation.start == 60 and validation.stop == 80
    assert test.start == 80


def test_schema_validation_and_serialization(tmp_path: Path) -> None:
    features, actual, physics = synthetic_training_data()
    result = train_residual_model(features, actual, physics, ["temperature", "flow"])
    artifact = tmp_path / "residual.joblib"
    result.model.save(artifact)
    restored = HybridResidualModel.load(artifact)
    _, predicted, uncertainty = restored.predict(features[80:], physics[80:])
    assert predicted.shape == uncertainty.shape == (20,)
    with pytest.raises(ValueError):
        restored.predict(features[:, :1], physics)


def test_held_out_metrics_are_reported_without_mape_zero_division() -> None:
    features, actual, physics = synthetic_training_data()
    result = train_residual_model(features, actual, physics, ["temperature", "flow"])
    assert result.test_metrics.mae < 0.01
    assert result.test_metrics.mape is not None
