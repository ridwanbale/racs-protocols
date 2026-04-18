"""Staged recovery orchestration after a fault or degradation event."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RecoveryStage(Enum):
    ASSESS = "ASSESS"              # Evaluate scope of fault
    ISOLATE = "ISOLATE"            # Prevent fault propagation
    REDISTRIBUTE = "REDISTRIBUTE"  # Move work away from affected site
    STABILISE = "STABILISE"        # Confirm fault is contained
    RESTORE = "RESTORE"            # Gradually return to normal ops
    COMPLETE = "COMPLETE"          # Recovery finished


@dataclass
class RecoveryStep:
    stage: RecoveryStage
    description: str
    hold_duration_s: float = 30.0   # minimum time to hold before advancing
    completed: bool = False
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def start(self) -> None:
        self.started_at = time.time()

    def complete(self) -> None:
        self.completed = True
        self.completed_at = time.time()

    def ready_to_advance(self) -> bool:
        if not self.started_at:
            return False
        return time.time() - self.started_at >= self.hold_duration_s


@dataclass
class RecoveryPlan:
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    site_id: str = ""
    fault_description: str = ""
    steps: List[RecoveryStep] = field(default_factory=list)
    current_step_index: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    @property
    def current_step(self) -> Optional[RecoveryStep]:
        if self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.current_step_index >= len(self.steps)

    def advance(self) -> Optional[RecoveryStep]:
        """Move to the next step if the current step is ready. Returns the new step or None."""
        step = self.current_step
        if step is None:
            return None
        if not step.ready_to_advance():
            return None
        step.complete()
        self.current_step_index += 1
        if self.is_complete:
            self.completed_at = time.time()
            return None
        next_step = self.current_step
        if next_step:
            next_step.start()
        return next_step

    def total_duration_s(self) -> Optional[float]:
        if self.completed_at:
            return self.completed_at - self.created_at
        return None


class RecoveryPlanner:
    """Generates staged recovery plans appropriate to the fault type and severity."""

    def plan_robot_fault(self, site_id: str, fault_count: int) -> RecoveryPlan:
        plan = RecoveryPlan(site_id=site_id, fault_description=f"{fault_count} robot fault(s)")
        plan.steps = [
            RecoveryStep(RecoveryStage.ASSESS, "Identify faulted robots and halt their tasks", hold_duration_s=15),
            RecoveryStep(RecoveryStage.ISOLATE, "Reroute traffic away from faulted robots", hold_duration_s=20),
            RecoveryStep(RecoveryStage.REDISTRIBUTE, "Assign pending tasks to healthy robots or peer sites", hold_duration_s=30),
            RecoveryStep(RecoveryStage.STABILISE, "Monitor throughput and fault rate for stability", hold_duration_s=60),
            RecoveryStep(RecoveryStage.RESTORE, "Gradually reintroduce repaired robots", hold_duration_s=30),
            RecoveryStep(RecoveryStage.COMPLETE, "Recovery complete — resume normal scheduling", hold_duration_s=0),
        ]
        if plan.steps:
            plan.steps[0].start()
        return plan

    def plan_cascade_containment(self, site_id: str, affected_sites: List[str]) -> RecoveryPlan:
        plan = RecoveryPlan(
            site_id=site_id,
            fault_description=f"Cascade from {site_id} threatening {', '.join(affected_sites)}",
        )
        plan.steps = [
            RecoveryStep(RecoveryStage.ASSESS, "Measure cascade spread across network", hold_duration_s=10),
            RecoveryStep(RecoveryStage.ISOLATE, "Throttle outgoing task flow from origin site", hold_duration_s=30),
            RecoveryStep(RecoveryStage.REDISTRIBUTE, "Pre-emptively balance load away from high-risk sites", hold_duration_s=45),
            RecoveryStep(RecoveryStage.STABILISE, "Confirm cascade is contained to origin site", hold_duration_s=90),
            RecoveryStep(RecoveryStage.RESTORE, "Incrementally restore normal inter-site flows", hold_duration_s=60),
            RecoveryStep(RecoveryStage.COMPLETE, "Network restored to normal operating state", hold_duration_s=0),
        ]
        if plan.steps:
            plan.steps[0].start()
        return plan
