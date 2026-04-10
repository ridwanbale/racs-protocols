"""
Full multi-site coordination demo: NetworkBrain + SiteAgents working together.

Run: python examples/multi_site_coordination.py
"""

import time

from racs.agents.network_brain import NetworkBrain
from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import TelemetryInput

SITES = ["ALPHA", "BETA", "GAMMA"]

issued_commands = []

def on_command(site_id: str, command: dict) -> None:
    issued_commands.append((site_id, command))
    print(f"  [NetworkBrain] → {site_id}: {command['type']}")


# Initialise brain and agents
brain = NetworkBrain(on_command=on_command)
agents = {
    sid: SiteAgent(
        AgentConfig(site_id=sid),
        on_signal_publish=lambda sig: brain.ingest_signal(sig),
    )
    for sid in SITES
}
for sid in SITES:
    brain.update_utilisation(sid, 0.5)

print("=" * 60)
print("Multi-Site RACS Coordination Demo")
print("=" * 60)

# Simulate 3 rounds of telemetry, escalating stress at ALPHA
rounds = [
    {"ALPHA": (15, 18, 0, 0.85, 0.02), "BETA": (5, 20, 0, 0.90, 0.01), "GAMMA": (3, 20, 0, 0.92, 0.01)},
    {"ALPHA": (40, 14, 3, 0.55, 0.10), "BETA": (8, 19, 0, 0.88, 0.02), "GAMMA": (5, 20, 0, 0.91, 0.01)},
    {"ALPHA": (65, 10, 6, 0.30, 0.22), "BETA": (12, 18, 1, 0.80, 0.04), "GAMMA": (6, 20, 0, 0.89, 0.01)},
]

for round_num, telemetries in enumerate(rounds, 1):
    print(f"\n--- Round {round_num} ---")
    for sid, (queue, active, faults, throughput, errors) in telemetries.items():
        t = TelemetryInput(
            site_id=sid,
            queue_length=queue,
            robot_active_count=active,
            robot_fault_count=faults,
            throughput_rate=throughput,
            error_rate_5min=errors,
        )
        # Force publish by resetting last_publish time
        agents[sid]._last_publish = 0.0
        signal = agents[sid].tick(t)
        if signal:
            print(f"  {sid}: {signal.level.value} (score={signal.composite_score:.2f}, "
                  f"congestion={signal.congestion_probability:.2f}, "
                  f"failure={signal.failure_likelihood:.2f})")

    brain.tick_recovery_plans()

print("\n" + "=" * 60)
print("Network Summary")
print("=" * 60)
summary = brain.network_summary()
print(f"  Network risk level:    {summary['aggregator']['network_level']}")
print(f"  Network score:         {summary['aggregator']['network_composite_score']:.3f}")
print(f"  Active recovery plans: {summary['active_recovery_plans']}")
print(f"  Total commands issued: {len(issued_commands)}")
print(f"  Total audit events:    {summary['total_audit_events']}")
