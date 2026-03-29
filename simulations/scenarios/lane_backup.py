"""Scenario: Lane congestion at one facility cascading to peer facilities."""

from __future__ import annotations

from simulations.warehouse_sim import WarehouseSimulation


def run(steps: int = 50, with_racs: bool = True) -> dict:
    """
    Simulate a lane backup at SITE_A that threatens to overflow into SITE_B and SITE_C.

    Returns summary metrics.
    """
    sites = ["SITE_A", "SITE_B", "SITE_C"]
    sim = WarehouseSimulation(site_ids=sites, with_racs=with_racs, seed=10)

    # Pre-load SITE_A with a high queue to simulate lane backup
    sim._sites["SITE_A"].queue_length = 60
    sim._sites["SITE_A"].demand_rate = 1.5

    metrics = sim.run(steps=steps, fault_site="SITE_A", fault_at_step=5)

    peak_cascade = max(
        m["sites"].get("SITE_B", {}).get("queue", 0) +
        m["sites"].get("SITE_C", {}).get("queue", 0)
        for m in metrics
    )

    return {
        "scenario": "lane_backup",
        "with_racs": with_racs,
        "steps": steps,
        "peak_cascade_queue_BC": peak_cascade,
        "metrics": metrics,
    }


if __name__ == "__main__":
    print("--- Without RACS ---")
    r1 = run(with_racs=False)
    print(f"Peak cascade queue (B+C): {r1['peak_cascade_queue_BC']}")

    print("\n--- With RACS ---")
    r2 = run(with_racs=True)
    print(f"Peak cascade queue (B+C): {r2['peak_cascade_queue_BC']}")
