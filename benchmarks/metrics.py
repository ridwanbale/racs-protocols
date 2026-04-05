"""Standardised RACS benchmark metric definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BenchmarkResult:
    scenario: str
    with_racs: bool

    # Core metrics
    recovery_time_steps: Optional[float] = None     # steps from fault to throughput >= 0.75
    cascade_containment_radius: Optional[int] = None  # sites affected during cascade
    prevention_rate: Optional[float] = None           # fraction of potential cascades prevented
    safety_violation_count: int = 0                   # hard constraint breaches
    avg_throughput_post_fault: Optional[float] = None
    peak_queue_depth: Optional[int] = None
    human_override_latency_s: Optional[float] = None

    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "with_racs": self.with_racs,
            "recovery_time_steps": self.recovery_time_steps,
            "cascade_containment_radius": self.cascade_containment_radius,
            "prevention_rate": self.prevention_rate,
            "safety_violation_count": self.safety_violation_count,
            "avg_throughput_post_fault": self.avg_throughput_post_fault,
            "peak_queue_depth": self.peak_queue_depth,
            "human_override_latency_s": self.human_override_latency_s,
            **self.metadata,
        }


def compute_recovery_time(metrics: List[dict], fault_step: int, site_id: str, threshold: float = 0.75) -> Optional[float]:
    """Steps from fault injection until site throughput recovers to >= threshold."""
    for m in metrics:
        if m["step"] < fault_step:
            continue
        t = m["sites"].get(site_id, {}).get("throughput", 0)
        if t >= threshold:
            return float(m["step"] - fault_step)
    return None


def compute_cascade_radius(metrics: List[dict], fault_step: int, fault_site: str, affected_threshold: float = 0.1) -> int:
    """Number of non-fault sites that experienced >affected_threshold queue increase post-fault."""
    pre = next((m for m in metrics if m["step"] == fault_step - 1), None)
    if not pre:
        return 0

    post_peak = {}
    for m in metrics:
        if m["step"] < fault_step:
            continue
        for sid, s in m["sites"].items():
            if sid == fault_site:
                continue
            post_peak[sid] = max(post_peak.get(sid, 0), s.get("queue", 0))

    affected = 0
    for sid, peak_q in post_peak.items():
        baseline_q = pre["sites"].get(sid, {}).get("queue", 0)
        if peak_q > baseline_q + affected_threshold * 100:
            affected += 1
    return affected


def compute_avg_throughput(metrics: List[dict], start_step: int, site_ids: List[str]) -> float:
    post = [m for m in metrics if m["step"] >= start_step]
    if not post:
        return 0.0
    totals = []
    for m in post:
        t_vals = [m["sites"].get(sid, {}).get("throughput", 0) for sid in site_ids if sid in m["sites"]]
        if t_vals:
            totals.append(sum(t_vals) / len(t_vals))
    return sum(totals) / len(totals) if totals else 0.0
