from __future__ import annotations

import numpy as np
from sklearn.ensemble import IsolationForest


class PotentialAnomalyDetector:
    """Multivariate detector that reports potential anomalies, not equipment failures."""

    def __init__(self, contamination: float = 0.02) -> None:
        self.model = IsolationForest(contamination=contamination, random_state=42)

    def fit(self, observations: np.ndarray) -> PotentialAnomalyDetector:
        self.model.fit(np.asarray(observations, dtype=float))
        return self

    def score(self, observations: np.ndarray) -> np.ndarray:
        return -self.model.score_samples(np.asarray(observations, dtype=float))

    def predict(self, observations: np.ndarray) -> np.ndarray:
        return self.model.predict(np.asarray(observations, dtype=float)) == -1
