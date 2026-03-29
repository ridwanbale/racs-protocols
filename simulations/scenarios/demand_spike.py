"""Scenario: Sudden demand surge requiring cross-site load balancing."""

from __future__ import annotations

from simulations.warehouse_sim import WarehouseSimulation


def run(steps: int = 50, with_racs: bool = True) -> dict:
    """Simulate a 2x demand spike at SITE_A and measure throughput maintenance."""
    sites = ["SITE_A", "SITE_B", "SITE_C", "SITE_D"]
    sim = WarehouseSimulation(site_ids=sites, with_racs=with_racs, seed=77)

    # Spike demand at SITE_A
    sim._sites["SITE_A"].demand_rate = 2.0
    sim._sites["SITE_A"].queue_length = 50

    metrics = sim.run(steps=steps, fault_site="SITE_A", fault_at_step=15)

    # Measure average throughput across all sites after the spike
    post_spike = [m for m in metrics if m["step"] >= 15]
    avg_throughput = sum(
        sum(m["sites"][sid]["throughput"] for sid in sites if sid in m["sites"]) / len(sites)
        for m in post_spike
    ) / max(len(post_spike), 1)

    return {
        "scenario": "demand_spike",
        "with_racs": with_racs,
        "avg_network_throughput_post_spike": round(avg_throughput, 3),
        "metrics": metrics,
    }


if __name__ == "__main__":
    r1 = run(with_racs=False)
    r2 = run(with_racs=True)
    print(f"Without RACS: avg throughput post-spike = {r1['avg_network_throughput_post_spike']:.3f}")
    print(f"With RACS:    avg throughput post-spike = {r2['avg_network_throughput_post_spike']:.3f}")
