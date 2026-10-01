"""
models/lstm_model.py
====================
LSTM Autoencoder anomaly detector for sequential browser telemetry features.

Architecture (sequence-to-sequence autoencoder):
  Input  (batch, seq_len=20, n_features=15)
    └─ Encoder LSTM  64 units, return_sequences=False
    └─ RepeatVector  seq_len
    └─ Decoder LSTM  64 units, return_sequences=True
    └─ TimeDistributed Dense  n_features
  Output (batch, seq_len, n_features)

The model is trained to RECONSTRUCT its input.  A high reconstruction
error (mean squared error per sample) indicates anomalous behaviour.

Anomaly scoring
---------------
  reconstruction_error = mean( (input - output)^2 )  per sequence
  threshold            = mean(train_errors) + 2 * std(train_errors)
  anomaly_score        = min(reconstruction_error / threshold, 1.0)
                         (clipped to [0, 1])
"""

import logging
import os
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(
    os.path.dirname(__file__), "..", "saved_models", "lstm_model.keras"
)
MODEL_PATH = os.path.normpath(MODEL_PATH)

# ──────────────────────────────────────────────────────────────────────────────
# Lazy TensorFlow import (avoids slow startup when only IF is needed)
# ──────────────────────────────────────────────────────────────────────────────

def _import_tf():
    """Import TensorFlow; raise a clear error if not installed."""
    try:
        import tensorflow as tf
        return tf
    except ImportError as exc:
        raise ImportError(
            "TensorFlow is required for LSTMDetector. "
            "Install it with: pip install tensorflow>=2.15.0"
        ) from exc


