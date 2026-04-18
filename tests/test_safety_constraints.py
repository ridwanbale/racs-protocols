"""Tests for safety constraints and the SafetyGate."""

import pytest

from racs.safety.constraints import (
    ConstraintAction,
    SafetyConfig,
    SafetyGate,
    SafetyResult,
)


def make_gate(**config_overrides) -> SafetyGate:
    config = SafetyConfig(**{
        k: v for k, v in config_overrides.items()
        if k in SafetyConfig.__dataclass_fields__
    })
    return SafetyGate(config)


def safe_action(**overrides) -> dict:
    base = {
        "type": "redistribute_tasks",
        "robot_speed_ms": 1.5,
        "target_site_robot_density": 0.60,
        "target_fault_ratio": 0.05,
        "risk_score": 0.3,
    }
    base.update(overrides)
    return base


class TestSafetyGate:
    def test_safe_action_is_allowed(self):
        gate = make_gate()
        result = gate.evaluate(safe_action())
        assert result.allowed is True
        assert result.action == ConstraintAction.ALLOW

    def test_speed_violation_blocks_action(self):
        gate = make_gate(max_robot_speed_ms=2.0)
        result = gate.evaluate(safe_action(robot_speed_ms=3.5))
        assert result.allowed is False
        assert result.action == ConstraintAction.BLOCK
        assert any(v.constraint_name == "max_robot_speed_ms" for v in result.violations)

    def test_density_violation_blocks_action(self):
        gate = make_gate(max_site_robot_density=0.85)
        result = gate.evaluate(safe_action(target_site_robot_density=0.95))
        assert result.allowed is False
        assert result.action == ConstraintAction.BLOCK

    def test_fault_ratio_violation_blocks_action(self):
        gate = make_gate(max_fault_ratio=0.20)
        result = gate.evaluate(safe_action(target_fault_ratio=0.30))
        assert result.allowed is False
        assert result.action == ConstraintAction.BLOCK

    def test_exclusion_zone_blocks_action(self):
        gate = make_gate(exclusion_zones=["ZONE_HAZMAT"])
        result = gate.evaluate(safe_action(target_zone="ZONE_HAZMAT"))
        assert result.allowed is False
        assert result.action == ConstraintAction.BLOCK

    def test_high_risk_escalates_to_human(self):
        gate = make_gate(escalate_risk_threshold=0.75, emergency_stop_risk_threshold=0.95)
        result = gate.evaluate(safe_action(risk_score=0.80))
        assert result.allowed is False
        assert result.action == ConstraintAction.ESCALATE_TO_HUMAN

    def test_critical_risk_emergency_stop(self):
        gate = make_gate(emergency_stop_risk_threshold=0.95)
        result = gate.evaluate(safe_action(risk_score=0.96))
        assert result.allowed is False
        assert result.action == ConstraintAction.BLOCK

    def test_result_has_audit_id(self):
        gate = make_gate()
        result = gate.evaluate(safe_action())
        assert result.audit_id
        assert isinstance(result.audit_id, str)

    def test_multiple_violations_all_reported(self):
        gate = make_gate(max_robot_speed_ms=2.0, max_site_robot_density=0.85)
        result = gate.evaluate(safe_action(robot_speed_ms=4.0, target_site_robot_density=0.99))
        assert len(result.violations) >= 2
