"""
Multi-warehouse discrete-event simulation.

Demonstrates RACS preventing cascading failures that would otherwise propagate
across all sites. Run directly: python simulations/warehouse_sim.py
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Mapping, Optional, Tuple

from racs.agents.network_brain import NetworkBrain
from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import RiskSignal, TelemetryInput


@dataclass(frozen=True)
class SimulationScenarioConfig:
    steps: int = 50
    step_duration_s: float = 1.0
    site_ids: tuple[str, ...] = ("SITE_A", "SITE_B", "SITE_C", "SITE_D")
    robot_count: int = 20
    seed: int = 42
    fault_site: str = "SITE_A"
    fault_step: int = 10
    fault_count: int = 5
    fault_robot_ids: Optional[tuple[str, ...]] = None
    initial_site_queue: Mapping[str, int] = field(default_factory=dict)
    initial_site_demand: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.steps <= 0:
            raise ValueError("steps must be greater than 0")
        if self.step_duration_s <= 0:
            raise ValueError("step_duration_s must be greater than 0")
        if self.robot_count <= 0:
            raise ValueError("robot_count must be greater than 0")
        if self.fault_count < 0:
            raise ValueError("fault_count must be greater than or equal to 0")
        if not 0 <= self.fault_step < self.steps:
            raise ValueError("fault_step must satisfy 0 <= fault_step < steps")
        if self.fault_site not in self.site_ids:
            raise ValueError("fault_site must exist in site_ids")
        if self.fault_count > self.robot_count:
            raise ValueError("fault_count cannot exceed robots available at fault_site")
        if self.fault_robot_ids is not None and len(self.fault_robot_ids) != self.fault_count:
            raise ValueError("fault_robot_ids count must match fault_count")
        if self.fault_robot_ids is not None and len(set(self.fault_robot_ids)) != len(self.fault_robot_ids):
            raise ValueError("fault_robot_ids cannot contain duplicate robot IDs")

        valid_sites = set(self.site_ids)
        for site_id, queue in self.initial_site_queue.items():
            if site_id not in valid_sites:
                raise ValueError("initial_site_queue keys must exist in site_ids")
            if queue < 0:
                raise ValueError("initial_site_queue values cannot be negative")
        for site_id, demand in self.initial_site_demand.items():
            if site_id not in valid_sites:
                raise ValueError("initial_site_demand keys must exist in site_ids")
            if demand < 0:
                raise ValueError("initial_site_demand values cannot be negative")


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

    def inject_fault(self, count: int = 1, rng: Optional[random.Random] = None) -> List[SimRobot]:
        if count == 0:
            return []
        healthy = [r for r in self.robots if not r.faulted]
        sampler = rng if rng is not None else random
        faulted = sampler.sample(healthy, count)
        for r in faulted:
            r.faulted = True
        return faulted

    def inject_fault_by_ids(self, robot_ids: tuple[str, ...]) -> List[SimRobot]:
        robots_by_id = {r.robot_id: r for r in self.robots}
        missing = [robot_id for robot_id in robot_ids if robot_id not in robots_by_id]
        if missing:
            raise ValueError(
                f"fault_robot_ids must exist at {self.site_id}: {', '.join(missing)}"
            )

        faulted = [robots_by_id[robot_id] for robot_id in robot_ids]
        for robot in faulted:
            robot.faulted = True
        return faulted

    def recover_robots(self, count: int = 1) -> None:
        faulted = [r for r in self.robots if r.faulted]
        for r in random.sample(faulted, min(count, len(faulted))):
            r.faulted = False

    def to_telemetry(self, timestamp: Optional[float] = None) -> TelemetryInput:
        active = sum(1 for r in self.robots if not r.faulted)
        fault_count = sum(1 for r in self.robots if r.faulted)
        throughput = max(0.1, (active / self.robot_count) * self.demand_rate)
        error_rate = fault_count / max(self.robot_count, 1) * 0.5
        telemetry = TelemetryInput(
            site_id=self.site_id,
            queue_length=self.queue_length,
            robot_active_count=active,
            robot_fault_count=fault_count,
            throughput_rate=min(throughput, 1.0),
            error_rate_5min=error_rate,
        )
        if timestamp is not None:
            telemetry.timestamp = timestamp
        return telemetry


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
        step_duration_s: float = 1.0,
        config: Optional[SimulationScenarioConfig] = None,
    ) -> None:
        if config is None:
            config = SimulationScenarioConfig(
                site_ids=tuple(site_ids) if site_ids is not None else SimulationScenarioConfig.site_ids,
                seed=seed,
                step_duration_s=step_duration_s,
            )

        self._with_racs = with_racs
        self._config = config
        self._rng = random.Random(config.seed)
        self._step_duration_s = config.step_duration_s
        ids = config.site_ids
        self._sites: Dict[str, SimSite] = {
            sid: SimSite(
                site_id=sid,
                robot_count=config.robot_count,
                queue_length=config.initial_site_queue.get(sid, 10),
                demand_rate=config.initial_site_demand.get(sid, 1.0),
            )
            for sid in ids
        }
        self._metrics: List[dict] = []

        if with_racs:
            self._brain = NetworkBrain(on_command=self._handle_brain_command)
            self._agents: Dict[str, SiteAgent] = {
                sid: SiteAgent(
                    AgentConfig(site_id=sid),
                    on_signal_publish=lambda sig: self._brain.ingest_signal(
                        sig, now=sig.timestamp
                    ),
                )
                for sid in ids
            }
        else:
            self._brain = None
            self._agents = {}

    def _validate_fault_robot_ids(self, config: SimulationScenarioConfig) -> None:
        if config.fault_robot_ids is None:
            return
        robots_by_id = {robot.robot_id for robot in self._sites[config.fault_site].robots}
        missing = [
            robot_id for robot_id in config.fault_robot_ids
            if robot_id not in robots_by_id
        ]
        if missing:
            raise ValueError(
                f"fault_robot_ids must exist at {config.fault_site}: {', '.join(missing)}"
            )

    def _handle_brain_command(self, site_id: str, command: dict) -> None:
        site = self._sites.get(site_id)
        if not site:
            return
        cmd_type = command.get("type", "")
        if cmd_type == "throttle_outflow":
            site.queue_length = max(0, int(site.queue_length * 0.7))
        elif cmd_type == "reduce_intake":
            site.demand_rate = max(0.3, site.demand_rate * 0.8)

    def run(
        self,
        steps: Optional[int] = None,
        fault_site: Optional[str] = None,
        fault_at_step: Optional[int] = None,
        fault_count: Optional[int] = None,
        fault_robot_ids: Optional[tuple[str, ...]] = None,
    ) -> List[dict]:
        config = replace(
            self._config,
            steps=self._config.steps if steps is None else steps,
            fault_site=self._config.fault_site if fault_site is None else fault_site,
            fault_step=self._config.fault_step if fault_at_step is None else fault_at_step,
            fault_count=self._config.fault_count if fault_count is None else fault_count,
            fault_robot_ids=(
                self._config.fault_robot_ids if fault_robot_ids is None else fault_robot_ids
            ),
        )
        self._validate_fault_robot_ids(config)
        print(f"\n{'='*60}")
        print(f"Simulation: {'WITH RACS' if self._with_racs else 'WITHOUT RACS'}")
        print(f"Sites: {list(self._sites.keys())} | Steps: {config.steps}")
        print(f"Fault injection: {config.fault_site} at step {config.fault_step}")
        print("=" * 60)

        for step in range(config.steps):
            simulated_time = step * self._step_duration_s

            faulted_robot_ids: List[str] = []
            if step == config.fault_step and config.fault_count > 0:
                fault_site_obj = self._sites[config.fault_site]
                if config.fault_robot_ids is not None:
                    faulted = fault_site_obj.inject_fault_by_ids(config.fault_robot_ids)
                else:
                    faulted = fault_site_obj.inject_fault(
                        count=config.fault_count,
                        rng=self._rng,
                    )
                faulted_robot_ids = [robot.robot_id for robot in faulted]
                fault_site_obj.queue_length += 30
                print(
                    f"\n[Step {step:3d}] FAULT INJECTED at {config.fault_site}: "
                    f"{len(faulted)} robots"
                )

            step_metrics: dict = {"step": step, "faulted_robot_ids": faulted_robot_ids, "sites": {}}

            for sid, site in self._sites.items():
                telemetry = site.to_telemetry(timestamp=simulated_time)

                if self._with_racs and sid in self._agents:
                    self._agents[sid].tick(telemetry, now=simulated_time)

                # Without RACS: simulate cascade manually
                if not self._with_racs and step > config.fault_step and sid != config.fault_site:
                    if self._sites[config.fault_site].queue_length > 30:
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
    config = replace(SimulationScenarioConfig(), steps=steps)

    print("\nRunning simulation WITHOUT RACS coordination...")
    sim_no_racs = WarehouseSimulation(config=config, with_racs=False)
    metrics_no_racs = sim_no_racs.run()

    print("\nRunning simulation WITH RACS coordination...")
    sim_racs = WarehouseSimulation(config=config, with_racs=True)
    metrics_racs = sim_racs.run()

    return metrics_no_racs, metrics_racs


if __name__ == "__main__":
    no_racs, with_racs = compare_with_without_racs(steps=60)
