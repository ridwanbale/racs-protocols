"""Scenario: Communication failure between sites — tests decentralised resilience."""

from __future__ import annotations

from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import TelemetryInput
from simulations.warehouse_sim import SimSite


def run(steps: int = 30) -> dict:
    """
    Simulate NetworkBrain going offline. Each SiteAgent must continue safely in autonomous mode.
    Returns per-site degradation levels and whether any safety violations occurred.
    """
    sites = {sid: SimSite(site_id=sid) for sid in ["SITE_A", "SITE_B", "SITE_C"]}
    agents = {
        sid: SiteAgent(AgentConfig(site_id=sid))
        for sid in sites
    }

    # Disconnect all agents from the brain at step 5
    disconnected_at = 5
    results = []

    for step in range(steps):
        step_data: dict = {"step": step, "sites": {}}

        for sid, site in sites.items():
            telemetry = site.to_telemetry()
            agent = agents[sid]

            if step == disconnected_at:
                agent.mark_disconnected()

            if step == disconnected_at + 15:
                agent.mark_connected()

            agent.tick(telemetry)
            step_data["sites"][sid] = {
                "degradation_level": agent.degradation_level.name,
                "operational": agent.is_operational,
                "connected": agent._connected,
            }

        results.append(step_data)

    # Check: after disconnect, agents should be degraded but operational (not emergency stopped)
    post_disconnect = [r for r in results if r["step"] >= disconnected_at]
    any_emergency_stop = any(
        r["sites"][sid]["degradation_level"] == "EMERGENCY_STOP"
        for r in post_disconnect
        for sid in sites
    )

    return {
        "scenario": "network_partition",
        "disconnected_at_step": disconnected_at,
        "reconnected_at_step": disconnected_at + 15,
        "any_emergency_stop_during_partition": any_emergency_stop,
        "all_sites_degraded_gracefully": not any_emergency_stop,
        "steps": results,
    }


if __name__ == "__main__":
    result = run()
    print(f"Network partition scenario complete.")
    print(f"Emergency stops: {result['any_emergency_stop_during_partition']}")
    print(f"Graceful degradation: {result['all_sites_degraded_gracefully']}")
