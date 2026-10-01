"""
main.py
=======
Real-time anomaly detection engine for browser telemetry.

This service:
  1. Connects to Redis and joins the `ml-engine` consumer group on the
     `raw:telemetry` stream.
  2. Reads batches of events using XREADGROUP (blocking long-poll).
  3. Groups events by session_id and extracts features when a session
     has accumulated enough events (>= MIN_SESSION_EVENTS).
  4. Scores each session with IsolationForest + LSTM autoencoder.
  5. Publishes results to the `ml:results` Redis stream.
  6. Acknowledges consumed messages with XACK.
  7. Handles Redis disconnects with exponential back-off reconnect.
  8. Gracefully shuts down on SIGINT / SIGTERM.

Redis Streams
-------------
  INPUT:  raw:telemetry   (produced by the Rails/Node telemetry pipeline)
  OUTPUT: ml:results      (consumed by the alerts aggregator)

Environment variables (all optional, fall back to defaults)
-----------------------------------------------------------
  REDIS_HOST         (default: localhost)
  REDIS_PORT         (default: 6379)
  REDIS_PASSWORD     (default: "")
  ML_BATCH_SIZE      (default: 10)
  ML_BLOCK_MS        (default: 1000)   blocking read timeout
  MIN_SESSION_EVENTS (default: 5)
  TRAIN_ON_STARTUP   (default: "false") – set "true" to train fresh models
"""

import json
import logging
import math
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import numpy as np
import redis

# ── Project imports ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_extractor import FeatureExtractor, N_FEATURES
from models.isolation_forest import IsolationForestDetector
from models.lstm_model import LSTMDetector

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("ml-engine")

# ──────────────────────────────────────────────────────────────────────────────
# Configuration (from environment variables with sensible defaults)
# ──────────────────────────────────────────────────────────────────────────────
REDIS_HOST         = os.environ.get("REDIS_HOST",        "localhost")
REDIS_PORT         = int(os.environ.get("REDIS_PORT",    "6379"))
REDIS_PASSWORD     = os.environ.get("REDIS_PASSWORD",   "") or None
REDIS_DB           = int(os.environ.get("REDIS_DB",      "0"))

STREAM_INPUT       = "raw:telemetry"
STREAM_OUTPUT      = "ml:results"
CONSUMER_GROUP     = "ml-engine"
CONSUMER_NAME      = "ml-worker-1"

ML_BATCH_SIZE      = int(os.environ.get("ML_BATCH_SIZE",      "10"))
ML_BLOCK_MS        = int(os.environ.get("ML_BLOCK_MS",        "1000"))
MIN_SESSION_EVENTS = int(os.environ.get("MIN_SESSION_EVENTS", "5"))
TRAIN_ON_STARTUP   = os.environ.get("TRAIN_ON_STARTUP", "false").lower() == "true"

# Back-off settings for reconnect
RECONNECT_BASE_DELAY  = 1.0   # seconds
RECONNECT_MAX_DELAY   = 60.0  # seconds
RECONNECT_MULTIPLIER  = 2.0

# ──────────────────────────────────────────────────────────────────────────────
# Global state / graceful shutdown
# ──────────────────────────────────────────────────────────────────────────────
_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Signal %d received – initiating graceful shutdown …", signum)
    _shutdown_requested = True


signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ──────────────────────────────────────────────────────────────────────────────
# Model loading / training
# ──────────────────────────────────────────────────────────────────────────────

