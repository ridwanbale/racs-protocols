"""XGBoost-based risk prediction engine."""

from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Optional

import numpy as np

from .risk_signals import RiskLevel, RiskSignal, TelemetryInput

LOCAL_ANOMALY_MEDIUM_SCORE = 0.30
LOCAL_ANOMALY_HIGH_SCORE = 0.60
LOCAL_ANOMALY_SCALE = LOCAL_ANOMALY_MEDIUM_SCORE

try:
    import xgboost as xgb
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False


def _make_feature_vector(t: TelemetryInput) -> np.ndarray:
    """Convert telemetry into a fixed-length feature vector."""
    robot_total = max(t.robot_active_count + t.robot_fault_count, 1)
    fault_ratio = t.robot_fault_count / robot_total
    return np.array([
        t.queue_length / 100.0,
        t.robot_active_count / 50.0,
        fault_ratio,
        1.0 - t.throughput_rate,
        t.error_rate_5min,
        t.avg_task_latency_s / 60.0,
        t.network_latency_ms / 500.0,
    ], dtype=np.float32)


class _HeuristicFallback:
    """Simple heuristic model used when XGBoost is unavailable or untrained."""

    def predict_congestion(self, features: np.ndarray) -> float:
        queue_norm, _, fault_ratio, capacity_gap, error_rate, latency_norm, _ = features
        return float(np.clip(0.4 * queue_norm + 0.3 * capacity_gap + 0.3 * error_rate, 0, 1))

    def predict_failure(self, features: np.ndarray) -> float:
        _, _, fault_ratio, capacity_gap, error_rate, latency_norm, net_lag = features
        return float(np.clip(0.5 * fault_ratio + 0.3 * error_rate + 0.2 * latency_norm, 0, 1))

    def predict_recovery_latency(self, features: np.ndarray) -> float:
        _, _, fault_ratio, capacity_gap, error_rate, latency_norm, _ = features
        base = 30.0
        return float(base + 120 * fault_ratio + 60 * capacity_gap + 30 * error_rate)


class RiskPredictor:
    """Predicts congestion probability, failure likelihood, and recovery latency from facility telemetry."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        self._model_path = model_path
        self._model: Optional[xgb.XGBRegressor] = None
        self._fallback = _HeuristicFallback()

        if model_path and Path(model_path).exists() and _XGB_AVAILABLE:
            self._load(model_path)

    def _load(self, path: str) -> None:
        with open(path, "rb") as f:
            self._model = pickle.load(f)

    def train_on_synthetic(self, n_samples: int = 5000, save_path: Optional[str] = None) -> None:
        """Train models on synthetic facility telemetry."""
        if not _XGB_AVAILABLE:
            raise RuntimeError("xgboost is required for training — pip install xgboost")

        rng = np.random.default_rng(42)

        queue = rng.integers(0, 100, n_samples)
        robots = rng.integers(5, 50, n_samples)
        faults = rng.integers(0, robots // 4 + 1)
        throughput = rng.uniform(0.3, 1.0, n_samples)
        errors = rng.exponential(0.05, n_samples)
        latency = rng.uniform(0, 30, n_samples)
        net_lag = rng.uniform(0, 200, n_samples)

        X = np.column_stack([
            queue / 100.0,
            robots / 50.0,
            faults / np.maximum(robots, 1),
            1.0 - throughput,
            np.clip(errors, 0, 1),
            latency / 60.0,
            net_lag / 500.0,
        ]).astype(np.float32)

        fault_ratio = faults / np.maximum(robots, 1)
        capacity_gap = 1.0 - throughput
        y_congestion = np.clip(0.4 * (queue / 100.0) + 0.3 * capacity_gap + 0.3 * errors, 0, 1)
        y_failure = np.clip(0.5 * fault_ratio + 0.3 * errors + 0.2 * (latency / 60.0), 0, 1)
        y_recovery = 30.0 + 120 * fault_ratio + 60 * capacity_gap + 30 * errors

        self._model = {
            "congestion": xgb.XGBRegressor(n_estimators=100, max_depth=4, random_state=42),
            "failure": xgb.XGBRegressor(n_estimators=100, max_depth=4, random_state=42),
            "recovery": xgb.XGBRegressor(n_estimators=100, max_depth=4, random_state=42),
        }
        self._model["congestion"].fit(X, y_congestion)
        self._model["failure"].fit(X, y_failure)
        self._model["recovery"].fit(X, y_recovery)

        if save_path:
            os.makedirs(Path(save_path).parent, exist_ok=True)
            with open(save_path, "wb") as f:
                pickle.dump(self._model, f)

    def predict(self, telemetry: TelemetryInput) -> RiskSignal:
        """Generate a RiskSignal from raw facility telemetry."""
        features = _make_feature_vector(telemetry)

        if self._model and isinstance(self._model, dict):
            X = features.reshape(1, -1)
            congestion = float(np.clip(self._model["congestion"].predict(X)[0], 0, 1))
            failure = float(np.clip(self._model["failure"].predict(X)[0], 0, 1))
            recovery = float(max(self._model["recovery"].predict(X)[0], 0))
            confidence = 0.90
        else:
            congestion = self._fallback.predict_congestion(features)
            failure = self._fallback.predict_failure(features)
            recovery = self._fallback.predict_recovery_latency(features)
            confidence = 0.70

        site_score = 0.45 * congestion + 0.40 * failure + 0.15 * min(recovery / 300.0, 1.0)
        local_anomaly_score = _local_anomaly_risk_score(telemetry.suspect_robot_anomaly)
        effective_score = max(site_score, local_anomaly_score)
        level = RiskLevel.from_score(effective_score)

        return RiskSignal(
            site_id=telemetry.site_id,
            congestion_probability=congestion,
            failure_likelihood=failure,
            recovery_latency_seconds=recovery,
            level=level,
            confidence=confidence,
            site_risk_score=site_score,
            local_anomaly_score=local_anomaly_score,
            source_telemetry=telemetry,
        )


def _local_anomaly_risk_score(suspect_robot_anomaly: float) -> float:
    """
    Map observable peer-relative robot task-age anomaly to bounded local risk.

    The simulator reports suspect_robot_anomaly as:
        (suspect current-task age - fastest peer current-task age) / base_service_steps

    A value of 1.0 means the suspect robot is lagging a peer by roughly one
    expected healthy service duration, which is treated as MEDIUM local risk.
    """
    return float(np.clip(max(suspect_robot_anomaly, 0.0) * LOCAL_ANOMALY_SCALE, 0.0, 1.0))
