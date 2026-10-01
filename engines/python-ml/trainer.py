"""
trainer.py
==========
Complete training pipeline for the Browser Anomaly Detection ML Engine.

Responsibilities
----------------
* Generate synthetic normal and anomalous browser traffic feature vectors
* Train IsolationForest and LSTM autoencoder on normal traffic
* Evaluate both models on a labelled mixed test set
* Save models and scaler to saved_models/

Usage
-----
    python trainer.py              # runs full training + evaluation
    python trainer.py --quick      # fewer samples, for smoke-testing
"""

import argparse
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

# Ensure the project root is on sys.path when running as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_extractor import FeatureExtractor, N_FEATURES, FEATURE_NAMES
from models.isolation_forest import IsolationForestDetector
from models.lstm_model import LSTMDetector

# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("trainer")

SAVED_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_models")
os.makedirs(SAVED_MODELS_DIR, exist_ok=True)

RNG = np.random.default_rng(42)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic data generation – NORMAL traffic
# ──────────────────────────────────────────────────────────────────────────────

def generate_normal_traffic(n_samples: int = 5000) -> np.ndarray:
    """
    Generate synthetic feature vectors representing normal browser behaviour.

    Feature columns (N_FEATURES = 15):
     0  nav_frequency        2–8 / min
     1  xhr_count            5–30 per window
     2  unique_domains       3–10
     3  redirect_chain_len   0–1
     4  dom_mutation_rate    0–5 / sec
     5  session_duration_secs 30–3600 s
     6  avg_request_size     500–50 000 bytes
     7  error_4xx_rate       0–0.05
     8  cross_origin_ratio   0–0.3
     9  tab_switch_rate      0–2 / min
    10  request_burst_score  10–500 ms std-dev
    11  time_of_day_sin      [-1, 1] – realistic working hours bias
    12  time_of_day_cos      [-1, 1]
    13  unique_tabs          1–4
    14  error_5xx_rate       0–0.02

    Returns
    -------
    np.ndarray, shape (n_samples, 15), dtype float32
    """
    logger.info("Generating %d normal traffic samples …", n_samples)

    def rng_normal(low, high, size, noise_frac=0.1):
        """Uniform base with additive Gaussian noise."""
        base  = RNG.uniform(low, high, size=size)
        noise = RNG.normal(0, (high - low) * noise_frac, size=size)
        return np.clip(base + noise, 0, None).astype(np.float32)

    # Cyclical time: bias towards 8:00–18:00 working hours
    hours = RNG.normal(13.0, 3.0, size=n_samples) % 24
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    X = np.column_stack([
        rng_normal(2.0,  8.0,     n_samples),          #  0 nav_frequency
        rng_normal(5.0,  30.0,    n_samples),           #  1 xhr_count
        rng_normal(3.0,  10.0,    n_samples),           #  2 unique_domains
        RNG.integers(0, 2, size=n_samples).astype(np.float32),  # 3 redirect (0 or 1)
        rng_normal(0.0,  5.0,     n_samples),           #  4 dom_mutation_rate
        rng_normal(30.0, 3600.0,  n_samples),           #  5 session_duration_secs
        rng_normal(500,  50_000,  n_samples, 0.05),     #  6 avg_request_size
        rng_normal(0.0,  0.05,    n_samples, 0.02),     #  7 error_4xx_rate
        rng_normal(0.0,  0.30,    n_samples, 0.05),     #  8 cross_origin_ratio
        rng_normal(0.0,  2.0,     n_samples),           #  9 tab_switch_rate
        rng_normal(10.0, 500.0,   n_samples, 0.15),     # 10 request_burst_score
        tsin,                                            # 11 time_of_day_sin
        tcos,                                            # 12 time_of_day_cos
        rng_normal(1.0,  4.0,     n_samples),           # 13 unique_tabs
        rng_normal(0.0,  0.02,    n_samples, 0.01),     # 14 error_5xx_rate
    ]).astype(np.float32)

    logger.info("Normal traffic shape: %s", X.shape)
    return X


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic data generation – ANOMALOUS traffic
# ──────────────────────────────────────────────────────────────────────────────

