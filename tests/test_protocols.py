"""Tests for protocol schemas and message validation."""

import json
from pathlib import Path

import pytest

try:
    import jsonschema
    _JSONSCHEMA_AVAILABLE = True
except ImportError:
    _JSONSCHEMA_AVAILABLE = False

from racs.risk.risk_signals import RiskLevel, RiskSignal
from racs.protocols.kafka_events import KafkaEventPublisher, KafkaMessage, KafkaTopics
from racs.protocols.ros2_interfaces import ROS2MessageMapper
from racs.safety.constraints import ConstraintAction, SafetyResult

SCHEMA_DIR = Path(__file__).parent.parent / "racs" / "protocols" / "schemas"


def load_schema(name: str) -> dict:
    with open(SCHEMA_DIR / name) as f:
        return json.load(f)


def make_signal() -> RiskSignal:
    return RiskSignal(
        site_id="TEST",
        congestion_probability=0.5,
        failure_likelihood=0.3,
        recovery_latency_seconds=45.0,
        level=RiskLevel.MEDIUM,
        confidence=0.85,
    )


@pytest.mark.skipif(not _JSONSCHEMA_AVAILABLE, reason="jsonschema not installed")
class TestSchemaValidation:
    def test_risk_signal_schema_loads(self):
        schema = load_schema("risk_signal.schema.json")
        assert schema["title"] == "RiskSignal"

    def test_valid_risk_signal_passes_schema(self):
        schema = load_schema("risk_signal.schema.json")
        sig = make_signal()
        data = sig.to_dict()
        jsonschema.validate(instance=data, schema=schema)

    def test_invalid_risk_signal_fails_schema(self):
        schema = load_schema("risk_signal.schema.json")
        bad_data = {"site_id": "X", "congestion_probability": 1.5}  # missing required fields + out-of-range
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad_data, schema=schema)

    def test_coordination_command_schema_loads(self):
        schema = load_schema("coordination_command.schema.json")
        assert schema["title"] == "CoordinationCommand"

    def test_agent_status_schema_loads(self):
        schema = load_schema("agent_status.schema.json")
        assert schema["title"] == "AgentStatus"

    def test_safety_override_schema_loads(self):
        schema = load_schema("safety_override.schema.json")
        assert schema["title"] == "SafetyOverride"


class TestKafkaEvents:
    def test_publish_risk_signal_buffers_message(self):
        publisher = KafkaEventPublisher()
        sig = make_signal()
        msg = publisher.publish_risk_signal(sig)
        assert msg.topic == KafkaTopics.RISK_SIGNALS
        assert msg.key == sig.site_id
        assert len(publisher.buffered_messages()) == 1

    def test_message_roundtrip(self):
        publisher = KafkaEventPublisher()
        sig = make_signal()
        msg = publisher.publish_risk_signal(sig)
        raw = msg.to_bytes()
        restored = KafkaMessage.from_bytes(msg.topic, msg.key, raw)
        assert restored.value["site_id"] == sig.site_id

    def test_publish_coordination_command(self):
        publisher = KafkaEventPublisher()
        msg = publisher.publish_coordination_command("SITE_A", {"type": "throttle_outflow"})
        assert msg.topic == KafkaTopics.COORDINATION_COMMANDS


class TestROS2Mapper:
    def test_risk_signal_to_ros2_has_type(self):
        sig = make_signal()
        ros_msg = ROS2MessageMapper.risk_signal_to_ros2(sig)
        assert ros_msg["_type"] == "racs_msgs/RiskSignal"
        assert ros_msg["site_id"] == sig.site_id

    def test_coordination_command_to_ros2(self):
        ros_msg = ROS2MessageMapper.coordination_command_to_ros2("SITE_B", {"type": "safe_hold"})
        assert ros_msg["_type"] == "racs_msgs/CoordinationCommand"
        assert ros_msg["target_site_id"] == "SITE_B"

    def test_safety_result_to_ros2(self):
        result = SafetyResult(allowed=True, action=ConstraintAction.ALLOW, reason="OK")
        ros_msg = ROS2MessageMapper.safety_result_to_ros2(result)
        assert ros_msg["_type"] == "racs_msgs/SafetyResult"
        assert ros_msg["allowed"] is True
