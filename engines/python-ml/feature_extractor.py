"""
feature_extractor.py
====================
Converts raw telemetry events from Redis streams into numerical feature
vectors suitable for anomaly detection models.

Feature vector (15 dimensions) per window:
  0  nav_frequency        – navigations per minute
  1  xhr_count            – total XHR/fetch requests in window
  2  unique_domains       – count of unique domains contacted
  3  redirect_chain_len   – max redirect chain depth seen
  4  dom_mutation_rate    – DOM mutations per second
  5  session_duration_secs– time since session start (seconds)
  6  avg_request_size     – average XHR response size in bytes
  7  error_4xx_rate       – ratio of 4xx responses to total requests
  8  cross_origin_ratio   – ratio of cross-origin requests
  9  tab_switch_rate      – tab switches per minute
  10 request_burst_score  – std dev of request inter-arrival times (ms)
  11 time_of_day_sin      – sin encoding of hour (cyclical)
  12 time_of_day_cos      – cos encoding of hour (cyclical)
  13 unique_tabs          – number of unique tabs in window
  14 error_5xx_rate       – ratio of 5xx responses to total requests
"""

import math
import logging
import os
from datetime import datetime, timezone
from typing import Any

import numpy as np
import joblib
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
N_FEATURES      = 15
WINDOW_SIZE     = 20   # events per window
STRIDE          = 5    # stride between windows
SCALER_PATH     = os.path.join(os.path.dirname(__file__), "saved_models", "scaler.pkl")