def _anomaly_credential_harvesting(n: int) -> np.ndarray:
    """
    Credential harvesting: many POST requests, high unique domains,
    unusual timing (often late-night), lots of XHR.
    """
    hours = RNG.uniform(0, 6, size=n) % 24   # late-night
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    return np.column_stack([
        RNG.uniform(10, 30,       n),    # high nav_frequency
        RNG.uniform(80, 200,      n),    # high xhr_count
        RNG.uniform(25, 60,       n),    # many unique_domains
        RNG.integers(0, 2, n).astype(float),
        RNG.uniform(0, 3,         n),
        RNG.uniform(60, 600,      n),
        RNG.uniform(500, 5_000,   n),
        RNG.uniform(0.1, 0.4,     n),    # high 4xx (probing)
        RNG.uniform(0.6, 1.0,     n),    # very high cross-origin
        RNG.uniform(0, 1,         n),
        RNG.uniform(5, 50,        n),
        tsin, tcos,
        RNG.uniform(1, 3,         n),
        RNG.uniform(0, 0.05,      n),
    ]).astype(np.float32)


def _anomaly_session_hijacking(n: int) -> np.ndarray:
    """
    Session hijacking: rapid auth requests, unusual time pattern,
    high request burst due to credential replays.
    """
    hours = RNG.uniform(1, 5, size=n)
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    return np.column_stack([
        RNG.uniform(15, 40,       n),
        RNG.uniform(50, 150,      n),
        RNG.uniform(5, 20,        n),
        RNG.integers(0, 3, n).astype(float),
        RNG.uniform(0, 2,         n),
        RNG.uniform(5, 120,       n),    # short sessions (just hijacked)
        RNG.uniform(200, 3_000,   n),
        RNG.uniform(0.05, 0.3,    n),
        RNG.uniform(0.5, 1.0,     n),    # high cross-origin
        RNG.uniform(5, 20,        n),    # rapid tab switches
        RNG.uniform(500, 3_000,   n),    # very bursty requests
        tsin, tcos,
        RNG.uniform(3, 8,         n),
        RNG.uniform(0.02, 0.15,   n),
    ]).astype(np.float32)


def _anomaly_data_exfiltration(n: int) -> np.ndarray:
    """
    Data exfiltration: enormous outbound sizes, many unique domains,
    high XHR count, prolonged sessions.
    """
    hours = RNG.uniform(0, 24, size=n)
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    return np.column_stack([
        RNG.uniform(1, 5,         n),    # low nav (silent exfil)
        RNG.uniform(100, 500,     n),    # very high xhr_count
        RNG.uniform(20, 80,       n),    # many unique domains
        RNG.integers(0, 2, n).astype(float),
        RNG.uniform(0, 1,         n),
        RNG.uniform(600, 7200,    n),    # long sessions
        RNG.uniform(500_000, 10_000_000, n),  # HUGE response sizes
        RNG.uniform(0, 0.03,      n),
        RNG.uniform(0.7, 1.0,     n),    # very high cross-origin
        RNG.uniform(0, 0.5,       n),
        RNG.uniform(100, 1000,    n),
        tsin, tcos,
        RNG.uniform(1, 2,         n),
        RNG.uniform(0, 0.02,      n),
    ]).astype(np.float32)


def _anomaly_malicious_redirect(n: int) -> np.ndarray:
    """
    Malicious redirect chains: deep redirect depth, rapid tab switches,
    fast navigation bursts.
    """
    hours = RNG.uniform(0, 24, size=n)
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    return np.column_stack([
        RNG.uniform(20, 60,       n),    # high nav_frequency
        RNG.uniform(10, 60,       n),
        RNG.uniform(5, 25,        n),
        RNG.uniform(5, 15,        n),    # deep redirect chains
        RNG.uniform(0, 3,         n),
        RNG.uniform(10, 300,      n),
        RNG.uniform(500, 20_000,  n),
        RNG.uniform(0.1, 0.5,     n),
        RNG.uniform(0.4, 0.9,     n),
        RNG.uniform(10, 50,       n),    # very rapid tab switches
        RNG.uniform(50, 500,      n),
        tsin, tcos,
        RNG.uniform(3, 10,        n),
        RNG.uniform(0.02, 0.1,    n),
    ]).astype(np.float32)