class LSTMDetector:
    """
    LSTM Autoencoder-based anomaly detector for time-series feature sequences.

    Parameters
    ----------
    sequence_length : number of time-steps per input window (default 20)
    n_features      : feature dimensionality per step (default 15)
    lstm_units      : hidden units in each LSTM layer (default 64)
    dropout_rate    : dropout between LSTM layers (default 0.1)
    """

    def __init__(
        self,
        sequence_length: int = 20,
        n_features: int = 15,
        lstm_units: int = 64,
        dropout_rate: float = 0.1,
    ) -> None:
        self.sequence_length = sequence_length
        self.n_features      = n_features
        self.lstm_units      = lstm_units
        self.dropout_rate    = dropout_rate

        self.model = None           # Keras model
        self.threshold: float = 0.1 # reconstruction error threshold
        self._tf = None             # cached TF module

    def _get_tf(self):
        if self._tf is None:
            self._tf = _import_tf()
        return self._tf

    # ──────────────────────────────────────────────────────────────────────────
    # Model construction
    # ──────────────────────────────────────────────────────────────────────────

    def build_model(self):
        """
        Construct and compile the LSTM autoencoder.

        Returns
        -------
        Compiled Keras model.
        """
        tf = self._get_tf()
        keras = tf.keras
        layers = keras.layers

        inp = layers.Input(shape=(self.sequence_length, self.n_features), name="encoder_input")

        # ── Encoder ─────────────────────────────────────────────────────────
        encoded = layers.LSTM(
            self.lstm_units,
            activation="tanh",
            recurrent_activation="sigmoid",
            return_sequences=False,
            name="encoder_lstm",
        )(inp)
        encoded = layers.Dropout(self.dropout_rate, name="encoder_dropout")(encoded)

        # ── Bottleneck repeat ────────────────────────────────────────────────
        repeated = layers.RepeatVector(self.sequence_length, name="repeat_vector")(encoded)

        # ── Decoder ─────────────────────────────────────────────────────────
        decoded = layers.LSTM(
            self.lstm_units,
            activation="tanh",
            recurrent_activation="sigmoid",
            return_sequences=True,
            name="decoder_lstm",
        )(repeated)
        decoded = layers.Dropout(self.dropout_rate, name="decoder_dropout")(decoded)

        # ── Reconstruction output ────────────────────────────────────────────
        output = layers.TimeDistributed(
            layers.Dense(self.n_features, activation="linear"),
            name="reconstruction",
        )(decoded)

        model = keras.Model(inputs=inp, outputs=output, name="lstm_autoencoder")
        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=1e-3),
            loss="mse",
            metrics=["mae"],
        )
        self.model = model
        logger.info(
            "LSTMDetector: model built – parameters: %d", model.count_params()
        )
        return model

    # ──────────────────────────────────────────────────────────────────────────
    # Data preparation
    # ──────────────────────────────────────────────────────────────────────────

    def _make_sequences(self, X: np.ndarray) -> np.ndarray:
        """
        Convert a 2-D feature matrix into 3-D sequences for the LSTM.

        If X already has shape (n, seq_len, n_features) return as-is.
        If X has shape (n, n_features) – treat each row as a single-step
        window and tile to produce (n, seq_len, n_features).
        """
        if X.ndim == 3:
            return X.astype(np.float32)
        if X.ndim == 2:
            # Stack consecutive rows into sequences
            n_samples = X.shape[0]
            sequences = []
            for i in range(n_samples):
                start = max(0, i - self.sequence_length + 1)
                chunk = X[start : i + 1]
                if chunk.shape[0] < self.sequence_length:
                    # Pad at the beginning with zeros
                    pad = np.zeros(
                        (self.sequence_length - chunk.shape[0], self.n_features),
                        dtype=np.float32,
                    )
                    chunk = np.vstack([pad, chunk])
                sequences.append(chunk)
            return np.array(sequences, dtype=np.float32)
        raise ValueError(f"Unsupported input shape: {X.shape}")

    # ──────────────────────────────────────────────────────────────────────────
    # Training
    # ──────────────────────────────────────────────────────────────────────────

    def train(
        self,
        X: np.ndarray,
        epochs: int = 50,
        batch_size: int = 32,
        validation_split: float = 0.1,
        verbose: int = 1,
    ) -> dict:
        """
        Train the LSTM autoencoder.

        Parameters
        ----------
        X              : feature matrix, shape (n_samples, n_features) or
                         (n_samples, seq_len, n_features)
        epochs         : training epochs
        batch_size     : mini-batch size
        validation_split: fraction of data used for validation
        verbose        : Keras verbosity level

        Returns
        -------
        history dict from Keras fit()
        """
        tf = self._get_tf()

        if self.model is None:
            self.build_model()

        X_seq = self._make_sequences(X)
        logger.info(
            "LSTMDetector: training on %d sequences (shape %s) …",
            X_seq.shape[0],
            X_seq.shape,
        )

        # Callbacks
        callbacks = [
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=5,
                restore_best_weights=True,
                verbose=1,
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=0.5,
                patience=3,
                min_lr=1e-6,
                verbose=1,
            ),
        ]

        history = self.model.fit(
            X_seq,
            X_seq,               # autoencoder: target = input
            epochs=epochs,
            batch_size=batch_size,
            validation_split=validation_split,
            callbacks=callbacks,
            verbose=verbose,
            shuffle=True,
        )

        # Compute threshold on training data
        self.set_threshold(X_seq)

        # Save
        self.save()
        logger.info(
            "LSTMDetector: training complete – threshold=%.6f", self.threshold
        )
        return history.history

    # ──────────────────────────────────────────────────────────────────────────
    # Threshold calibration
    # ──────────────────────────────────────────────────────────────────────────

    def set_threshold(self, X: np.ndarray, n_std: float = 2.0) -> float:
        """
        Compute the anomaly threshold as mean + n_std * std of reconstruction
        errors on the provided dataset.

        Parameters
        ----------
        X     : sequence data used for calibration
        n_std : number of standard deviations above the mean

        Returns
        -------
        threshold as float
        """
        if self.model is None:
            raise RuntimeError("Model not built yet – call build_model() or train() first")

        X_seq = self._make_sequences(X)
        errors = self._reconstruction_errors(X_seq)
        self.threshold = float(np.mean(errors) + n_std * np.std(errors))
        logger.info(
            "LSTMDetector: threshold set to %.6f (mean=%.6f, std=%.6f)",
            self.threshold,
            float(np.mean(errors)),
            float(np.std(errors)),
        )
        return self.threshold

    # ──────────────────────────────────────────────────────────────────────────
    # Inference
    # ──────────────────────────────────────────────────────────────────────────

    def _reconstruction_errors(self, X_seq: np.ndarray) -> np.ndarray:
        """
        Compute per-sample mean squared reconstruction error.

        Parameters
        ----------
        X_seq : np.ndarray, shape (n_samples, seq_len, n_features)

        Returns
        -------
        errors : np.ndarray, shape (n_samples,)
        """
        reconstructed = self.model.predict(X_seq, verbose=0)
        # MSE per sample: mean over (seq_len, n_features)
        errors = np.mean(np.square(X_seq - reconstructed), axis=(1, 2))
        return errors.astype(np.float32)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        Score samples for anomaly.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, n_features) or
                               (n_samples, seq_len, n_features)

        Returns
        -------
        anomaly_scores : np.ndarray float32, shape (n_samples,), range [0, 1]
        is_anomaly     : np.ndarray bool,    shape (n_samples,)
        """
        if self.model is None:
            raise RuntimeError("LSTMDetector: model not loaded – call train() or load() first")

        X_seq  = self._make_sequences(X)
        errors = self._reconstruction_errors(X_seq)

        # Normalise by threshold: score = error / threshold (capped at 1.0)
        if self.threshold > 0:
            anomaly_scores = np.clip(errors / self.threshold, 0.0, 1.0)
        else:
            anomaly_scores = np.clip(errors, 0.0, 1.0)

        is_anomaly = errors >= self.threshold
        return anomaly_scores.astype(np.float32), is_anomaly

    def predict_single(self, x: np.ndarray) -> tuple[float, bool]:
        """
        Convenience wrapper for a single feature vector.

        Parameters
        ----------
        x : np.ndarray, shape (n_features,) or (seq_len, n_features)

        Returns
        -------
        (anomaly_score: float, is_anomaly: bool)
        """
        if x.ndim == 1:
            x = x.reshape(1, -1)
        elif x.ndim == 2 and x.shape[0] == self.sequence_length:
            x = x.reshape(1, self.sequence_length, self.n_features)

        scores, flags = self.predict(x)
        return float(scores[0]), bool(flags[0])

    # ──────────────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────────────

    def save(self, path: Optional[str] = None) -> None:
        """Save Keras model to *.keras* format and threshold to a sidecar."""
        import json

        path = path or MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.model.save(path)

        # Save threshold as a simple JSON sidecar next to the model
        sidecar = path.replace(".keras", "_meta.json")
        meta = {
            "threshold":       self.threshold,
            "sequence_length": self.sequence_length,
            "n_features":      self.n_features,
            "lstm_units":      self.lstm_units,
        }
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)

        logger.info("LSTMDetector: saved to %s (threshold=%.6f)", path, self.threshold)

    def load(self, path: Optional[str] = None) -> "LSTMDetector":
        """
        Load a previously saved LSTM autoencoder from disk.

        Returns
        -------
        self (for chaining)
        """
        import json

        tf = self._get_tf()
        path = path or MODEL_PATH

        if not os.path.exists(path):
            raise FileNotFoundError(f"LSTM model not found at {path}")

        self.model = tf.keras.models.load_model(path)

        # Load sidecar metadata
        sidecar = path.replace(".keras", "_meta.json")
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            self.threshold       = meta.get("threshold", self.threshold)
            self.sequence_length = meta.get("sequence_length", self.sequence_length)
            self.n_features      = meta.get("n_features", self.n_features)
            self.lstm_units      = meta.get("lstm_units", self.lstm_units)

        logger.info(
            "LSTMDetector: loaded from %s (threshold=%.6f)", path, self.threshold
        )
        return self

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        """Return True if the model is loaded and ready for inference."""
        return self.model is not None

    def summary(self) -> None:
        """Print the Keras model summary."""
        if self.model is None:
            print("Model not built yet.")
        else:
            self.model.summary()

    def __repr__(self) -> str:
        status = "loaded" if self.is_ready() else "not loaded"
        return (
            f"LSTMDetector("
            f"seq_len={self.sequence_length}, "
            f"n_features={self.n_features}, "
            f"units={self.lstm_units}, "
            f"threshold={self.threshold:.6f}, "
            f"status={status})"
        )
