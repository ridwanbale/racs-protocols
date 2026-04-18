"""Cross-site workload balancing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class RebalanceCommand:
    from_site: str
    to_site: str
    transfer_fraction: float   # fraction of from_site's load to shift
    rationale: str


class LoadBalancer:
    """
    Identifies load imbalances across sites and generates rebalance commands.

    A site is considered overloaded when its utilisation exceeds ``high_threshold``
    and another site exists below ``low_threshold``.
    """

    def __init__(
        self,
        high_threshold: float = 0.80,
        low_threshold: float = 0.40,
        max_transfer_fraction: float = 0.25,
    ) -> None:
        self._high = high_threshold
        self._low = low_threshold
        self._max_transfer = max_transfer_fraction

    def compute_rebalance(
        self,
        utilisation: Dict[str, float],
        risk_scores: Optional[Dict[str, float]] = None,
        excluded_targets: Optional[List[str]] = None,
    ) -> List[RebalanceCommand]:
        """
        Return a list of transfer commands that would move work from overloaded to underloaded sites.
        """
        risk = risk_scores or {}
        excluded = set(excluded_targets or [])
        commands = []

        overloaded = sorted(
            [(site, u) for site, u in utilisation.items() if u > self._high],
            key=lambda x: -x[1],
        )
        underloaded = sorted(
            [
                (site, u)
                for site, u in utilisation.items()
                if u < self._low and site not in excluded and risk.get(site, 0) < 0.6
            ],
            key=lambda x: x[1],
        )

        if not overloaded or not underloaded:
            return commands

        for over_site, over_util in overloaded:
            for under_site, under_util in underloaded:
                if over_util <= self._high:
                    break
                headroom = self._low - under_util
                if headroom <= 0:
                    continue
                transfer = min(headroom, over_util - self._high, self._max_transfer)
                if transfer <= 0:
                    continue
                commands.append(RebalanceCommand(
                    from_site=over_site,
                    to_site=under_site,
                    transfer_fraction=transfer,
                    rationale=(
                        f"Move {transfer:.0%} load from {over_site} "
                        f"({over_util:.0%} util) to {under_site} ({under_util:.0%} util)"
                    ),
                ))
                over_util -= transfer

        return commands

    def network_balance_score(self, utilisation: Dict[str, float]) -> float:
        """0.0 = perfectly balanced, 1.0 = maximally imbalanced."""
        if len(utilisation) < 2:
            return 0.0
        values = list(utilisation.values())
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return float(min(variance ** 0.5 / 0.5, 1.0))