def _anomaly_dom_injection(n: int) -> np.ndarray:
    """
    DOM injection / XSS: extreme DOM mutation rate, many cross-origin
    scripts loaded, moderate XHR.
    """
    hours = RNG.uniform(0, 24, size=n)
    tsin  = np.sin(2 * np.pi * hours / 24).astype(np.float32)
    tcos  = np.cos(2 * np.pi * hours / 24).astype(np.float32)

    return np.column_stack([
        RNG.uniform(2, 15,        n),
        RNG.uniform(30, 100,      n),
        RNG.uniform(10, 40,       n),
        RNG.integers(0, 3, n).astype(float),
        RNG.uniform(50, 500,      n),    # EXTREME dom_mutation_rate
        RNG.uniform(30, 3600,     n),
        RNG.uniform(1_000, 100_000, n),
        RNG.uniform(0, 0.1,       n),
        RNG.uniform(0.6, 1.0,     n),    # very high cross-origin
        RNG.uniform(0, 3,         n),
        RNG.uniform(10, 300,      n),
        tsin, tcos,
        RNG.uniform(1, 5,         n),
        RNG.uniform(0, 0.05,      n),
    ]).astype(np.float32)


def generate_anomalous_traffic(n_samples: int = 500) -> np.ndarray:
    """
    Generate synthetic anomalous browser traffic covering 5 attack scenarios.

    Scenarios
    ---------
    1. credential_harvesting
    2. session_hijacking
    3. data_exfiltration
    4. malicious_redirect
    5. dom_injection

    Returns
    -------
    np.ndarray, shape (n_samples, 15), dtype float32
    """
    logger.info("Generating %d anomalous traffic samples …", n_samples)
    per_class = n_samples // 5
    remainder = n_samples - per_class * 5

    generators = [
        _anomaly_credential_harvesting,
        _anomaly_session_hijacking,
        _anomaly_data_exfiltration,
        _anomaly_malicious_redirect,
        _anomaly_dom_injection,
    ]

    chunks = []
    for i, gen in enumerate(generators):
        count = per_class + (1 if i < remainder else 0)
        chunks.append(gen(count))

    X_anom = np.vstack(chunks).astype(np.float32)
    # Shuffle so class order is not preserved
    perm = RNG.permutation(X_anom.shape[0])
    X_anom = X_anom[perm]
    logger.info("Anomalous traffic shape: %s", X_anom.shape)
    return X_anom


# ──────────────────────────────────────────────────────────────────────────────
# Training pipeline
# ──────────────────────────────────────────────────────────────────────────────

def train_all(
    n_normal: int = 5000,
    n_anomaly: int = 500,
) -> tuple[IsolationForestDetector, LSTMDetector, FeatureExtractor]:
    """
    Full training pipeline.

    1. Generate synthetic normal + anomalous data
    2. Fit FeatureExtractor scaler on normal data
    3. Train IsolationForest on scaled normal data
    4. Train LSTM autoencoder on scaled normal data
    5. Return trained objects

    Returns
    -------
    (IsolationForestDetector, LSTMDetector, FeatureExtractor)
    """
    logger.info("═══ Starting full training pipeline ═══")
    t0 = time.perf_counter()

    # 1. Data
    X_normal = generate_normal_traffic(n_normal)
    X_anom   = generate_anomalous_traffic(n_anomaly)

    # 2. Fit scaler on normal data and save
    fe = FeatureExtractor()
    fe.fit_scaler(X_normal)
    X_normal_scaled = fe.transform(X_normal)
    X_anom_scaled   = fe.transform(X_anom)

    # 3. Isolation Forest
    logger.info("─── Training Isolation Forest ───")
    if_det = IsolationForestDetector(n_estimators=200, contamination=0.05, random_state=42)
    if_det.train(X_normal_scaled)

    # 4. LSTM Autoencoder
    logger.info("─── Training LSTM Autoencoder ───")
    lstm_det = LSTMDetector(sequence_length=20, n_features=N_FEATURES, lstm_units=64)
    lstm_det.train(X_normal_scaled, epochs=50, batch_size=32, verbose=1)

    elapsed = time.perf_counter() - t0
    logger.info("═══ Training complete in %.1f seconds ═══", elapsed)
    return if_det, lstm_det, fe


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation
# ──────────────────────────────────────────────────────────────────────────────

