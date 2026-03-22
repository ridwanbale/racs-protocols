"""Apache Kafka event definitions — vendor-agnostic risk signal transport layer."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ..risk.risk_signals import RiskSignal


class KafkaTopics:
    RISK_SIGNALS = "racs.risk.signals"
    COORDINATION_COMMANDS = "racs.coordination.commands"
    AGENT_STATUS = "racs.agents.status"
    SAFETY_OVERRIDES = "racs.safety.overrides"
    CASCADE_ALERTS = "racs.risk.cascade_alerts"


@dataclass
class KafkaMessage:
    topic: str
    key: str
    value: Dict[str, Any]
    headers: Dict[str, str] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_bytes(self) -> bytes:
        payload = {
            "message_id": self.message_id,
            "timestamp": self.timestamp,
            "payload": self.value,
        }
        return json.dumps(payload).encode("utf-8")

    @classmethod
    def from_bytes(cls, topic: str, key: str, data: bytes) -> "KafkaMessage":
        parsed = json.loads(data.decode("utf-8"))
        return cls(
            topic=topic,
            key=key,
            value=parsed.get("payload", {}),
            message_id=parsed.get("message_id", str(uuid.uuid4())),
            timestamp=parsed.get("timestamp", time.time()),
        )


class KafkaEventPublisher:
    """
    Publishes RACS events in the standardised Kafka message format.

    In production this wraps a real confluent-kafka Producer.
    In simulation/testing it buffers messages in memory.
    """

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        self._servers = bootstrap_servers
        self._buffer: List[KafkaMessage] = []
        self._producer = None  # set to real producer when kafka is available

    def publish_risk_signal(self, signal: RiskSignal) -> KafkaMessage:
        msg = KafkaMessage(
            topic=KafkaTopics.RISK_SIGNALS,
            key=signal.site_id,
            value=signal.to_dict(),
        )
        self._emit(msg)
        return msg

    def publish_coordination_command(self, site_id: str, command: dict) -> KafkaMessage:
        msg = KafkaMessage(
            topic=KafkaTopics.COORDINATION_COMMANDS,
            key=site_id,
            value={"site_id": site_id, **command},
        )
        self._emit(msg)
        return msg

    def publish_agent_status(self, agent_status: dict) -> KafkaMessage:
        msg = KafkaMessage(
            topic=KafkaTopics.AGENT_STATUS,
            key=agent_status.get("site_id", "unknown"),
            value=agent_status,
        )
        self._emit(msg)
        return msg

    def _emit(self, msg: KafkaMessage) -> None:
        if self._producer:
            self._producer.produce(
                msg.topic,
                key=msg.key.encode(),
                value=msg.to_bytes(),
            )
        else:
            self._buffer.append(msg)

    def buffered_messages(self) -> List[KafkaMessage]:
        return list(self._buffer)

    def flush(self) -> None:
        if self._producer:
            self._producer.flush()
        else:
            self._buffer.clear()


class KafkaEventConsumer:
    """Simulated Kafka consumer that processes buffered messages for testing."""

    def __init__(self, topics: List[str]) -> None:
        self._topics = set(topics)
        self._handlers: Dict[str, Callable[[KafkaMessage], None]] = {}

    def register_handler(self, topic: str, handler: Callable[[KafkaMessage], None]) -> None:
        self._handlers[topic] = handler

    def process(self, messages: List[KafkaMessage]) -> int:
        processed = 0
        for msg in messages:
            if msg.topic in self._topics and msg.topic in self._handlers:
                self._handlers[msg.topic](msg)
                processed += 1
        return processed
