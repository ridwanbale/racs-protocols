"""Per-facility Site Agent — the local decision-making unit for each warehouse/site."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..risk.predictor import RiskPredictor
from ..risk.risk_signals import RiskSignal, TelemetryInput
from ..safety.audit_log import AuditEventType, AuditLog
from ..safety.constraints import SafetyConfig, SafetyGate
from ..safety.controlled_degradation import DegradationController, DegradationLevel
from ..safety.human_override import HumanOverrideSystem


@dataclass
class AgentConfig:
    site_id: str
    risk_publish_interval_s: float = 10.0
    degradation_threshold: float = 0.75
    emergency_threshold: float = 0.95
    model_path: Optional[str] = None
    safety_config: Optional[SafetyConfig] = None
    audit_log_path: Optional[str] = None


class SiteAgent:
    """
    Local agent for one facility. Runs risk prediction, publishes signals,
    executes coordination commands, and enforces safety constraints.

    Operates autonomously if network connectivity to the NetworkBrain is lost.
    """

    def __init__(
        self,
        config: AgentConfig,
        on_signal_publish: Optional[Callable[[RiskSignal], None]] = None,
    ) -> None:
        self._config = config
        self._site_id = config.site_id
        self._predictor = RiskPredictor(model_path=config.model_path)
        self._safety_gate = SafetyGate(config.safety_config)
        self._degradation = DegradationController(site_id=config.site_id)
        self._audit = AuditLog(log_path=config.audit_log_path)
        self._override = HumanOverrideSystem(audit_log=self._audit)
        self._on_signal_publish = on_signal_publish

        self._latest_signal: Optional[RiskSignal] = None
        self._last_publish: Optional[float] = None
        self._connected: bool = True   # False when network brain is unreachable
        self._robot_states: Dict[str, str] = {}   # robot_id -> state

        self._audit.log(
            AuditEventType.AGENT_STARTED,
            site_id=self._site_id,
            description=f"SiteAgent started for {self._site_id}",
        )

    def tick(self, telemetry: TelemetryInput, now: Optional[float] = None) -> Optional[RiskSignal]:
        """
        Process one observation cycle. Returns a new RiskSignal if it's time to publish.
        Call this periodically (e.g. every second) from a control loop.
        """
        effective_now = time.time() if now is None else now
        signal = self._predictor.predict(telemetry)
        signal.timestamp = effective_now
        self._latest_signal = signal

        # Automatic degradation management
        self._manage_degradation(signal)

        should_publish = (
            self._last_publish is None
            or effective_now - self._last_publish >= self._config.risk_publish_interval_s
        )
        if should_publish:
            self._last_publish = effective_now
            if self._on_signal_publish:
                self._on_signal_publish(signal)
            return signal

        return None

    def _manage_degradation(self, signal: RiskSignal) -> None:
        score = signal.composite_score
        current = self._degradation.current_level

        if score >= self._config.emergency_threshold and current != DegradationLevel.EMERGENCY_STOP:
            self._degradation.emergency_stop(
                reason=f"Risk score {score:.2f} exceeded emergency threshold"
            )
        elif score >= self._config.degradation_threshold and not current.is_degraded():
            self._degradation.degrade(
                reason=f"Risk score {score:.2f} exceeded degradation threshold"
            )
        elif score < 0.4 and current.is_degraded():
            self._degradation.attempt_recovery()

    def execute_command(self, command: dict) -> dict:
        """
        Evaluate and execute a coordination command from the NetworkBrain.
        All commands pass through the SafetyGate before execution.
        """
        command["risk_score"] = (
            self._latest_signal.composite_score if self._latest_signal else 0.0
        )
        result = self._safety_gate.evaluate(command)

        if result.allowed:
            self._audit.log(
                AuditEventType.COORDINATION_COMMAND,
                site_id=self._site_id,
                description=f"Executed command: {command.get('type', 'unknown')}",
                details={"command": command, "safety_result": result.to_dict()},
            )
            return {"status": "executed", "audit_id": result.audit_id}

        if result.action.value == "ESCALATE_TO_HUMAN":
            req = self._override.request_approval(
                site_id=self._site_id,
                action_description=str(command),
                risk_score=command.get("risk_score", 0),
                recommended_action=command.get("type", "unknown"),
            )
            return {"status": "pending_human_approval", "request_id": req.request_id}

        self._audit.log(
            AuditEventType.SAFETY_BLOCKED,
            site_id=self._site_id,
            description=f"Command blocked: {result.reason}",
            details=result.to_dict(),
        )
        return {"status": "blocked", "reason": result.reason}

    def mark_disconnected(self) -> None:
        """Called when the NetworkBrain is unreachable — agent continues in local-safe mode."""
        self._connected = False
        self._degradation.degrade(reason="Network brain unreachable — autonomous safe mode")

    def mark_connected(self) -> None:
        self._connected = True

    @property
    def is_operational(self) -> bool:
        return self._degradation.is_operational()

    @property
    def degradation_level(self) -> DegradationLevel:
        return self._degradation.current_level

    @property
    def latest_signal(self) -> Optional[RiskSignal]:
        return self._latest_signal

    @property
    def site_id(self) -> str:
        return self._site_id

    def audit_summary(self) -> dict:
        return {
            "site_id": self._site_id,
            "degradation_level": self._degradation.current_level.name,
            "speed_factor": self._degradation.speed_factor,
            "connected": self._connected,
            "total_audit_events": len(self._audit),
            "pending_overrides": len(self._override.pending_requests()),
        }