FEATURE_NAMES = [
    "nav_frequency",
    "xhr_count",
    "unique_domains",
    "redirect_chain_len",
    "dom_mutation_rate",
    "session_duration_secs",
    "avg_request_size",
    "error_4xx_rate",
    "cross_origin_ratio",
    "tab_switch_rate",
    "request_burst_score",
    "time_of_day_sin",
    "time_of_day_cos",
    "unique_tabs",
    "error_5xx_rate",
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert a value to float, returning *default* on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _epoch_ms(event: dict) -> float:
    """Return the event timestamp as epoch milliseconds."""
    ts = event.get("timestamp") or event.get("ts") or event.get("created_at")
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        # Could be seconds or ms – treat >1e12 as ms, else seconds
        return float(ts) if float(ts) > 1e12 else float(ts) * 1000
    # ISO-8601 string
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.timestamp() * 1000
    except (ValueError, TypeError):
        return 0.0


def _extract_window_features(events: list[dict]) -> np.ndarray:
    """
    Extract the 15-dimensional feature vector from a list of event dicts.

    This function is intentionally defensive: missing / malformed fields
    fall back to zero so a single bad event never breaks inference.
    """
    if not events:
        return np.zeros(N_FEATURES, dtype=np.float32)

    # ── Timing baseline ──────────────────────────────────────────────────────
    timestamps_ms = [_epoch_ms(e) for e in events]
    valid_ts = [t for t in timestamps_ms if t > 0]
    if len(valid_ts) >= 2:
        window_start_ms  = min(valid_ts)
        window_end_ms    = max(valid_ts)
        window_duration_s = max((window_end_ms - window_start_ms) / 1000, 1e-6)
        window_duration_min = window_duration_s / 60.0
    else:
        window_start_ms  = 0.0
        window_end_ms    = 0.0
        window_duration_s = 1.0
        window_duration_min = 1.0 / 60.0

    # Hour-of-day for cyclical encoding (use first valid timestamp)
    hour_of_day = 12.0  # neutral default
    if valid_ts:
        try:
            dt = datetime.fromtimestamp(valid_ts[0] / 1000, tz=timezone.utc)
            hour_of_day = dt.hour + dt.minute / 60.0
        except (OSError, OverflowError, ValueError):
            pass

    # ── Per-event field extraction ───────────────────────────────────────────
    nav_count          = 0
    xhr_count          = 0
    domains: set       = set()
    max_redirect_depth = 0
    dom_mutations      = 0
    request_sizes: list[float] = []
    status_4xx         = 0
    status_5xx         = 0
    total_requests     = 0
    cross_origin_count = 0
    tab_switches       = 0
    tabs: set          = set()
    session_start_ms   = None
    request_ts_ms: list[float] = []

    for ev in events:
        event_type = str(ev.get("event_type") or ev.get("type") or "")

        # Navigation events
        if event_type in ("navigation", "navigate", "pageview", "page_view"):
            nav_count += 1

        # XHR / fetch requests
        if event_type in ("xhr", "fetch", "request", "http_request", "api_call"):
            xhr_count += 1
            total_requests += 1

            url = str(ev.get("url") or ev.get("request_url") or "")
            if url:
                try:
                    from urllib.parse import urlparse
                    host = urlparse(url).netloc
                    if host:
                        domains.add(host)
                except Exception:
                    pass

            size = _safe_float(ev.get("response_size") or ev.get("size") or ev.get("bytes"))
            if size > 0:
                request_sizes.append(size)

            status = int(_safe_float(ev.get("status_code") or ev.get("status") or 0))
            if 400 <= status < 500:
                status_4xx += 1
            elif 500 <= status < 600:
                status_5xx += 1

            cross_origin = ev.get("cross_origin") or ev.get("is_cross_origin")
            if cross_origin is True or str(cross_origin).lower() in ("true", "1", "yes"):
                cross_origin_count += 1

            ts = _epoch_ms(ev)
            if ts > 0:
                request_ts_ms.append(ts)

        # Redirect
        redirect_depth = int(_safe_float(ev.get("redirect_depth") or ev.get("redirect_count") or 0))
        max_redirect_depth = max(max_redirect_depth, redirect_depth)

        # DOM mutations
        dom_mutations += int(_safe_float(ev.get("dom_mutations") or ev.get("mutations") or 0))

        # Tab events
        if event_type in ("tab_switch", "tab_change", "tab_focus", "visibility_change"):
            tab_switches += 1

        tab_id = ev.get("tab_id") or ev.get("tab")
        if tab_id is not None:
            tabs.add(str(tab_id))

        # Session start
        s_start = ev.get("session_start") or ev.get("session_started_at")
        if s_start is not None:
            candidate = _epoch_ms({"timestamp": s_start})
            if candidate > 0 and (session_start_ms is None or candidate < session_start_ms):
                session_start_ms = candidate

    # ── Derived features ─────────────────────────────────────────────────────

    # 0  nav_frequency (navigations / min)
    nav_frequency = nav_count / max(window_duration_min, 1e-9)

    # 1  xhr_count (raw count in window)
    f_xhr_count = float(xhr_count)

    # 2  unique_domains
    unique_domains = float(len(domains))

    # 3  redirect_chain_len
    f_redirect = float(max_redirect_depth)

    # 4  dom_mutation_rate (mutations / sec)
    dom_mutation_rate = dom_mutations / max(window_duration_s, 1e-9)

    # 5  session_duration_secs
    if session_start_ms and window_end_ms > 0:
        session_duration_secs = max((window_end_ms - session_start_ms) / 1000, 0.0)
    else:
        session_duration_secs = window_duration_s

    # 6  avg_request_size (bytes)
    avg_request_size = float(np.mean(request_sizes)) if request_sizes else 0.0

    # 7  error_4xx_rate
    error_4xx_rate = status_4xx / max(total_requests, 1)

    # 8  cross_origin_ratio
    cross_origin_ratio = cross_origin_count / max(total_requests, 1)

    # 9  tab_switch_rate (switches / min)
    tab_switch_rate = tab_switches / max(window_duration_min, 1e-9)

    # 10 request_burst_score (std dev of inter-arrival times in ms)
    if len(request_ts_ms) >= 2:
        sorted_ts = sorted(request_ts_ms)
        inter_arrivals = np.diff(sorted_ts)
        request_burst_score = float(np.std(inter_arrivals))
    else:
        request_burst_score = 0.0

    # 11 time_of_day_sin
    time_of_day_sin = math.sin(2 * math.pi * hour_of_day / 24.0)

    # 12 time_of_day_cos
    time_of_day_cos = math.cos(2 * math.pi * hour_of_day / 24.0)

    # 13 unique_tabs
    unique_tabs = float(len(tabs)) if tabs else 1.0

    # 14 error_5xx_rate
    error_5xx_rate = status_5xx / max(total_requests, 1)

    feature_vec = np.array([
        nav_frequency,
        f_xhr_count,
        unique_domains,
        f_redirect,
        dom_mutation_rate,
        session_duration_secs,
        avg_request_size,
        error_4xx_rate,
        cross_origin_ratio,
        tab_switch_rate,
        request_burst_score,
        time_of_day_sin,
        time_of_day_cos,
        unique_tabs,
        error_5xx_rate,
    ], dtype=np.float32)

    # Replace any NaN / Inf that slipped through
    feature_vec = np.nan_to_num(feature_vec, nan=0.0, posinf=1e6, neginf=-1e6)
    return feature_vec


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

class FeatureExtractor:
    """
    Converts raw telemetry event lists into normalised feature matrices.

    Usage
    -----
    >>> fe = FeatureExtractor()
    >>> X = fe.extract_windows(events)        # (n_windows, 15)
    >>> x = fe.extract_single_window(events)  # (15,) for real-time
    """

    def __init__(self, window_size: int = WINDOW_SIZE, stride: int = STRIDE):
        self.window_size = window_size
        self.stride      = stride
        self.scaler: StandardScaler | None = None
        self._load_scaler()

    # ── Scaler persistence ───────────────────────────────────────────────────

    def _load_scaler(self) -> None:
        """Load a previously fitted scaler from disk if it exists."""
        if os.path.exists(SCALER_PATH):
            try:
                self.scaler = joblib.load(SCALER_PATH)
                logger.info("FeatureExtractor: scaler loaded from %s", SCALER_PATH)
            except Exception as exc:
                logger.warning("FeatureExtractor: could not load scaler (%s) – will use raw features", exc)
                self.scaler = None

    def fit_scaler(self, X: np.ndarray) -> "FeatureExtractor":
        """
        Fit a StandardScaler on the provided feature matrix and persist it.

        Parameters
        ----------
        X : np.ndarray, shape (n_samples, 15)
        """
        self.scaler = StandardScaler()
        self.scaler.fit(X)
        os.makedirs(os.path.dirname(SCALER_PATH), exist_ok=True)
        joblib.dump(self.scaler, SCALER_PATH)
        logger.info("FeatureExtractor: scaler fitted and saved to %s", SCALER_PATH)
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Apply fitted scaler; if none loaded, return X unchanged."""
        if self.scaler is not None:
            return self.scaler.transform(X).astype(np.float32)
        return X.astype(np.float32)

    # ── Windowed extraction ──────────────────────────────────────────────────

    def extract_windows(
        self,
        events: list[dict],
        apply_scaler: bool = True,
    ) -> np.ndarray:
        """
        Extract feature vectors using a sliding window over *events*.

        Parameters
        ----------
        events        : flat list of event dicts (sorted by time is preferred)
        apply_scaler  : if True and scaler is loaded, normalise output

        Returns
        -------
        np.ndarray of shape (n_windows, 15).  Empty array if not enough events.
        """
        if len(events) < self.window_size:
            # Not enough events for even one full window; use what we have
            window_feats = _extract_window_features(events)
            X = window_feats.reshape(1, N_FEATURES)
        else:
            windows = []
            for start in range(0, len(events) - self.window_size + 1, self.stride):
                window = events[start : start + self.window_size]
                windows.append(_extract_window_features(window))
            X = np.vstack(windows).astype(np.float32)

        if apply_scaler and self.scaler is not None:
            X = self.transform(X)
        return X

    # ── Single-window real-time path ─────────────────────────────────────────

    def extract_single_window(
        self,
        events: list[dict],
        apply_scaler: bool = True,
    ) -> np.ndarray:
        """
        Extract a single feature vector from *events* for real-time inference.

        Uses the most recent `window_size` events if more are provided.

        Returns
        -------
        np.ndarray of shape (15,)
        """
        window = events[-self.window_size:] if len(events) > self.window_size else events
        feat   = _extract_window_features(window)  # shape (15,)
        if apply_scaler and self.scaler is not None:
            feat = self.transform(feat.reshape(1, -1)).flatten()
        return feat

    # ── Convenience dict serialisation ──────────────────────────────────────

    @staticmethod
    def feature_vector_to_dict(vec: np.ndarray) -> dict:
        """Convert a (15,) feature vector to a human-readable dict."""
        if vec.shape[0] != N_FEATURES:
            raise ValueError(f"Expected {N_FEATURES} features, got {vec.shape[0]}")
        return {name: float(val) for name, val in zip(FEATURE_NAMES, vec)}
