"""Cascading failure detection across multiple sites."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .risk_signals import RiskLevel, RiskSignal


@dataclass
class CascadeAlert:
    """Signals a potential cascading failure across sites."""

    origin_site: str
    affected_sites: List[str]
    cascade_probability: float
    estimated_impact_radius: int        # number of sites likely to be affected
    alert_id: str = field(default_factory=lambda: str(__import__("uuid").uuid4()))
    timestamp: float = field(default_factory=time.time)
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "origin_site": self.origin_site,
            "affected_sites": self.affected_sites,
            "cascade_probability": self.cascade_probability,
            "estimated_impact_radius": self.estimated_impact_radius,
            "timestamp": self.timestamp,
            "description": self.description,
        }


class CascadeDetector:
    """Detects cross-site cascade risk from a collection of site risk signals."""

    def __init__(
        self,
        cascade_threshold: float = 0.65,
        signal_window_s: float = 60.0,
        min_sites_for_cascade: int = 2,
    ) -> None:
        self._cascade_threshold = cascade_threshold
        self._signal_window_s = signal_window_s
        self._min_sites = min_sites_for_cascade
        self._signal_history: Dict[str, List[RiskSignal]] = {}

    def ingest(self, signal: RiskSignal, now: Optional[float] = None) -> None:
        """Record a new risk signal, pruning signals older than the window."""
        history = self._signal_history.setdefault(signal.site_id, [])
        effective_now = time.time() if now is None else now
        cutoff = effective_now - self._signal_window_s
        self._signal_history[signal.site_id] = [s for s in history if s.timestamp >= cutoff]
        self._signal_history[signal.site_id].append(signal)

    def detect(self, current_signals: Dict[str, RiskSignal]) -> Optional[CascadeAlert]:
        """
        Evaluate whether a cascading failure is developing.
        Returns a CascadeAlert if cascade probability exceeds threshold.
        """
        elevated = {
            site: sig for site, sig in current_signals.items()
            if sig.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        }

        if len(elevated) < self._min_sites:
            return None

        scores = [sig.composite_score for sig in elevated.values()]
        avg_score = sum(scores) / len(scores)

        # Cascade probability rises with number of elevated sites and average severity
        site_factor = min(len(elevated) / max(len(current_signals), 1), 1.0)
        cascade_prob = float(avg_score * (0.5 + 0.5 * site_factor))

        if cascade_prob < self._cascade_threshold:
            return None

        # The origin is the site with the highest composite score
        origin = max(elevated, key=lambda s: elevated[s].composite_score)
        affected = [s for s in elevated if s != origin]

        return CascadeAlert(
            origin_site=origin,
            affected_sites=affected,
            cascade_probability=cascade_prob,
            estimated_impact_radius=len(elevated),
            description=(
                f"Cascade risk {cascade_prob:.0%} — {len(elevated)} sites at HIGH/CRITICAL. "
                f"Origin: {origin}. Affected: {', '.join(affected) or 'none yet'}."
            ),
        )

    def trend_for_site(self, site_id: str) -> float:
        """Return the slope of risk scores over the signal window (positive = worsening)."""
        history = self._signal_history.get(site_id, [])
        if len(history) < 2:
            return 0.0
        scores = [s.composite_score for s in history]
        n = len(scores)
        x_mean = (n - 1) / 2.0
        y_mean = sum(scores) / n
        numerator = sum((i - x_mean) * (scores[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator else 0.0
