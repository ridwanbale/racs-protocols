"""Dynamic task redistribution across sites."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class TaskPriority(Enum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class Task:
    task_id: str
    origin_site: str
    task_type: str
    priority: TaskPriority = TaskPriority.NORMAL
    estimated_duration_s: float = 60.0
    payload: dict = field(default_factory=dict)
    assigned_site: Optional[str] = None
    created_at: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class AllocationResult:
    task: Task
    assigned_to: str
    rationale: str
    confidence: float = 1.0


class TaskAllocator:
    """
    Distributes tasks across sites based on capacity, risk, and priority.

    Uses a greedy best-fit heuristic that respects safety-gated site availability.
    """

    def allocate(
        self,
        task: Task,
        site_capacities: Dict[str, float],   # site_id -> available capacity 0.0–1.0
        site_risk_scores: Dict[str, float],  # site_id -> composite risk 0.0–1.0
        excluded_sites: Optional[List[str]] = None,
    ) -> Optional[AllocationResult]:
        excluded = set(excluded_sites or [])
        excluded.add(task.origin_site)  # don't allocate back to origin under redistribution

        candidates = {
            site: capacity
            for site, capacity in site_capacities.items()
            if site not in excluded and capacity > 0.1
        }

        if not candidates:
            return None

        # Score each candidate: higher capacity + lower risk = better fit
        def score(site: str) -> float:
            cap = candidates[site]
            risk = site_risk_scores.get(site, 0.5)
            return cap * (1.0 - risk)

        best_site = max(candidates, key=score)
        return AllocationResult(
            task=task,
            assigned_to=best_site,
            rationale=(
                f"Selected {best_site} (capacity={candidates[best_site]:.0%}, "
                f"risk={site_risk_scores.get(best_site, 0):.2f}, score={score(best_site):.2f})"
            ),
            confidence=score(best_site),
        )

    def bulk_redistribute(
        self,
        tasks: List[Task],
        site_capacities: Dict[str, float],
        site_risk_scores: Dict[str, float],
        excluded_sites: Optional[List[str]] = None,
    ) -> List[AllocationResult]:
        """Allocate multiple tasks, updating running capacity estimates as we go."""
        running_capacity = dict(site_capacities)
        results = []

        for task in sorted(tasks, key=lambda t: t.priority.value, reverse=True):
            result = self.allocate(task, running_capacity, site_risk_scores, excluded_sites)
            if result:
                # Reduce estimated capacity of chosen site
                load_fraction = min(task.estimated_duration_s / 600.0, 0.1)
                running_capacity[result.assigned_to] = max(
                    running_capacity[result.assigned_to] - load_fraction, 0.0
                )
                results.append(result)
        return results
