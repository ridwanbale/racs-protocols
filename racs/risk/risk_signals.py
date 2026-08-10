"""Risk signal definitions — the core data types exchanged between agents."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        if score < 0.3:
            return cls.LOW
        if score < 0.55:
            return cls.MEDIUM
        if score < 0.80:
            return cls.HIGH
        return cls.CRITICAL


@dataclass
class TelemetryInput:
    """Raw sensor and operational data from a facility."""

    site_id: str
    queue_length: int
    robot_active_count: int
    robot_fault_count: int
    throughput_rate: float        # 0.0–1.0 fraction of target capacity
    error_rate_5min: float        # errors per minute over last 5 min
    avg_task_latency_s: float = 0.0
    network_latency_ms: float = 0.0
    suspect_robot_id: Optional[str] = None
    suspect_robot_anomaly: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class RiskSignal:
    """Predictive risk signal produced by a Site Agent and shared across the network."""

    site_id: str
    congestion_probability: float      # 0.0–1.0
    failure_likelihood: float          # 0.0–1.0
    recovery_latency_seconds: float    # estimated seconds to recover
    level: RiskLevel
    confidence: float = 1.0            # model confidence 0.0–1.0
    site_risk_score: Optional[float] = None
    local_anomaly_score: float = 0.0
    signal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    source_telemetry: Optional[TelemetryInput] = None

    @property
    def site_composite_score(self) -> float:
        """Generic site-level risk score from congestion/failure/recovery features."""
        return (
            0.45 * self.congestion_probability
            + 0.40 * self.failure_likelihood
            + 0.15 * min(self.recovery_latency_seconds / 300.0, 1.0)
        )

    @property
    def composite_score(self) -> float:
        """Effective risk score used for prioritisation."""
        site_score = self.site_risk_score
        if site_score is None:
            site_score = self.site_composite_score
        return max(site_score, self.local_anomaly_score)

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "site_id": self.site_id,
            "congestion_probability": self.congestion_probability,
            "failure_likelihood": self.failure_likelihood,
            "recovery_latency_seconds": self.recovery_latency_seconds,
            "site_risk_score": (
                self.site_composite_score
                if self.site_risk_score is None
                else self.site_risk_score
            ),
            "local_anomaly_score": self.local_anomaly_score,
            "composite_score": self.composite_score,
            "level": self.level.value,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RiskSignal":
        return cls(
            site_id=data["site_id"],
            congestion_probability=data["congestion_probability"],
            failure_likelihood=data["failure_likelihood"],
            recovery_latency_seconds=data["recovery_latency_seconds"],
            level=RiskLevel(data["level"]),
            confidence=data.get("confidence", 1.0),
            site_risk_score=data.get("site_risk_score"),
            local_anomaly_score=data.get("local_anomaly_score", 0.0),
            signal_id=data.get("signal_id", str(uuid.uuid4())),
            timestamp=data.get("timestamp", time.time()),
        )
