"""
Multi-warehouse discrete-event simulation.

Demonstrates RACS preventing cascading failures that would otherwise propagate
across all sites. Run directly: python simulations/warehouse_sim.py
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from racs.agents.network_brain import NetworkBrain
from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import RiskSignal, TelemetryInput


@dataclass
class SimRobot:
    robot_id: str
    site_id: str
    faulted: bool = False
    speed_factor: float = 1.0


@dataclass
class SimSite:
    site_id: str
    robot_count: int = 20
    queue_length: int = 10
    demand_rate: float = 1.0   # multiplier relative to baseline
    robots: List[SimRobot] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.robots = [
            SimRobot(robot_id=f"{self.site_id}_R{i:03d}", site_id=self.site_id)
            for i in range(self.robot_count)
        ]

    def inject_fault(self, count: int = 1) -> List[SimRobot]:
        healthy = [r for r in self.robots if not r.faulted]
        faulted = random.sample(healthy, min(count, len(healthy)))
        for r in faulted:
            r.faulted = True
        return faulted

    def recover_robots(self, count: int = 1) -> None:
        faulted = [r for r in self.robots if r.faulted]
        for r in random.sample(faulted, min(count, len(faulted))):
            r.faulted = False

    def to_telemetry(self) -> TelemetryInput:
        active = sum(1 for r in self.robots if not r.faulted)
        fault_count = sum(1 for r in self.robots if r.faulted)
        throughput = max(0.1, (active / self.robot_count) * self.demand_rate)
        error_rate = fault_count / max(self.robot_count, 1) * 0.5
        return TelemetryInput(
            site_id=self.site_id,
            queue_length=self.queue_length,
            robot_active_count=active,
            robot_fault_count=fault_count,
            throughput_rate=min(throughput, 1.0),
            error_rate_5min=error_rate,
        )


class WarehouseSimulation:
    """
    Simulates a network of warehouses over a number of time steps.

    Supports two modes:
    - with_racs=True: SiteAgents + NetworkBrain coordinate responses
    - with_racs=False: no coordination — faults propagate freely
    """

    def __init__(
        self,
        site_ids: Optional[List[str]] = None,
        with_racs: bool = True,
        seed: int = 42,
    ) -> None:
        random.seed(seed)
        self._with_racs = with_racs
        ids = site_ids or ["SITE_A", "SITE_B", "SITE_C", "SITE_D"]
        self._sites: Dict[str, SimSite] = {sid: SimSite(site_id=sid) for sid in ids}
        self._metrics: List[dict] = []

        if with_racs:
            self._brain = NetworkBrain(on_command=self._handle_brain_command)
            self._agents: Dict[str, SiteAgent] = {
                sid: SiteAgent(
                    AgentConfig(site_id=sid),
                    on_signal_publish=lambda sig: self._brain.ingest_signal(sig),
                )
                for sid in ids
            }
        else:
            self._brain = None
            self._agents = {}

    def _handle_brain_command(self, site_id: str, command: dict) -> None:
        site = self._sites.get(site_id)
        if not site:
            return
        cmd_type = command.get("type", "")
        if cmd_type == "throttle_outflow":
            site.queue_length = max(0, int(site.queue_length * 0.7))
        elif cmd_type == "reduce_intake":
            site.demand_rate = max(0.3, site.demand_rate * 0.8)

    def run(self, steps: int = 50, fault_site: str = "SITE_A", fault_at_step: int = 10) -> List[dict]:
        print(f"\n{'='*60}")
        print(f"Simulation: {'WITH RACS' if self._with_racs else 'WITHOUT RACS'}")
        print(f"Sites: {list(self._sites.keys())} | Steps: {steps}")
        print(f"Fault injection: {fault_site} at step {fault_at_step}")
        print("=" * 60)

        for step in range(steps):
            if step == fault_at_step:
                faulted = self._sites[fault_site].inject_fault(count=5)
                self._sites[fault_site].queue_length += 30
                print(f"\n[Step {step:3d}] FAULT INJECTED at {fault_site}: {len(faulted)} robots")

            step_metrics: dict = {"step": step, "sites": {}}

            for sid, site in self._sites.items():
                telemetry = site.to_telemetry()

                if self._with_racs and sid in self._agents:
                    self._agents[sid].tick(telemetry)

                # Without RACS: simulate cascade manually
                if not self._with_racs and step > fault_at_step and sid != fault_site:
                    if self._sites[fault_site].queue_length > 30:
                        site.queue_length = min(site.queue_length + 3, 100)

                fault_count = sum(1 for r in site.robots if r.faulted)
                throughput = telemetry.throughput_rate
                step_metrics["sites"][sid] = {
                    "queue": site.queue_length,
                    "faults": fault_count,
                    "throughput": round(throughput, 3),
                }

            self._metrics.append(step_metrics)

            if step % 10 == 0:
                self._print_step(step, step_metrics)

            # Gradual queue drain (baseline recovery)
            for site in self._sites.values():
                site.queue_length = max(0, site.queue_length - 2)

        self._print_summary()
        return self._metrics

    def _print_step(self, step: int, metrics: dict) -> None:
        parts = []
        for sid, m in metrics["sites"].items():
            parts.append(f"{sid}: Q={m['queue']:3d} F={m['faults']} T={m['throughput']:.2f}")
        print(f"  Step {step:3d} | " + " | ".join(parts))

    def _print_summary(self) -> None:
        print(f"\n{'='*60}")
        print("SUMMARY")
        for sid in self._sites:
            q_values = [m["sites"][sid]["queue"] for m in self._metrics if sid in m["sites"]]
            f_values = [m["sites"][sid]["faults"] for m in self._metrics if sid in m["sites"]]
            print(f"  {sid}: avg_queue={sum(q_values)/len(q_values):.1f} "
                  f"max_faults={max(f_values)} total_fault_steps={sum(1 for f in f_values if f > 0)}")
        print("=" * 60)


def compare_with_without_racs(steps: int = 60) -> Tuple[List[dict], List[dict]]:
    """Run both modes and return metrics for comparison."""
    print("\nRunning simulation WITHOUT RACS coordination...")
    sim_no_racs = WarehouseSimulation(with_racs=False)
    metrics_no_racs = sim_no_racs.run(steps=steps)

    print("\nRunning simulation WITH RACS coordination...")
    sim_racs = WarehouseSimulation(with_racs=True)
    metrics_racs = sim_racs.run(steps=steps)

    return metrics_no_racs, metrics_racs


if __name__ == "__main__":
    no_racs, with_racs = compare_with_without_racs(steps=60)
