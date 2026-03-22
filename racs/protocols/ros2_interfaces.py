"""ROS 2 message type mappings — maps RACS data types to ROS 2 compatible message formats."""

from __future__ import annotations

from typing import Any, Dict

from ..risk.risk_signals import RiskLevel, RiskSignal
from ..safety.constraints import SafetyResult


class ROS2MessageMapper:
    """
    Maps RACS internal types to ROS 2 message-compatible dicts.

    In a real ROS 2 deployment, replace these dict representations with
    generated message classes from custom .msg definitions.
    """

    @staticmethod
    def risk_signal_to_ros2(signal: RiskSignal) -> Dict[str, Any]:
        """Maps to a custom racs_msgs/RiskSignal.msg."""
        return {
            "_type": "racs_msgs/RiskSignal",
            "header": {
                "stamp": {"sec": int(signal.timestamp), "nanosec": int((signal.timestamp % 1) * 1e9)},
                "frame_id": signal.site_id,
            },
            "site_id": signal.site_id,
            "signal_id": signal.signal_id,
            "congestion_probability": signal.congestion_probability,
            "failure_likelihood": signal.failure_likelihood,
            "recovery_latency_seconds": signal.recovery_latency_seconds,
            "composite_score": signal.composite_score,
            "risk_level": signal.level.value,
            "confidence": signal.confidence,
        }

    @staticmethod
    def coordination_command_to_ros2(site_id: str, command: dict) -> Dict[str, Any]:
        """Maps to racs_msgs/CoordinationCommand.msg."""
        import time
        return {
            "_type": "racs_msgs/CoordinationCommand",
            "header": {
                "stamp": {"sec": int(time.time()), "nanosec": 0},
                "frame_id": site_id,
            },
            "target_site_id": site_id,
            "command_type": command.get("type", ""),
            "parameters": {k: str(v) for k, v in command.items() if k != "type"},
        }

    @staticmethod
    def safety_result_to_ros2(result: SafetyResult) -> Dict[str, Any]:
        """Maps to racs_msgs/SafetyResult.msg."""
        return {
            "_type": "racs_msgs/SafetyResult",
            "audit_id": result.audit_id,
            "allowed": result.allowed,
            "action": result.action.value,
            "reason": result.reason,
            "violation_count": len(result.violations),
        }

    @staticmethod
    def agent_status_to_ros2(status: dict) -> Dict[str, Any]:
        """Maps to racs_msgs/AgentStatus.msg."""
        import time
        return {
            "_type": "racs_msgs/AgentStatus",
            "header": {
                "stamp": {"sec": int(time.time()), "nanosec": 0},
                "frame_id": status.get("site_id", ""),
            },
            "site_id": status.get("site_id", ""),
            "degradation_level": status.get("degradation_level", "NORMAL"),
            "speed_factor": status.get("speed_factor", 1.0),
            "connected": status.get("connected", True),
        }
