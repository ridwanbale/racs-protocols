"""Complete decision audit trail — every safety and coordination decision is logged here."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditEventType(Enum):
    SAFETY_ALLOWED = "SAFETY_ALLOWED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    SAFETY_ESCALATED = "SAFETY_ESCALATED"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    DEGRADATION_ENTERED = "DEGRADATION_ENTERED"
    DEGRADATION_EXITED = "DEGRADATION_EXITED"
    COORDINATION_COMMAND = "COORDINATION_COMMAND"
    RISK_SIGNAL = "RISK_SIGNAL"
    CASCADE_ALERT = "CASCADE_ALERT"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_STOPPED = "AGENT_STOPPED"


@dataclass
class AuditEvent:
    event_type: AuditEventType
    site_id: str
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    operator_id: Optional[str] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "site_id": self.site_id,
            "description": self.description,
            "details": self.details,
            "operator_id": self.operator_id,
            "timestamp": self.timestamp,
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp)),
        }


class AuditLog:
    """Append-only audit log for all safety and coordination decisions."""

    def __init__(self, log_path: Optional[str] = None) -> None:
        self._events: List[AuditEvent] = []
        self._log_path = log_path
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)
        if self._log_path:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(event.to_dict()) + "\n")

    def log(
        self,
        event_type: AuditEventType,
        site_id: str,
        description: str,
        details: Optional[Dict[str, Any]] = None,
        operator_id: Optional[str] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            site_id=site_id,
            description=description,
            details=details or {},
            operator_id=operator_id,
        )
        self.record(event)
        return event

    def events_for_site(self, site_id: str) -> List[AuditEvent]:
        return [e for e in self._events if e.site_id == site_id]

    def events_of_type(self, event_type: AuditEventType) -> List[AuditEvent]:
        return [e for e in self._events if e.event_type == event_type]

    def recent(self, n: int = 50) -> List[AuditEvent]:
        return self._events[-n:]

    def to_json(self) -> str:
        return json.dumps([e.to_dict() for e in self._events], indent=2)

    def __len__(self) -> int:
        return len(self._events)