def evaluate(
    if_det: IsolationForestDetector,
    lstm_det: LSTMDetector,
    fe: FeatureExtractor,
    n_normal_test: int = 1000,
    n_anom_test: int = 200,
) -> dict:
    """
    Evaluate both models on a held-out labelled test set.

    Generates fresh test data that was NOT used during training.

    Returns
    -------
    dict with precision, recall, f1, auc for each model and ensemble.
    """
    logger.info("─── Generating evaluation test set ───")
    # Use a different seed to avoid overlap with training data
    rng_backup = RNG.bit_generator.state
    test_rng = np.random.default_rng(seed=9999)

    # Generate test data with fresh RNG
    X_test_normal = _gen_with_rng(generate_normal_traffic, n_normal_test, test_rng)
    X_test_anom   = _gen_with_rng(generate_anomalous_traffic, n_anom_test, test_rng)

    X_test = np.vstack([X_test_normal, X_test_anom])
    y_true = np.array(
        [0] * n_normal_test + [1] * n_anom_test, dtype=np.int32
    )

    # Scale
    X_test_scaled = fe.transform(X_test)

    # Isolation Forest
    if_scores, if_preds = if_det.predict(X_test_scaled)

    # LSTM
    lstm_scores, lstm_preds = lstm_det.predict(X_test_scaled)

    # Ensemble (equal weighting)
    combined_scores = 0.5 * if_scores + 0.5 * lstm_scores
    ensemble_preds  = combined_scores >= 0.5

    results = {}
    for name, preds, scores in [
        ("IsolationForest", if_preds.astype(int), if_scores),
        ("LSTM",            lstm_preds.astype(int), lstm_scores),
        ("Ensemble",        ensemble_preds.astype(int), combined_scores),
    ]:
        p  = precision_score(y_true, preds, zero_division=0)
        r  = recall_score(y_true, preds, zero_division=0)
        f1 = f1_score(y_true, preds, zero_division=0)
        try:
            auc = roc_auc_score(y_true, scores)
        except ValueError:
            auc = float("nan")

        results[name] = {"precision": p, "recall": r, "f1": f1, "auc": auc}
        cm = confusion_matrix(y_true, preds)

        logger.info(
            "%-20s  P=%.3f  R=%.3f  F1=%.3f  AUC=%.3f",
            name, p, r, f1, auc,
        )
        print(f"\n{'═'*60}")
        print(f"  {name} Evaluation")
        print(f"{'═'*60}")
        print(f"  Precision : {p:.4f}")
        print(f"  Recall    : {r:.4f}")
        print(f"  F1-Score  : {f1:.4f}")
        print(f"  ROC-AUC   : {auc:.4f}")
        print(f"  Confusion matrix:\n{cm}")
        print(classification_report(y_true, preds, target_names=["normal", "anomaly"]))

    return results


def _gen_with_rng(gen_fn, n, override_rng):
    """Re-seed the global RNG and call gen_fn(n)."""
    global RNG
    saved = RNG
    RNG = override_rng
    X = gen_fn(n)
    RNG = saved
    return X


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Train the Browser Anomaly Detection ML models")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use small dataset for smoke-testing (1000 normal / 100 anomaly)",
    )
    parser.add_argument(
        "--no-lstm",
        action="store_true",
        help="Skip LSTM training (faster, only trains IsolationForest)",
    )
    args = parser.parse_args()

    n_normal = 1000 if args.quick else 5000
    n_anom   = 100  if args.quick else 500

    logger.info("Training config: n_normal=%d  n_anomaly=%d  lstm=%s",
                n_normal, n_anom, not args.no_lstm)

    if args.no_lstm:
        # Train only IF
        X_normal = generate_normal_traffic(n_normal)
        fe = FeatureExtractor()
        fe.fit_scaler(X_normal)
        X_scaled = fe.transform(X_normal)
        if_det = IsolationForestDetector(n_estimators=200, contamination=0.05)
        if_det.train(X_scaled)
        lstm_det = None
    else:
        if_det, lstm_det, fe = train_all(n_normal=n_normal, n_anomaly=n_anom)

    logger.info("All models saved to: %s", SAVED_MODELS_DIR)

    if lstm_det is not None:
        print("\n" + "═" * 60)
        print("  EVALUATION REPORT")
        print("═" * 60)
        evaluate(if_det, lstm_det, fe, n_normal_test=500, n_anom_test=100)

    logger.info("Training pipeline finished successfully.")


if __name__ == "__main__":
    main()
