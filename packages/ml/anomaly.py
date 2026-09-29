from __future__ import annotations

from typing import Any, TypeAlias, cast

import numpy as np
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]

FloatArray: TypeAlias = np.ndarray[Any, np.dtype[np.float64]]
BoolArray: TypeAlias = np.ndarray[Any, np.dtype[np.bool_]]


class PotentialAnomalyDetector:
    """Multivariate detector that reports potential anomalies, not equipment failures."""

    def __init__(self, contamination: float = 0.02) -> None:
        self.model: Any = IsolationForest(contamination=contamination, random_state=42)

    def fit(self, observations: FloatArray) -> PotentialAnomalyDetector:
        self.model.fit(np.asarray(observations, dtype=float))
        return self

    def score(self, observations: FloatArray) -> FloatArray:
        return cast(FloatArray, -self.model.score_samples(np.asarray(observations, dtype=float)))

    def predict(self, observations: FloatArray) -> BoolArray:
        return cast(BoolArray, self.model.predict(np.asarray(observations, dtype=float)) == -1)
