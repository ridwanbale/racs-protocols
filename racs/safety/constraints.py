"""Safety constraint layer — hard limits that cannot be overridden by optimisation."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


KNOWN_COORDINATION_COMMANDS = {
    "throttle_outflow",
    "reduce_intake",
    "redistribute_tasks",
    "quarantine_robot",
    "drain_robot",
    "safe_hold",
}


class ConstraintAction(Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE_TO_HUMAN = "ESCALATE_TO_HUMAN"


@dataclass
class ConstraintViolation:
    constraint_name: str
    actual_value: Any
    limit_value: Any
    description: str


@dataclass
class SafetyResult:
    allowed: bool
    action: ConstraintAction
    violations: List[ConstraintViolation] = field(default_factory=list)
    reason: str = ""
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "allowed": self.allowed,
            "action": self.action.value,
            "reason": self.reason,
            "violations": [
                {
                    "constraint": v.constraint_name,
                    "actual": v.actual_value,
                    "limit": v.limit_value,
                    "description": v.description,
                }
                for v in self.violations
            ],
            "timestamp": self.timestamp,
        }


@dataclass
class SafetyConfig:
    """Safety policy configuration — loaded from YAML so policy is separate from code."""

    max_robot_speed_ms: float = 2.0           # metres per second
    min_robot_spacing_m: float = 1.5          # metres between robots
    max_site_robot_density: float = 0.85      # fraction of rated capacity
    max_fault_ratio: float = 0.20             # faults / total robots
    emergency_stop_risk_threshold: float = 0.95
    escalate_risk_threshold: float = 0.75
    exclusion_zones: List[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> "SafetyConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def default(cls) -> "SafetyConfig":
        return cls()


class SafetyGate:
    """
    Wraps every coordination decision and enforces hard + soft safety constraints.

    Hard constraints can never be overridden by the optimisation layer.
    Soft constraints may be relaxed by an authorised human operator.
    """

    def __init__(self, config: Optional[SafetyConfig] = None) -> None:
        self._config = config or SafetyConfig.default()

    def evaluate(self, action: Dict[str, Any]) -> SafetyResult:
        """
        Evaluate a proposed coordination action against safety constraints.

        ``action`` is a dict describing the proposed decision. Required keys vary
        by action type; missing keys are treated as zero / safe defaults.
        """
        violations: List[ConstraintViolation] = []
        command_type = action.get("type", "")
        if command_type and command_type not in KNOWN_COORDINATION_COMMANDS:
            violations.append(ConstraintViolation(
                constraint_name="known_command_type",
                actual_value=command_type,
                limit_value=sorted(KNOWN_COORDINATION_COMMANDS),
                description=f"Unknown coordination command type '{command_type}'",
            ))
        if command_type in ("quarantine_robot", "drain_robot") and not action.get("robot_id"):
            violations.append(ConstraintViolation(
                constraint_name="required_robot_id",
                actual_value=action.get("robot_id"),
                limit_value="non-empty robot_id",
                description=f"{command_type} command requires robot_id",
            ))

        # Hard constraint: robot speed
        proposed_speed = action.get("robot_speed_ms", 0.0)
        if proposed_speed > self._config.max_robot_speed_ms:
            violations.append(ConstraintViolation(
                constraint_name="max_robot_speed_ms",
                actual_value=proposed_speed,
                limit_value=self._config.max_robot_speed_ms,
                description=f"Proposed speed {proposed_speed:.1f} m/s exceeds limit {self._config.max_robot_speed_ms:.1f} m/s",
            ))

        # Hard constraint: site robot density
        proposed_density = action.get("target_site_robot_density", 0.0)
        if proposed_density > self._config.max_site_robot_density:
            violations.append(ConstraintViolation(
                constraint_name="max_site_robot_density",
                actual_value=proposed_density,
                limit_value=self._config.max_site_robot_density,
                description=f"Target density {proposed_density:.0%} exceeds safe limit {self._config.max_site_robot_density:.0%}",
            ))

        # Hard constraint: fault ratio at target site
        fault_ratio = action.get("target_fault_ratio", 0.0)
        if fault_ratio > self._config.max_fault_ratio:
            violations.append(ConstraintViolation(
                constraint_name="max_fault_ratio",
                actual_value=fault_ratio,
                limit_value=self._config.max_fault_ratio,
                description=f"Target site fault ratio {fault_ratio:.0%} exceeds safe limit {self._config.max_fault_ratio:.0%}",
            ))

        # Hard constraint: exclusion zones
        target_zone = action.get("target_zone", "")
        if target_zone in self._config.exclusion_zones:
            violations.append(ConstraintViolation(
                constraint_name="exclusion_zone",
                actual_value=target_zone,
                limit_value=self._config.exclusion_zones,
                description=f"Zone '{target_zone}' is a designated exclusion zone",
            ))

        # Emergency stop threshold
        risk_score = action.get("risk_score", 0.0)
        if risk_score >= self._config.emergency_stop_risk_threshold:
            violations.append(ConstraintViolation(
                constraint_name="emergency_stop_risk_threshold",
                actual_value=risk_score,
                limit_value=self._config.emergency_stop_risk_threshold,
                description=f"Risk score {risk_score:.2f} triggers emergency stop",
            ))

        if violations:
            # Any hard constraint violation blocks the action
            return SafetyResult(
                allowed=False,
                action=ConstraintAction.BLOCK,
                violations=violations,
                reason="; ".join(v.description for v in violations),
            )

        # Soft: high risk warrants human escalation before execution
        if risk_score >= self._config.escalate_risk_threshold:
            return SafetyResult(
                allowed=False,
                action=ConstraintAction.ESCALATE_TO_HUMAN,
                reason=f"Risk score {risk_score:.2f} requires human authorisation before execution",
            )

        return SafetyResult(
            allowed=True,
            action=ConstraintAction.ALLOW,
            reason="All safety constraints satisfied",
        )

    def update_config(self, config: SafetyConfig) -> None:
        self._config = config
