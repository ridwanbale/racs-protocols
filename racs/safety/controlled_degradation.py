"""Graceful degradation — transitions the system to progressively safer operating states."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from .audit_log import AuditEventType, AuditLog


class DegradationLevel(Enum):
    """Ordered degradation states from full operation to emergency stop."""
    NORMAL = 0
    REDUCED_SPEED = 1        # robots slow to 60% rated speed
    LIMITED_OPERATIONS = 2   # non-essential tasks suspended
    SAFE_HOLD = 3            # all motion paused, humans notified
    EMERGENCY_STOP = 4       # full system halt

    def is_degraded(self) -> bool:
        return self != DegradationLevel.NORMAL


@dataclass
class DegradationEvent:
    site_id: str
    from_level: DegradationLevel
    to_level: DegradationLevel
    trigger_reason: str
    timestamp: float = field(default_factory=time.time)
    recovered_at: Optional[float] = None

    @property
    def duration_s(self) -> Optional[float]:
        return (self.recovered_at - self.timestamp) if self.recovered_at else None


# Speed multipliers applied at each degradation level
_SPEED_FACTORS: Dict[DegradationLevel, float] = {
    DegradationLevel.NORMAL: 1.0,
    DegradationLevel.REDUCED_SPEED: 0.60,
    DegradationLevel.LIMITED_OPERATIONS: 0.30,
    DegradationLevel.SAFE_HOLD: 0.0,
    DegradationLevel.EMERGENCY_STOP: 0.0,
}


class DegradationController:
    """
    Manages graceful degradation for a single site.

    Transitions are one-way toward safety unless confirmed clear by explicit recovery.
    Recovery is graduated — the system steps back one level at a time.
    """

    def __init__(
        self,
        site_id: str,
        audit_log: Optional[AuditLog] = None,
        recovery_hold_s: float = 30.0,
    ) -> None:
        self._site_id = site_id
        self._level = DegradationLevel.NORMAL
        self._history: List[DegradationEvent] = []
        self._audit = audit_log or AuditLog()
        self._recovery_hold_s = recovery_hold_s
        self._last_transition: float = time.time()

    @property
    def current_level(self) -> DegradationLevel:
        return self._level

    @property
    def speed_factor(self) -> float:
        return _SPEED_FACTORS[self._level]

    def degrade(self, reason: str, target: Optional[DegradationLevel] = None) -> DegradationLevel:
        """Move to the next (or specified) degradation level. Never moves toward NORMAL."""
        if target is None:
            target = DegradationLevel(min(self._level.value + 1, DegradationLevel.EMERGENCY_STOP.value))

        if target.value <= self._level.value:
            return self._level  # already at or beyond target

        event = DegradationEvent(
            site_id=self._site_id,
            from_level=self._level,
            to_level=target,
            trigger_reason=reason,
        )
        self._history.append(event)
        self._level = target
        self._last_transition = time.time()

        self._audit.log(
            AuditEventType.DEGRADATION_ENTERED,
            site_id=self._site_id,
            description=f"Degraded to {target.name}: {reason}",
            details={
                "from_level": event.from_level.name,
                "to_level": target.name,
                "speed_factor": self.speed_factor,
                "reason": reason,
            },
        )
        return self._level

    def attempt_recovery(self) -> bool:
        """
        Attempt to step back one degradation level.
        Returns True if recovery was permitted, False if the hold period has not elapsed.
        """
        if self._level == DegradationLevel.NORMAL:
            return True

        if time.time() - self._last_transition < self._recovery_hold_s:
            return False

        prev = self._level
        self._level = DegradationLevel(self._level.value - 1)
        self._last_transition = time.time()

        if self._history:
            self._history[-1].recovered_at = time.time()

        self._audit.log(
            AuditEventType.DEGRADATION_EXITED,
            site_id=self._site_id,
            description=f"Recovered from {prev.name} to {self._level.name}",
            details={"from_level": prev.name, "to_level": self._level.name},
        )
        return True

    def emergency_stop(self, reason: str) -> None:
        """Immediately jump to EMERGENCY_STOP regardless of current level."""
        self.degrade(reason, target=DegradationLevel.EMERGENCY_STOP)

    def is_operational(self) -> bool:
        return self._level in (DegradationLevel.NORMAL, DegradationLevel.REDUCED_SPEED)

    def history(self) -> List[DegradationEvent]:
        return list(self._history)
