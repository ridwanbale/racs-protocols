"""Scenario: Single robot failure with coordinated recovery."""

from __future__ import annotations

from simulations.warehouse_sim import WarehouseSimulation


def run(steps: int = 40, with_racs: bool = True) -> dict:
    """Simulate a single-robot failure and measure recovery time."""
    sites = ["SITE_A", "SITE_B"]
    sim = WarehouseSimulation(site_ids=sites, with_racs=with_racs, seed=99)

    # Inject only 1 fault (single robot failure)
    metrics = sim.run(steps=steps, fault_site="SITE_A", fault_at_step=8)

    # Recovery time: steps from fault injection until SITE_A throughput >= 0.75
    recovery_step = None
    for m in metrics:
        if m["step"] < 8:
            continue
        t = m["sites"].get("SITE_A", {}).get("throughput", 0)
        if t >= 0.75:
            recovery_step = m["step"]
            break

    recovery_time_steps = (recovery_step - 8) if recovery_step else steps

    return {
        "scenario": "robot_failure",
        "with_racs": with_racs,
        "recovery_time_steps": recovery_time_steps,
        "recovered": recovery_step is not None,
        "metrics": metrics,
    }


if __name__ == "__main__":
    r1 = run(with_racs=False)
    r2 = run(with_racs=True)
    print(f"Without RACS: recovery in {r1['recovery_time_steps']} steps | recovered={r1['recovered']}")
    print(f"With RACS:    recovery in {r2['recovery_time_steps']} steps | recovered={r2['recovered']}")
