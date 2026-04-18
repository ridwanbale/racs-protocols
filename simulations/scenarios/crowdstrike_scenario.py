"""
Scenario: Centralised update failure (inspired by the July 2024 CrowdStrike incident).

A faulty configuration update is broadcast to all sites simultaneously.
- Without RACS: all sites fail in sync (centralised failure)
- With RACS: decentralised agents detect the anomaly via the SafetyGate and reject
  the update, continuing operation in safe mode.
"""

from __future__ import annotations

from typing import Dict, List

from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import TelemetryInput
from racs.safety.constraints import SafetyConfig, SafetyGate
from simulations.warehouse_sim import SimSite


FAULTY_UPDATE = {
    "type": "apply_config_update",
    "robot_speed_ms": 5.0,             # exceeds hard limit of 2.0 m/s
    "target_site_robot_density": 0.99,  # exceeds safe density limit
    "target_fault_ratio": 0.0,
    "risk_score": 0.0,
    "update_id": "config-v2024-07-19",
}


def run_without_racs(sites: List[str]) -> dict:
    """No safety gate — all sites blindly apply the faulty update."""
    results = {}
    for sid in sites:
        results[sid] = {
            "received_update": True,
            "applied": True,
            "failed": True,
            "reason": "No safety gate — faulty config applied without validation",
        }
    failed_count = len(sites)
    print(f"\n[WITHOUT RACS] All {failed_count} sites failed simultaneously.")
    return {"scenario": "crowdstrike", "with_racs": False, "sites_failed": failed_count, "results": results}


def run_with_racs(sites: List[str]) -> dict:
    """Each agent passes the update through its SafetyGate — faulty updates are blocked."""
    results = {}
    site_objs = {sid: SimSite(site_id=sid) for sid in sites}
    agents = {sid: SiteAgent(AgentConfig(site_id=sid)) for sid in sites}

    # Give each agent one tick to establish baseline
    for sid, site in site_objs.items():
        agents[sid].tick(site.to_telemetry())

    # Broadcast the faulty update to all agents
    failed_count = 0
    for sid, agent in agents.items():
        execution_result = agent.execute_command(dict(FAULTY_UPDATE))  # copy so it's not mutated
        blocked = execution_result["status"] in ("blocked", "pending_human_approval")
        if not blocked:
            failed_count += 1
        results[sid] = {
            "received_update": True,
            "applied": not blocked,
            "failed": not blocked,
            "status": execution_result["status"],
            "reason": execution_result.get("reason", ""),
        }

    print(f"\n[WITH RACS] {failed_count}/{len(sites)} sites failed. "
          f"{len(sites) - failed_count}/{len(sites)} blocked the faulty update.")

    return {
        "scenario": "crowdstrike",
        "with_racs": True,
        "sites_failed": failed_count,
        "sites_protected": len(sites) - failed_count,
        "results": results,
    }


if __name__ == "__main__":
    site_list = ["SITE_A", "SITE_B", "SITE_C", "SITE_D", "SITE_E"]

    print("=" * 60)
    print("CrowdStrike-Inspired Cascading Update Failure Scenario")
    print("=" * 60)

    no_racs = run_without_racs(site_list)
    racs = run_with_racs(site_list)

    print(f"\nResult: Without RACS — {no_racs['sites_failed']}/{len(site_list)} sites crashed")
    print(f"Result: With RACS    — {racs['sites_failed']}/{len(site_list)} sites crashed "
          f"({racs['sites_protected']} protected by SafetyGate)")
