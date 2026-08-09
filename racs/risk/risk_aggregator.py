"""Multi-signal risk aggregation across a fleet of sites."""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from .risk_signals import RiskLevel, RiskSignal


class RiskAggregator:
    """Maintains a live snapshot of risk across all registered sites."""

    def __init__(self, staleness_threshold_s: float = 120.0) -> None:
        self._signals: Dict[str, RiskSignal] = {}
        self._staleness_s = staleness_threshold_s

    def update(self, signal: RiskSignal) -> None:
        """Record the latest risk signal for a site."""
        self._signals[signal.site_id] = signal

    def get(self, site_id: str) -> Optional[RiskSignal]:
        return self._signals.get(site_id)

    def all_current(self, now: Optional[float] = None) -> Dict[str, RiskSignal]:
        """Return only non-stale signals."""
        effective_now = time.time() if now is None else now
        cutoff = effective_now - self._staleness_s
        return {sid: sig for sid, sig in self._signals.items() if sig.timestamp >= cutoff}

    def stale_sites(self, now: Optional[float] = None) -> List[str]:
        effective_now = time.time() if now is None else now
        cutoff = effective_now - self._staleness_s
        return [sid for sid, sig in self._signals.items() if sig.timestamp < cutoff]

    def network_risk_level(self, now: Optional[float] = None) -> RiskLevel:
        """Highest risk level currently seen across all active sites."""
        current = self.all_current(now=now)
        if not current:
            return RiskLevel.LOW
        levels = [sig.level for sig in current.values()]
        order = [RiskLevel.CRITICAL, RiskLevel.HIGH, RiskLevel.MEDIUM, RiskLevel.LOW]
        for level in order:
            if level in levels:
                return level
        return RiskLevel.LOW

    def network_composite_score(self, now: Optional[float] = None) -> float:
        current = self.all_current(now=now)
        if not current:
            return 0.0
        return sum(s.composite_score for s in current.values()) / len(current)

    def sites_above_threshold(
        self, threshold: float = 0.6, now: Optional[float] = None
    ) -> List[str]:
        return [
            sid for sid, sig in self.all_current(now=now).items()
            if sig.composite_score >= threshold
        ]

    def summary(self, now: Optional[float] = None) -> dict:
        current = self.all_current(now=now)
        return {
            "active_sites": len(current),
            "stale_sites": len(self.stale_sites(now=now)),
            "network_level": self.network_risk_level(now=now).value,
            "network_composite_score": self.network_composite_score(now=now),
            "sites_above_0.6": self.sites_above_threshold(0.6, now=now),
            "site_levels": {sid: sig.level.value for sid, sig in current.items()},
        }
