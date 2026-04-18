"""Terminal-based simulation visualizer."""

from __future__ import annotations

from typing import Dict, List


def render_step(step: int, site_metrics: Dict[str, dict], width: int = 60) -> str:
    """Render a single simulation step as a formatted terminal string."""
    lines = [f"Step {step:4d} {'─' * (width - 9)}"]
    for sid, m in site_metrics.items():
        queue = m.get("queue", 0)
        faults = m.get("faults", 0)
        throughput = m.get("throughput", 1.0)
        bar_len = int(throughput * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        fault_indicator = f" ⚠ {faults} fault(s)" if faults else ""
        lines.append(f"  {sid:8s} [{bar}] {throughput:.0%}  Q={queue:3d}{fault_indicator}")
    return "\n".join(lines)


def print_comparison(
    metrics_no_racs: List[dict],
    metrics_racs: List[dict],
    site_id: str = "SITE_A",
) -> None:
    """Print a side-by-side throughput comparison for a specific site."""
    print(f"\n{'Step':>6}  {'Without RACS':>14}  {'With RACS':>12}")
    print("─" * 38)
    for m_no, m_yes in zip(metrics_no_racs, metrics_racs):
        step = m_no["step"]
        if step % 5 != 0:
            continue
        t_no = m_no["sites"].get(site_id, {}).get("throughput", 0)
        t_yes = m_yes["sites"].get(site_id, {}).get("throughput", 0)
        delta = t_yes - t_no
        sign = "+" if delta >= 0 else ""
        print(f"{step:>6}  {t_no:>14.3f}  {t_yes:>12.3f}  ({sign}{delta:.3f})")


def print_cascade_comparison(metrics_no_racs: List[dict], metrics_racs: List[dict]) -> None:
    """Show aggregate queue depth across all sites for both modes."""
    print(f"\n{'Step':>6}  {'Total Queue (no RACS)':>22}  {'Total Queue (with RACS)':>24}")
    print("─" * 56)
    for m_no, m_yes in zip(metrics_no_racs, metrics_racs):
        step = m_no["step"]
        if step % 10 != 0:
            continue
        q_no = sum(v.get("queue", 0) for v in m_no["sites"].values())
        q_yes = sum(v.get("queue", 0) for v in m_yes["sites"].values())
        print(f"{step:>6}  {q_no:>22}  {q_yes:>24}")
