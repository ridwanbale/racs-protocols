"""Scenario: Sudden demand surge requiring cross-site load balancing."""

from __future__ import annotations

from dataclasses import replace

from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


SCENARIO_CONFIG = SimulationScenarioConfig(
    steps=50,
    site_ids=("SITE_A", "SITE_B", "SITE_C", "SITE_D"),
    seed=77,
    fault_site="SITE_A",
    fault_step=15,
    fault_count=0,
    initial_site_queue={"SITE_A": 50},
    initial_site_demand={"SITE_A": 2.0},
)


def run(steps: int = 50, with_racs: bool = True) -> dict:
    """Simulate a 2x demand spike at SITE_A and measure throughput maintenance."""
    sites = list(SCENARIO_CONFIG.site_ids)
    config = replace(SCENARIO_CONFIG, steps=steps)
    sim = WarehouseSimulation(config=config, with_racs=with_racs)

    metrics = sim.run()

    # Measure average throughput across all sites after the spike
    post_spike = [m for m in metrics if m["step"] >= config.fault_step]
    avg_throughput = sum(
        sum(m["sites"][sid]["throughput"] for sid in sites if sid in m["sites"]) / len(sites)
        for m in post_spike
    ) / max(len(post_spike), 1)

    return {
        "scenario": "demand_spike",
        "with_racs": with_racs,
        "fault_step": config.fault_step,
        "fault_count": config.fault_count,
        "avg_network_throughput_post_spike": round(avg_throughput, 3),
        "metrics": metrics,
    }


if __name__ == "__main__":
    r1 = run(with_racs=False)
    r2 = run(with_racs=True)
    print(f"Without RACS: avg throughput post-spike = {r1['avg_network_throughput_post_spike']:.3f}")
    print(f"With RACS:    avg throughput post-spike = {r2['avg_network_throughput_post_spike']:.3f}")