class ModelBundle:
    """
    Holds references to all loaded ML components and exposes a unified
    `score_session()` method for real-time inference.
    """

    def __init__(self):
        self.fe: FeatureExtractor | None           = None
        self.if_det: IsolationForestDetector | None = None
        self.lstm_det: LSTMDetector | None          = None
        self._ready = False

    def load_or_train(self, force_train: bool = False) -> None:
        """
        Attempt to load pre-trained models from disk.
        Falls back to running the full training pipeline if models are missing
        or *force_train* is True.
        """
        from models.isolation_forest import MODEL_PATH as IF_PATH
        from models.lstm_model import MODEL_PATH as LSTM_PATH

        self.fe = FeatureExtractor()  # loads scaler if available

        models_exist = (
            os.path.exists(IF_PATH) and
            os.path.exists(LSTM_PATH)
        )

        if force_train or not models_exist:
            logger.info("Training models from scratch …")
            self._run_training()
        else:
            logger.info("Loading pre-trained models …")
            self._load_models(IF_PATH, LSTM_PATH)

        self._ready = True
        logger.info("ModelBundle ready – IF=%s  LSTM=%s", self.if_det, self.lstm_det)

    def _run_training(self) -> None:
        """Import trainer and run the full training pipeline."""
        from trainer import train_all
        self.if_det, self.lstm_det, self.fe = train_all(n_normal=5000, n_anomaly=500)

    def _load_models(self, if_path: str, lstm_path: str) -> None:
        """Load both models from their persisted files."""
        self.if_det = IsolationForestDetector()
        self.if_det.load(if_path)

        self.lstm_det = LSTMDetector()
        self.lstm_det.load(lstm_path)

    def is_ready(self) -> bool:
        return self._ready and self.if_det is not None

    def score_session(
        self,
        events: list[dict],
    ) -> dict[str, Any]:
        """
        Extract features and score a single session's event window.

        Parameters
        ----------
        events : list of raw telemetry event dicts (min MIN_SESSION_EVENTS)

        Returns
        -------
        dict with keys:
            if_score, lstm_score, ml_score (combined),
            is_anomaly (bool), features_json (str)
        """
        if not self._ready:
            raise RuntimeError("ModelBundle not initialised")

        # ── Feature extraction ────────────────────────────────────────────
        feat_vec = self.fe.extract_single_window(events, apply_scaler=True)  # (15,)

        # ── IsolationForest ───────────────────────────────────────────────
        if self.if_det and self.if_det.is_ready():
            if_score, if_anom = self.if_det.predict_single(feat_vec)
        else:
            if_score, if_anom = 0.0, False

        # ── LSTM ──────────────────────────────────────────────────────────
        if self.lstm_det and self.lstm_det.is_ready():
            lstm_score, lstm_anom = self.lstm_det.predict_single(feat_vec)
        else:
            lstm_score, lstm_anom = 0.0, False

        # ── Ensemble ──────────────────────────────────────────────────────
        ml_score   = 0.5 * if_score + 0.5 * lstm_score
        is_anomaly = bool(if_anom or lstm_anom or ml_score >= 0.5)

        # Serialise features for downstream consumers
        feat_dict    = FeatureExtractor.feature_vector_to_dict(feat_vec)
        features_json = json.dumps(feat_dict)

        return {
            "if_score":      round(float(if_score),   6),
            "lstm_score":    round(float(lstm_score),  6),
            "ml_score":      round(float(ml_score),    6),
            "is_anomaly":    is_anomaly,
            "features_json": features_json,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Redis helpers
# ──────────────────────────────────────────────────────────────────────────────

def _connect_redis() -> redis.Redis:
    """Create and return an authenticated Redis client."""
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    client.ping()
    logger.info("Connected to Redis at %s:%d", REDIS_HOST, REDIS_PORT)
    return client


def _ensure_consumer_group(client: redis.Redis) -> None:
    """
    Create the consumer group if it does not already exist.
    Uses MKSTREAM so the stream itself is also created if absent.
    """
    try:
        client.xgroup_create(
            STREAM_INPUT,
            CONSUMER_GROUP,
            id="$",      # only process new messages
            mkstream=True,
        )
        logger.info(
            "Consumer group '%s' created on stream '%s'",
            CONSUMER_GROUP,
            STREAM_INPUT,
        )
    except redis.exceptions.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.info(
                "Consumer group '%s' already exists – continuing",
                CONSUMER_GROUP,
            )
        else:
            raise


def _decode_event(raw_fields: dict) -> dict:
    """
    Parse a raw Redis stream message into a structured event dict.

    The pipeline may publish events as either:
      - A flat key-value map (field per attribute)
      - A single `data` or `payload` key containing JSON
    """
    # Try to find a JSON payload field first
    for key in ("data", "payload", "event", "body"):
        if key in raw_fields:
            try:
                parsed = json.loads(raw_fields[key])
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass

    # Fall back to using the fields dict directly
    event = dict(raw_fields)

    # Attempt to coerce numeric strings for common fields
    for numeric_key in (
        "status_code", "response_size", "redirect_depth",
        "dom_mutations", "timestamp", "ts",
    ):
        if numeric_key in event:
            try:
                event[numeric_key] = float(event[numeric_key])
            except (ValueError, TypeError):
                pass

    for bool_key in ("cross_origin", "is_cross_origin"):
        if bool_key in event:
            event[bool_key] = str(event[bool_key]).lower() in ("true", "1", "yes")

    return event


def _publish_result(
    client: redis.Redis,
    session_id: str,
    event_id: str,
    scores: dict[str, Any],
) -> None:
    """Publish ML scoring result to the ml:results stream."""
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    fields = {
        "session_id":    session_id,
        "event_id":      event_id,
        "ml_score":      str(scores["ml_score"]),
        "if_score":      str(scores["if_score"]),
        "lstm_score":    str(scores["lstm_score"]),
        "is_anomaly":    "true" if scores["is_anomaly"] else "false",
        "features_json": scores["features_json"],
        "timestamp":     now_iso,
        "source":        "python-ml-engine",
    }
    client.xadd(STREAM_OUTPUT, fields, maxlen=10_000, approximate=True)


# ──────────────────────────────────────────────────────────────────────────────
# Session event buffer (in-process, keyed by session_id)
# ──────────────────────────────────────────────────────────────────────────────

class SessionBuffer:
    """
    Lightweight in-process buffer that accumulates events per session_id.

    Events older than MAX_AGE_SECONDS are evicted to avoid unbounded growth
    for abandoned sessions.
    """

    MAX_AGE_SECONDS = 3600  # 1 hour

    def __init__(self):
        self._store: dict[str, list[dict]] = defaultdict(list)
        self._last_seen: dict[str, float]  = {}

    def add(self, session_id: str, event: dict) -> None:
        self._store[session_id].append(event)
        self._last_seen[session_id] = time.monotonic()

    def get(self, session_id: str) -> list[dict]:
        return self._store.get(session_id, [])

    def size(self, session_id: str) -> int:
        return len(self._store.get(session_id, []))

    def evict_stale(self) -> int:
        """Remove sessions not updated in the last MAX_AGE_SECONDS."""
        cutoff   = time.monotonic() - self.MAX_AGE_SECONDS
        stale    = [sid for sid, t in self._last_seen.items() if t < cutoff]
        for sid in stale:
            del self._store[sid]
            del self._last_seen[sid]
        if stale:
            logger.debug("Evicted %d stale sessions from buffer", len(stale))
        return len(stale)

    def clear_session(self, session_id: str) -> None:
        """Remove all buffered events for a session (after scoring)."""
        self._store.pop(session_id, None)
        self._last_seen.pop(session_id, None)


# ──────────────────────────────────────────────────────────────────────────────
# Main processing loop
# ──────────────────────────────────────────────────────────────────────────────

def _process_batch(
    client: redis.Redis,
    messages: list,
    session_buffer: SessionBuffer,
    model_bundle: ModelBundle,
    stats: dict,
) -> None:
    """
    Process a batch of Redis stream messages:
      - Decode each message
      - Buffer by session_id
      - Score sessions with >= MIN_SESSION_EVENTS buffered events
      - Publish results and ACK messages
    """
    # Collect message IDs so we can ACK them regardless of scoring outcome
    msg_ids   = []
    last_msg_id_per_session: dict[str, str] = {}

    for stream_name, stream_messages in messages:
        for msg_id, fields in stream_messages:
            msg_ids.append(msg_id)
            stats["messages_consumed"] += 1

            event = _decode_event(fields)
            session_id = (
                str(event.get("session_id") or event.get("session") or "unknown")
            )
            event["_redis_msg_id"] = msg_id

            session_buffer.add(session_id, event)
            last_msg_id_per_session[session_id] = msg_id

    # Score sessions with enough data
    for session_id, latest_msg_id in last_msg_id_per_session.items():
        buf_size = session_buffer.size(session_id)
        if buf_size < MIN_SESSION_EVENTS:
            continue

        events = session_buffer.get(session_id)
        try:
            scores = model_bundle.score_session(events)
            _publish_result(client, session_id, latest_msg_id, scores)
            stats["sessions_scored"] += 1

            if scores["is_anomaly"]:
                stats["anomalies_detected"] += 1
                logger.warning(
                    "ANOMALY detected – session=%s  ml_score=%.4f  "
                    "if=%.4f  lstm=%.4f",
                    session_id,
                    scores["ml_score"],
                    scores["if_score"],
                    scores["lstm_score"],
                )
            else:
                logger.debug(
                    "Normal – session=%s  ml_score=%.4f",
                    session_id,
                    scores["ml_score"],
                )

            # Keep a sliding buffer (don't clear entirely so context is preserved)
            # Only keep the last 20 events to avoid unbounded growth
            if buf_size > 40:
                session_buffer._store[session_id] = events[-20:]

        except Exception as exc:
            logger.error(
                "Scoring failed for session=%s: %s", session_id, exc, exc_info=True
            )
            stats["scoring_errors"] += 1

    # ACK all consumed messages
    if msg_ids:
        client.xack(STREAM_INPUT, CONSUMER_GROUP, *msg_ids)
        stats["messages_acked"] += len(msg_ids)


def _log_stats(stats: dict, interval: float) -> None:
    """Emit a periodic statistics log line."""
    rate = stats.get("messages_consumed", 0) / max(interval, 1)
    logger.info(
        "Stats │ consumed=%d  scored=%d  anomalies=%d  errors=%d  rate=%.1f msg/s",
        stats["messages_consumed"],
        stats["sessions_scored"],
        stats["anomalies_detected"],
        stats["scoring_errors"],
        rate,
    )


def run_consumer_loop(model_bundle: ModelBundle) -> None:
    """
    Main blocking consumer loop.

    Maintains a persistent Redis connection with automatic reconnection
    using exponential back-off on failure.
    """
    session_buffer = SessionBuffer()
    stats = defaultdict(int)
    reconnect_delay = RECONNECT_BASE_DELAY
    last_stat_time  = time.monotonic()
    stat_interval   = 60.0  # log stats every 60 s
    evict_counter   = 0

    client: redis.Redis | None = None

    while not _shutdown_requested:
        # ── (Re)connect ───────────────────────────────────────────────────
        if client is None:
            try:
                client = _connect_redis()
                _ensure_consumer_group(client)
                reconnect_delay = RECONNECT_BASE_DELAY  # reset on success
            except Exception as exc:
                logger.error(
                    "Redis connection failed: %s – retrying in %.1fs",
                    exc,
                    reconnect_delay,
                )
                time.sleep(reconnect_delay)
                reconnect_delay = min(
                    reconnect_delay * RECONNECT_MULTIPLIER,
                    RECONNECT_MAX_DELAY,
                )
                client = None
                continue

        # ── Read batch ────────────────────────────────────────────────────
        try:
            messages = client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_INPUT: ">"},
                count=ML_BATCH_SIZE,
                block=ML_BLOCK_MS,
            )
        except redis.exceptions.ConnectionError as exc:
            logger.warning("Redis connection lost: %s – will reconnect", exc)
            client = None
            continue
        except redis.exceptions.TimeoutError:
            # Blocking read timed out – normal, just loop
            messages = None
        except Exception as exc:
            logger.error("Unexpected Redis error: %s", exc, exc_info=True)
            time.sleep(1)
            continue

        # ── Process messages ──────────────────────────────────────────────
        if messages:
            try:
                _process_batch(client, messages, session_buffer, model_bundle, stats)
            except Exception as exc:
                logger.error("Batch processing error: %s", exc, exc_info=True)

        # ── Periodic maintenance ──────────────────────────────────────────
        now = time.monotonic()
        if now - last_stat_time >= stat_interval:
            _log_stats(stats, stat_interval)
            last_stat_time = now
            # Reset counters for next window
            for k in stats:
                stats[k] = 0

        evict_counter += 1
        if evict_counter >= 1000:
            session_buffer.evict_stale()
            evict_counter = 0

    logger.info("Consumer loop exited cleanly.")


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("═══ Browser Anomaly Detection – Python ML Engine starting ═══")
    logger.info(
        "Config │ Redis=%s:%d  stream=%s  group=%s  consumer=%s  batch=%d  block=%dms",
        REDIS_HOST, REDIS_PORT,
        STREAM_INPUT, CONSUMER_GROUP, CONSUMER_NAME,
        ML_BATCH_SIZE, ML_BLOCK_MS,
    )

    # Load / train models
    bundle = ModelBundle()
    try:
        bundle.load_or_train(force_train=TRAIN_ON_STARTUP)
    except Exception as exc:
        logger.critical("Failed to initialise models: %s", exc, exc_info=True)
        sys.exit(1)

    logger.info("Models loaded – starting consumer loop …")

    run_consumer_loop(bundle)

    logger.info("═══ ML Engine shutdown complete ═══")


if __name__ == "__main__":
    main()
