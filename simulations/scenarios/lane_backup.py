"""Scenario: Lane congestion at one facility cascading to peer facilities."""

from __future__ import annotations

from dataclasses import replace

from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


SCENARIO_CONFIG = SimulationScenarioConfig(
    steps=50,
    site_ids=("SITE_A", "SITE_B", "SITE_C"),
    seed=10,
    fault_site="SITE_A",
    fault_step=5,
    fault_count=0,
    initial_site_queue={"SITE_A": 60},
    initial_site_demand={"SITE_A": 1.5},
)


def run(steps: int = 50, with_racs: bool = True) -> dict:
    """
    Simulate a lane backup at SITE_A that threatens to overflow into SITE_B and SITE_C.

    Returns summary metrics.
    """
    sites = list(SCENARIO_CONFIG.site_ids)
    config = replace(SCENARIO_CONFIG, steps=steps)
    sim = WarehouseSimulation(config=config, with_racs=with_racs)

    metrics = sim.run()

    peak_cascade = max(
        m["sites"].get("SITE_B", {}).get("queue", 0) +
        m["sites"].get("SITE_C", {}).get("queue", 0)
        for m in metrics
    )

    return {
        "scenario": "lane_backup",
        "with_racs": with_racs,
        "steps": steps,
        "fault_step": config.fault_step,
        "fault_count": config.fault_count,
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
