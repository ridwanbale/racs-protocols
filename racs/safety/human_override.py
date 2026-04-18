"""Human-in-the-loop override system — high-risk actions require explicit human approval."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

from .audit_log import AuditEvent, AuditEventType, AuditLog


class OverrideDecision(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"
    TIMED_OUT = "TIMED_OUT"


@dataclass
class OverrideRequest:
    site_id: str
    action_description: str
    risk_score: float
    recommended_action: str
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    decision: OverrideDecision = OverrideDecision.PENDING
    operator_id: Optional[str] = None
    operator_justification: str = ""
    decided_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "site_id": self.site_id,
            "action_description": self.action_description,
            "risk_score": self.risk_score,
            "recommended_action": self.recommended_action,
            "decision": self.decision.value,
            "operator_id": self.operator_id,
            "operator_justification": self.operator_justification,
            "timestamp": self.timestamp,
            "decided_at": self.decided_at,
            "response_latency_s": (self.decided_at - self.timestamp) if self.decided_at else None,
        }


class HumanOverrideSystem:
    """
    Manages human approval workflow for high-risk coordination actions.

    High-risk actions are never executed autonomously; they are queued here
    and only proceed after an operator explicitly approves them.
    """

    def __init__(
        self,
        audit_log: Optional[AuditLog] = None,
        approval_timeout_s: float = 300.0,
        on_escalation: Optional[Callable[[OverrideRequest], None]] = None,
    ) -> None:
        self._pending: Dict[str, OverrideRequest] = {}
        self._history: List[OverrideRequest] = []
        self._audit = audit_log or AuditLog()
        self._timeout_s = approval_timeout_s
        self._on_escalation = on_escalation

    def request_approval(
        self,
        site_id: str,
        action_description: str,
        risk_score: float,
        recommended_action: str,
    ) -> OverrideRequest:
        """Create and queue a new override request. Returns immediately — does not block."""
        req = OverrideRequest(
            site_id=site_id,
            action_description=action_description,
            risk_score=risk_score,
            recommended_action=recommended_action,
        )
        self._pending[req.request_id] = req

        self._audit.log(
            AuditEventType.SAFETY_ESCALATED,
            site_id=site_id,
            description=f"Human approval required: {action_description}",
            details=req.to_dict(),
        )

        if self._on_escalation:
            self._on_escalation(req)

        return req

    def approve(self, request_id: str, operator_id: str, justification: str = "") -> OverrideRequest:
        """Operator approves the pending action."""
        req = self._resolve(request_id)
        req.decision = OverrideDecision.APPROVED
        req.operator_id = operator_id
        req.operator_justification = justification
        req.decided_at = time.time()

        self._audit.log(
            AuditEventType.HUMAN_OVERRIDE,
            site_id=req.site_id,
            description=f"Operator {operator_id} APPROVED: {req.action_description}",
            details=req.to_dict(),
            operator_id=operator_id,
        )
        return req

    def reject(self, request_id: str, operator_id: str, justification: str = "") -> OverrideRequest:
        """Operator rejects the pending action."""
        req = self._resolve(request_id)
        req.decision = OverrideDecision.REJECTED
        req.operator_id = operator_id
        req.operator_justification = justification
        req.decided_at = time.time()

        self._audit.log(
            AuditEventType.HUMAN_OVERRIDE,
            site_id=req.site_id,
            description=f"Operator {operator_id} REJECTED: {req.action_description}",
            details=req.to_dict(),
            operator_id=operator_id,
        )
        return req

    def expire_timed_out(self) -> List[OverrideRequest]:
        """Mark requests that exceeded the timeout as TIMED_OUT. Returns the expired list."""
        now = time.time()
        expired = []
        for req_id, req in list(self._pending.items()):
            if req.decision == OverrideDecision.PENDING and now - req.timestamp > self._timeout_s:
                req.decision = OverrideDecision.TIMED_OUT
                req.decided_at = now
                self._history.append(req)
                del self._pending[req_id]
                expired.append(req)
        return expired

    def pending_requests(self) -> List[OverrideRequest]:
        self.expire_timed_out()
        return list(self._pending.values())

    def _resolve(self, request_id: str) -> OverrideRequest:
        req = self._pending.pop(request_id, None)
        if req is None:
            raise KeyError(f"No pending request with id {request_id}")
        self._history.append(req)
        return req

    def response_latencies(self) -> List[float]:
        return [
            r.decided_at - r.timestamp
            for r in self._history
            if r.decided_at and r.decision in (OverrideDecision.APPROVED, OverrideDecision.REJECTED)
        ]

    def avg_response_latency(self) -> Optional[float]:
        latencies = self.response_latencies()
        return sum(latencies) / len(latencies) if latencies else None
