"""
models/isolation_forest.py
==========================
Isolation Forest–based anomaly detector for browser telemetry features.

The raw decision_function() output of sklearn's IsolationForest is a
negative-to-positive float (lower = more anomalous).  This class maps
that output to a [0, 1] anomaly score where 1.0 = most anomalous using
a min-max normalisation anchored to training-set statistics.
"""

import logging
import os

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import MinMaxScaler

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "saved_models", "isolation_forest.pkl")
MODEL_PATH = os.path.normpath(MODEL_PATH)


class IsolationForestDetector:
    """
    Isolation Forest wrapper for anomaly detection.

    Parameters
    ----------
    n_estimators  : number of trees in the forest
    contamination : expected fraction of anomalies in training data
    random_state  : reproducibility seed
    """

    def __init__(
        self,
        n_estimators: int = 200,
        contamination: float = 0.05,
        random_state: int = 42,
    ) -> None:
        self.n_estimators  = n_estimators
        self.contamination = contamination
        self.random_state  = random_state

        self.model: IsolationForest | None = None
        self.score_scaler: MinMaxScaler | None = None  # maps raw scores → [0,1]

        # Threshold in normalised score space; above this → anomaly
        self.threshold: float = 0.5

    # ──────────────────────────────────────────────────────────────────────────
    # Training
    # ──────────────────────────────────────────────────────────────────────────

    def train(self, X: np.ndarray) -> "IsolationForestDetector":
        """
        Fit the Isolation Forest on feature matrix *X*.

        The method also fits an internal MinMaxScaler on the decision function
        scores computed on the training set so that predict() always returns
        values in [0, 1].

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features)
            Should contain ONLY normal traffic samples for best performance.

        Returns
        -------
        self (for chaining)
        """
        logger.info("IsolationForestDetector: training on %d samples …", X.shape[0])

        self.model = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.model.fit(X)

        # Compute raw decision-function scores on training data
        raw_scores = self.model.decision_function(X)  # higher = more normal

        # Fit MinMaxScaler: invert so higher score = more anomalous
        inverted = -raw_scores.reshape(-1, 1)
        self.score_scaler = MinMaxScaler(feature_range=(0.0, 1.0))
        self.score_scaler.fit(inverted)

        # Tune threshold based on contamination
        self.tune_threshold(X)

        # Persist
        self.save()
        logger.info(
            "IsolationForestDetector: training complete – threshold=%.4f", self.threshold
        )
        return self

    # ──────────────────────────────────────────────────────────────────────────
    # Inference
    # ──────────────────────────────────────────────────────────────────────────

    def _raw_to_score(self, raw: np.ndarray) -> np.ndarray:
        """Map decision_function output to [0, 1] anomaly scores."""
        if self.score_scaler is None:
            # Fallback: simple sigmoid inversion
            return 1.0 / (1.0 + np.exp(raw))
        inverted = -raw.reshape(-1, 1)
        scores = self.score_scaler.transform(inverted).flatten()
        return np.clip(scores, 0.0, 1.0)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Score samples for anomaly.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features)

        Returns
        -------
        anomaly_scores : np.ndarray float32, shape (n_samples,), range [0, 1]
        is_anomaly     : np.ndarray bool,    shape (n_samples,)
        """
        if self.model is None:
            raise RuntimeError("IsolationForestDetector: model not trained – call train() or load() first")

        if X.ndim == 1:
            X = X.reshape(1, -1)

        raw_scores     = self.model.decision_function(X)
        anomaly_scores = self._raw_to_score(raw_scores).astype(np.float32)
        is_anomaly     = anomaly_scores >= self.threshold
        return anomaly_scores, is_anomaly

    def predict_single(self, x: np.ndarray) -> tuple[float, bool]:
        """
        Convenience wrapper for a single feature vector.

        Parameters
        ----------
        x : np.ndarray, shape (n_features,)

        Returns
        -------
        (anomaly_score: float, is_anomaly: bool)
        """
        scores, flags = self.predict(x.reshape(1, -1))
        return float(scores[0]), bool(flags[0])

    # ──────────────────────────────────────────────────────────────────────────
    # Threshold tuning
    # ──────────────────────────────────────────────────────────────────────────

    def tune_threshold(
        self,
        X: np.ndarray,
        percentile: float | None = None,
    ) -> float:
        """
        Set the anomaly threshold.

        Strategy:
        - If *percentile* is given, use that percentile of scores on X.
        - Otherwise derive the percentile from `self.contamination`
          (i.e. flag the top `contamination` fraction as anomalies).

        Parameters
        ----------
        X          : feature matrix used for scoring
        percentile : explicit percentile override (0–100)

        Returns
        -------
        threshold as float
        """
        if self.model is None:
            raise RuntimeError("Model not trained yet")

        scores, _ = self.predict(X)
        if percentile is None:
            percentile = 100.0 * (1.0 - self.contamination)
        self.threshold = float(np.percentile(scores, percentile))
        logger.info(
            "IsolationForestDetector: threshold tuned to %.4f (p%.1f)",
            self.threshold,
            percentile,
        )
        return self.threshold

    # ──────────────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def save(self, path: str | None = None) -> None:
        """Serialise the detector (model + scaler + threshold) with joblib."""
        path = path or MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "model":        self.model,
            "score_scaler": self.score_scaler,
            "threshold":    self.threshold,
            "n_estimators": self.n_estimators,
            "contamination":self.contamination,
        }
        joblib.dump(payload, path)
        logger.info("IsolationForestDetector: saved to %s", path)

    def load(self, path: str | None = None) -> "IsolationForestDetector":
        """Load a persisted detector from disk.

        Returns
        -------
        self (for chaining)
        """
        path = path or MODEL_PATH
        if not os.path.exists(path):
            raise FileNotFoundError(f"IsolationForest model not found at {path}")
        payload = joblib.load(path)
        self.model        = payload["model"]
        self.score_scaler = payload.get("score_scaler")
        self.threshold    = payload.get("threshold", 0.5)
        self.n_estimators = payload.get("n_estimators", self.n_estimators)
        self.contamination= payload.get("contamination", self.contamination)
        logger.info(
            "IsolationForestDetector: loaded from %s (threshold=%.4f)", path, self.threshold
        )
        return self

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """Return True if the model is loaded and ready for inference."""
        return self.model is not None

    def __repr__(self) -> str:
        status = "trained" if self.is_ready() else "untrained"
        return (
            f"IsolationForestDetector("
            f"n_estimators={self.n_estimators}, "
            f"contamination={self.contamination}, "
            f"threshold={self.threshold:.4f}, "
            f"status={status})"
        )
