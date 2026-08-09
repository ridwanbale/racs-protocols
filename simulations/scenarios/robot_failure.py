"""Scenario: Single robot failure with coordinated recovery."""

from __future__ import annotations

from dataclasses import replace

from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


SCENARIO_CONFIG = SimulationScenarioConfig(
    steps=40,
    site_ids=("SITE_A", "SITE_B"),
    seed=99,
    fault_site="SITE_A",
    fault_step=8,
    fault_count=1,
)


def run(steps: int = 40, with_racs: bool = True) -> dict:
    """Simulate a single-robot failure and measure recovery time."""
    config = replace(SCENARIO_CONFIG, steps=steps)
    sim = WarehouseSimulation(config=config, with_racs=with_racs)

    metrics = sim.run()

    # Recovery time: steps from fault injection until SITE_A throughput >= 0.75
    recovery_step = None
    for m in metrics:
        if m["step"] < config.fault_step:
            continue
        t = m["sites"].get("SITE_A", {}).get("throughput", 0)
        if t >= 0.75:
            recovery_step = m["step"]
            break

    recovery_time_steps = (recovery_step - config.fault_step) if recovery_step else steps

    return {
        "scenario": "robot_failure",
        "with_racs": with_racs,
        "fault_step": config.fault_step,
        "fault_count": config.fault_count,
        "recovery_time_steps": recovery_time_steps,
        "recovered": recovery_step is not None,
        "metrics": metrics,
    }


if __name__ == "__main__":
    r1 = run(with_racs=False)
    r2 = run(with_racs=True)
    print(f"Without RACS: recovery in {r1['recovery_time_steps']} steps | recovered={r1['recovered']}")
    print(f"With RACS:    recovery in {r2['recovery_time_steps']} steps | recovered={r2['recovered']}")
