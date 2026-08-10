"""
Multi-warehouse discrete-event simulation.

Demonstrates RACS preventing cascading failures that would otherwise propagate
across all sites. Run directly: python simulations/warehouse_sim.py
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from math import ceil
from typing import Dict, List, Mapping, Optional, Tuple

from racs.agents.network_brain import NetworkBrain
from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.risk_signals import RiskLevel, RiskSignal, TelemetryInput


DEFAULT_FAULT_SITE = "SITE_A"
DEFAULT_FAULT_STEP = 10
DEFAULT_FAULT_COUNT = 5


@dataclass(frozen=True)
class SimulationScenarioConfig:
    steps: int = 50
    step_duration_s: float = 1.0
    site_ids: tuple[str, ...] = ("SITE_A", "SITE_B", "SITE_C", "SITE_D")
    robot_count: int = 20
    seed: int = 42
    fault_site: str = DEFAULT_FAULT_SITE
    fault_step: int = DEFAULT_FAULT_STEP
    fault_count: int = DEFAULT_FAULT_COUNT
    fault_robot_ids: Optional[tuple[str, ...]] = None
    initial_site_queue: Mapping[str, int] = field(default_factory=dict)
    initial_site_demand: Mapping[str, float] = field(default_factory=dict)
    tasks_per_step: float = 1.0
    base_service_steps: int = 3
    task_deadline_steps: int = 10
    degradation_enabled: bool = False
    degradation_robot_id: Optional[str] = None
    degradation_site: Optional[str] = None
    degradation_start_step: int = 0
    degradation_rate_per_step: float = 0.0
    minimum_service_capacity: float = 0.25
    hard_failure_capacity_threshold: float = 0.25

    def __post_init__(self) -> None:
        implicit_legacy_fault_default = (
            self.degradation_enabled
            and self.fault_site == DEFAULT_FAULT_SITE
            and self.fault_step == DEFAULT_FAULT_STEP
            and self.fault_count == DEFAULT_FAULT_COUNT
            and self.fault_robot_ids is None
        )
        if self.steps <= 0:
            raise ValueError("steps must be greater than 0")
        if self.step_duration_s <= 0:
            raise ValueError("step_duration_s must be greater than 0")
        if self.robot_count <= 0:
            raise ValueError("robot_count must be greater than 0")
        if self.fault_count < 0:
            raise ValueError("fault_count must be greater than or equal to 0")
        if not implicit_legacy_fault_default and not 0 <= self.fault_step < self.steps:
            raise ValueError("fault_step must satisfy 0 <= fault_step < steps")
        if self.fault_site not in self.site_ids:
            raise ValueError("fault_site must exist in site_ids")
        if not implicit_legacy_fault_default and self.fault_count > self.robot_count:
            raise ValueError("fault_count cannot exceed robots available at fault_site")
        if self.fault_robot_ids is not None and len(self.fault_robot_ids) != self.fault_count:
            raise ValueError("fault_robot_ids count must match fault_count")
        if self.fault_robot_ids is not None and len(set(self.fault_robot_ids)) != len(self.fault_robot_ids):
            raise ValueError("fault_robot_ids cannot contain duplicate robot IDs")
        if self.tasks_per_step < 0:
            raise ValueError("tasks_per_step must be greater than or equal to 0")
        if self.base_service_steps <= 0:
            raise ValueError("base_service_steps must be greater than 0")
        if self.task_deadline_steps <= 0:
            raise ValueError("task_deadline_steps must be greater than 0")
        if self.degradation_start_step < 0 or self.degradation_start_step >= self.steps:
            raise ValueError("degradation_start_step must satisfy 0 <= degradation_start_step < steps")
        if self.degradation_rate_per_step < 0:
            raise ValueError("degradation_rate_per_step must be greater than or equal to 0")
        if not 0 < self.minimum_service_capacity <= 1.0:
            raise ValueError("minimum_service_capacity must satisfy 0 < minimum_service_capacity <= 1")
        if not 0.0 <= self.hard_failure_capacity_threshold <= 1.0:
            raise ValueError(
                "hard_failure_capacity_threshold must satisfy "
                "0 <= hard_failure_capacity_threshold <= 1"
            )
        if self.degradation_enabled and self.degradation_site is None:
            raise ValueError("degradation_site is required when degradation_enabled is true")
        if self.degradation_enabled and self.fault_count > 0 and not implicit_legacy_fault_default:
            raise ValueError("binary robot faults cannot be combined with progressive degradation")

        valid_sites = set(self.site_ids)
        if self.degradation_site is not None and self.degradation_site not in valid_sites:
            raise ValueError("degradation_site must exist in site_ids")
        if (
            self.degradation_robot_id is not None
            and self.degradation_site is not None
            and not self.degradation_robot_id.startswith(f"{self.degradation_site}_R")
        ):
            raise ValueError("degradation_robot_id must belong to degradation_site")
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


class TaskStatus(Enum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class SimTask:
    task_id: str
    created_step: int
    deadline_step: int
    assigned_robot_id: Optional[str] = None
    started_step: Optional[int] = None
    completed_step: Optional[int] = None
    status: TaskStatus = TaskStatus.QUEUED
    reassignment_count: int = 0
    last_requeued_step: Optional[int] = None
    last_requeued_from_robot_id: Optional[str] = None
    last_requeued_reason: Optional[str] = None
    predictive_reassignment_count: int = 0

    @property
    def completion_latency_steps(self) -> Optional[int]:
        if self.completed_step is None:
            return None
        return self.completed_step - self.created_step

    @property
    def is_late(self) -> bool:
        return self.completed_step is not None and self.completed_step > self.deadline_step


@dataclass
class SimRobot:
    robot_id: str
    site_id: str
    faulted: bool = False
    speed_factor: float = 1.0
    available: bool = True
    current_task_id: Optional[str] = None
    remaining_service_work: float = 0.0
    service_time_multiplier: float = 1.0
    completed_task_count: int = 0
    busy_steps: int = 0
    current_service_capacity: float = 1.0
    degrading: bool = False
    degradation_start_step: Optional[int] = None
    degradation_rate_per_step: float = 0.0
    minimum_service_capacity: float = 1.0
    hard_failure_capacity_threshold: float = 0.0
    hard_failed: bool = False
    hard_failure_step: Optional[int] = None
    predictively_draining: bool = False
    drain_step: Optional[int] = None
    graceful_drain_completion_step: Optional[int] = None
    drain_interrupted_by_failure: bool = False
    predictively_quarantined: bool = False
    quarantine_step: Optional[int] = None

    @property
    def can_accept_task(self) -> bool:
        return (
            self.available
            and not self.faulted
            and not self.predictively_draining
            and not self.predictively_quarantined
            and self.current_task_id is None
        )

    def configure_degradation(
        self,
        start_step: int,
        rate_per_step: float,
        minimum_service_capacity: float,
        hard_failure_capacity_threshold: float,
    ) -> None:
        self.degrading = True
        self.degradation_start_step = start_step
        self.degradation_rate_per_step = rate_per_step
        self.minimum_service_capacity = minimum_service_capacity
        self.hard_failure_capacity_threshold = hard_failure_capacity_threshold

    def update_service_capacity(self, step: int) -> None:
        if not self.degrading or self.degradation_start_step is None or self.hard_failed:
            return
        if step < self.degradation_start_step:
            self.current_service_capacity = 1.0
            return

        elapsed_steps = step - self.degradation_start_step
        self.current_service_capacity = max(
            self.minimum_service_capacity,
            1.0 - self.degradation_rate_per_step * elapsed_steps,
        )
        if self.current_service_capacity <= self.hard_failure_capacity_threshold:
            self.hard_failed = True
            self.hard_failure_step = step
            if self.predictively_draining and not self.predictively_quarantined:
                self.drain_interrupted_by_failure = True
            self.faulted = True
            self.available = False


@dataclass
class SimSite:
    site_id: str
    robot_count: int = 20
    queue_length: int = 10
    demand_rate: float = 1.0   # multiplier relative to baseline
    robots: List[SimRobot] = field(default_factory=list)
    tasks: Dict[str, SimTask] = field(default_factory=dict)
    _next_task_index: int = 0
    _last_step: int = 0
    _arrival_credit: float = 0.0

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
            r.available = False
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
            robot.available = False
        return faulted

    def recover_robots(self, count: int = 1) -> None:
        faulted = [r for r in self.robots if r.faulted]
        for r in random.sample(faulted, min(count, len(faulted))):
            r.faulted = False

    def to_telemetry(
        self,
        timestamp: Optional[float] = None,
        step_duration_s: float = 1.0,
        base_service_steps: int = 1,
    ) -> TelemetryInput:
        active = sum(1 for r in self.robots if not r.faulted)
        fault_count = sum(1 for r in self.robots if r.faulted)
        completed = [task for task in self.tasks.values() if task.status == TaskStatus.COMPLETED]
        latencies = [
            task.completion_latency_steps
            for task in completed
            if task.completion_latency_steps is not None
        ]
        capacity = max(active, 1)
        recent_completed = sum(1 for task in completed if task.completed_step == self._last_step)
        healthy_expected_completions = capacity / base_service_steps
        throughput = (
            min(recent_completed / healthy_expected_completions, 1.0)
            if active and healthy_expected_completions > 0 else 0.0
        )
        error_rate = fault_count / max(self.robot_count, 1) * 0.5
        telemetry = TelemetryInput(
            site_id=self.site_id,
            queue_length=self.queued_task_count,
            robot_active_count=active,
            robot_fault_count=fault_count,
            throughput_rate=min(throughput, 1.0),
            error_rate_5min=error_rate,
            avg_task_latency_s=(
                float(sum(latencies) / len(latencies) * step_duration_s)
                if latencies else 0.0
            ),
            **self._observable_robot_anomaly(base_service_steps=base_service_steps),
        )
        if timestamp is not None:
            telemetry.timestamp = timestamp
        return telemetry

    @property
    def queued_task_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.status == TaskStatus.QUEUED)

    @property
    def completed_task_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.status == TaskStatus.COMPLETED)

    @property
    def late_task_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.is_late)

    @property
    def failed_task_count(self) -> int:
        return sum(1 for task in self.tasks.values() if task.status == TaskStatus.FAILED)

    def create_tasks(self, count: int, step: int, deadline_steps: int) -> List[SimTask]:
        created = []
        for _ in range(count):
            task_id = f"{self.site_id}_T{self._next_task_index:06d}"
            self._next_task_index += 1
            task = SimTask(
                task_id=task_id,
                created_step=step,
                deadline_step=step + deadline_steps,
            )
            self.tasks[task_id] = task
            created.append(task)
        self.queue_length = self.queued_task_count
        return created

    def arrival_count_for_step(self, expected_arrivals: float) -> int:
        if expected_arrivals < 0:
            raise ValueError("expected task arrivals cannot be negative")
        self._arrival_credit += expected_arrivals
        arrivals = int(self._arrival_credit + 1e-12)
        self._arrival_credit -= arrivals
        return arrivals

    def service_in_progress(self, step: int) -> List[SimTask]:
        completed = []
        for robot in self.robots:
            if robot.current_task_id is None or robot.faulted or not robot.available:
                continue

            robot.busy_steps += 1
            robot.remaining_service_work -= robot.current_service_capacity
            if robot.remaining_service_work > 0:
                continue

            task = self.tasks[robot.current_task_id]
            task.status = TaskStatus.COMPLETED
            task.completed_step = step
            completed.append(task)
            robot.completed_task_count += 1
            robot.current_task_id = None
            robot.remaining_service_work = 0.0
            if robot.predictively_draining:
                robot.predictively_draining = False
                robot.predictively_quarantined = True
                robot.quarantine_step = step
                robot.graceful_drain_completion_step = step
                robot.available = False

        return completed

    def assign_queued_tasks(self, step: int, base_service_steps: int) -> List[dict]:
        reassignment_events = []
        queued = sorted(
            (task for task in self.tasks.values() if task.status == TaskStatus.QUEUED),
            key=lambda task: task.task_id,
        )
        available = sorted(
            (robot for robot in self.robots if robot.can_accept_task),
            key=lambda robot: (robot.completed_task_count, robot.robot_id),
        )

        for task, robot in zip(queued, available):
            task.status = TaskStatus.IN_PROGRESS
            task.assigned_robot_id = robot.robot_id
            task.started_step = step
            robot.current_task_id = task.task_id
            robot.remaining_service_work = max(
                1.0,
                float(base_service_steps * robot.service_time_multiplier),
            )
            if task.last_requeued_from_robot_id is not None:
                task.reassignment_count += 1
                if task.last_requeued_reason == "predictive":
                    task.predictive_reassignment_count += 1
                reassignment_events.append({
                    "task_id": task.task_id,
                    "from_robot": task.last_requeued_from_robot_id,
                    "to_robot": robot.robot_id,
                    "step": step,
                    "reason": task.last_requeued_reason,
                    "stranded_task_duration": (
                        step - task.last_requeued_step
                        if task.last_requeued_step is not None else 0
                    ),
                })
                task.last_requeued_from_robot_id = None
                task.last_requeued_step = None
                task.last_requeued_reason = None

        self.queue_length = self.queued_task_count
        return reassignment_events

    def recover_stranded_tasks_from_hard_failed_robots(
        self,
        step: int,
        reason: str = "baseline",
    ) -> List[dict]:
        recovery_events = []
        for robot in self.robots:
            if not robot.hard_failed or robot.current_task_id is None:
                continue

            task = self.tasks[robot.current_task_id]
            task.status = TaskStatus.QUEUED
            task.assigned_robot_id = None
            task.started_step = None
            task.last_requeued_step = step
            task.last_requeued_from_robot_id = robot.robot_id
            task.last_requeued_reason = reason
            recovery_events.append({
                "task_id": task.task_id,
                "from_robot": robot.robot_id,
                "step": step,
                "reason": reason,
            })
            robot.current_task_id = None
            robot.remaining_service_work = 0.0

        self.queue_length = self.queued_task_count
        return recovery_events

    def quarantine_robot(self, robot_id: str, step: int) -> List[dict]:
        robots_by_id = {robot.robot_id: robot for robot in self.robots}
        if robot_id not in robots_by_id:
            raise ValueError(f"robot_id must exist at {self.site_id}: {robot_id}")
        robot = robots_by_id[robot_id]
        robot.predictively_quarantined = True
        robot.quarantine_step = step
        robot.available = False

        if robot.current_task_id is None:
            return []

        task = self.tasks[robot.current_task_id]
        task.status = TaskStatus.QUEUED
        task.assigned_robot_id = None
        task.started_step = None
        task.last_requeued_step = step
        task.last_requeued_from_robot_id = robot.robot_id
        task.last_requeued_reason = "predictive"
        robot.current_task_id = None
        robot.remaining_service_work = 0.0
        self.queue_length = self.queued_task_count
        return [{
            "task_id": task.task_id,
            "from_robot": robot.robot_id,
            "step": step,
        }]

    def drain_robot(self, robot_id: str, step: int) -> dict:
        robots_by_id = {robot.robot_id: robot for robot in self.robots}
        if robot_id not in robots_by_id:
            raise ValueError(f"robot_id must exist at {self.site_id}: {robot_id}")
        robot = robots_by_id[robot_id]
        if robot.hard_failed or robot.predictively_quarantined:
            return {
                "robot_id": robot.robot_id,
                "step": step,
                "current_task_id": robot.current_task_id,
                "immediate_quarantine": False,
            }

        robot.predictively_draining = True
        robot.drain_step = step
        if robot.current_task_id is None:
            robot.predictively_draining = False
            robot.predictively_quarantined = True
            robot.quarantine_step = step
            robot.available = False
            immediate_quarantine = True
        else:
            immediate_quarantine = False

        return {
            "robot_id": robot.robot_id,
            "step": step,
            "current_task_id": robot.current_task_id,
            "immediate_quarantine": immediate_quarantine,
        }

    def _observable_robot_anomaly(self, base_service_steps: int) -> dict:
        candidates = []
        for robot in self.robots:
            if robot.current_task_id is None:
                continue
            task = self.tasks[robot.current_task_id]
            if task.started_step is None:
                continue
            task_age = self._last_step - task.started_step
            candidates.append((
                task_age,
                task.task_id,
                robot.robot_id,
            ))
        if not candidates:
            return {"suspect_robot_id": None, "suspect_robot_anomaly": 0.0}

        max_age, _, robot_id = max(candidates)
        peer_ages = [age for age, _, rid in candidates if rid != robot_id]
        if not peer_ages:
            return {"suspect_robot_id": robot_id, "suspect_robot_anomaly": 0.0}

        peer_reference_age = min(peer_ages)
        anomaly = max(0.0, (max_age - peer_reference_age) / max(base_service_steps, 1))
        return {
            "suspect_robot_id": robot_id,
            "suspect_robot_anomaly": round(anomaly, 3),
        }

    def task_metrics(self, completed_this_step: List[SimTask], step: int) -> dict:
        completed = [task for task in self.tasks.values() if task.status == TaskStatus.COMPLETED]
        latencies = [
            task.completion_latency_steps
            for task in completed
            if task.completion_latency_steps is not None
        ]
        elapsed_steps = max(step + 1, 1)
        return {
            "tasks_created": len(self.tasks),
            "tasks_completed": len(completed),
            "tasks_completed_step": len(completed_this_step),
            "tasks_late": self.late_task_count,
            "tasks_failed": self.failed_task_count,
            "tasks_reassigned": sum(task.reassignment_count for task in self.tasks.values()),
            "predictive_tasks_reassigned": sum(
                task.predictive_reassignment_count for task in self.tasks.values()
            ),
            "avg_completion_latency": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
            "per_robot_completed": {
                robot.robot_id: robot.completed_task_count for robot in self.robots
            },
            "per_robot_busy_steps": {
                robot.robot_id: robot.busy_steps for robot in self.robots
            },
            "per_robot_utilization": {
                robot.robot_id: round(robot.busy_steps / elapsed_steps, 3)
                for robot in self.robots
            },
        }


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
        racs_intervention_risk_level: RiskLevel = RiskLevel.MEDIUM,
        racs_robot_anomaly_threshold: float = 1.0,
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
                queue_length=0,
                demand_rate=config.initial_site_demand.get(sid, 1.0),
            )
            for sid in ids
        }
        for sid, site in self._sites.items():
            site.create_tasks(
                count=config.initial_site_queue.get(sid, 10),
                step=0,
                deadline_steps=config.task_deadline_steps,
            )
        self._degrading_robot_id: Optional[str] = None
        if config.degradation_enabled:
            self._degrading_robot_id = self._configure_degrading_robot(config)
        self._current_step: Optional[int] = None
        self._current_step_metrics: Optional[dict] = None
        self._risk_detection_step: Optional[int] = None
        self._risk_detection_score: Optional[float] = None
        self._risk_detection_level: Optional[str] = None
        self._risk_detection_robot_anomaly: Optional[float] = None
        self._intervention_step: Optional[int] = None
        self._draining_robot_id: Optional[str] = None
        self._quarantined_robot_id: Optional[str] = None
        self._counterfactual_failure_step = self._compute_counterfactual_failure_step(config)
        self._metrics: List[dict] = []

        if with_racs:
            self._brain = NetworkBrain(
                on_command=self._handle_brain_command,
                intervention_risk_level=racs_intervention_risk_level,
                robot_anomaly_threshold=racs_robot_anomaly_threshold,
            )
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

    def _configure_degrading_robot(self, config: SimulationScenarioConfig) -> str:
        if config.degradation_site is None:
            raise ValueError("degradation_site is required when degradation_enabled is true")

        site = self._sites[config.degradation_site]
        robots_by_id = {robot.robot_id: robot for robot in site.robots}
        if config.degradation_robot_id is None:
            robot = self._rng.choice(sorted(site.robots, key=lambda r: r.robot_id))
        else:
            if config.degradation_robot_id not in robots_by_id:
                raise ValueError(
                    f"degradation_robot_id must exist at {config.degradation_site}: "
                    f"{config.degradation_robot_id}"
                )
            robot = robots_by_id[config.degradation_robot_id]

        robot.configure_degradation(
            start_step=config.degradation_start_step,
            rate_per_step=config.degradation_rate_per_step,
            minimum_service_capacity=config.minimum_service_capacity,
            hard_failure_capacity_threshold=config.hard_failure_capacity_threshold,
        )
        return robot.robot_id

    @staticmethod
    def _compute_counterfactual_failure_step(config: SimulationScenarioConfig) -> Optional[int]:
        if not config.degradation_enabled:
            return None
        if config.hard_failure_capacity_threshold < config.minimum_service_capacity:
            return None
        if config.hard_failure_capacity_threshold >= 1.0:
            return config.degradation_start_step
        if config.degradation_rate_per_step == 0:
            return None

        elapsed_steps = ceil(
            max(0.0, 1.0 - config.hard_failure_capacity_threshold - 1e-12)
            / config.degradation_rate_per_step
        )
        return config.degradation_start_step + elapsed_steps

    @staticmethod
    def _has_implicit_legacy_fault_default(config: SimulationScenarioConfig) -> bool:
        return (
            config.degradation_enabled
            and config.fault_site == DEFAULT_FAULT_SITE
            and config.fault_step == DEFAULT_FAULT_STEP
            and config.fault_count == DEFAULT_FAULT_COUNT
            and config.fault_robot_ids is None
        )

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
            site.demand_rate = max(0.3, site.demand_rate * 0.7)
        elif cmd_type == "reduce_intake":
            site.demand_rate = max(0.3, site.demand_rate * 0.8)
        elif cmd_type == "quarantine_robot":
            if self._current_step is None:
                return
            robot_id = command.get("robot_id")
            if not robot_id:
                raise ValueError("quarantine_robot command requires robot_id")
            recovery_events = site.quarantine_robot(robot_id, step=self._current_step)
            if self._risk_detection_step is None:
                self._risk_detection_step = self._current_step
                self._risk_detection_score = command.get("risk_score")
                self._risk_detection_level = command.get("risk_level")
                self._risk_detection_robot_anomaly = command.get("robot_anomaly")
            self._intervention_step = self._current_step
            self._quarantined_robot_id = robot_id
            if self._current_step_metrics is not None:
                self._current_step_metrics["risk_detection_step"] = self._risk_detection_step
                self._current_step_metrics["risk_detection_score"] = self._risk_detection_score
                self._current_step_metrics["risk_detection_level"] = self._risk_detection_level
                self._current_step_metrics["risk_detection_robot_anomaly"] = (
                    self._risk_detection_robot_anomaly
                )
                self._current_step_metrics["intervention_step"] = self._intervention_step
                self._current_step_metrics["quarantined_robot_id"] = self._quarantined_robot_id
                self._current_step_metrics["predictive_recovery_events"].extend(
                    recovery_events
                )
        elif cmd_type == "drain_robot":
            if self._current_step is None:
                return
            robot_id = command.get("robot_id")
            if not robot_id:
                raise ValueError("drain_robot command requires robot_id")
            drain_event = site.drain_robot(robot_id, step=self._current_step)
            if self._risk_detection_step is None:
                self._risk_detection_step = self._current_step
                self._risk_detection_score = command.get("risk_score")
                self._risk_detection_level = command.get("risk_level")
                self._risk_detection_robot_anomaly = command.get("robot_anomaly")
            self._intervention_step = self._current_step
            self._draining_robot_id = robot_id
            if drain_event.get("immediate_quarantine"):
                self._quarantined_robot_id = robot_id
            if self._current_step_metrics is not None:
                self._current_step_metrics["risk_detection_step"] = self._risk_detection_step
                self._current_step_metrics["risk_detection_score"] = self._risk_detection_score
                self._current_step_metrics["risk_detection_level"] = self._risk_detection_level
                self._current_step_metrics["risk_detection_robot_anomaly"] = (
                    self._risk_detection_robot_anomaly
                )
                self._current_step_metrics["intervention_step"] = self._intervention_step
                self._current_step_metrics["draining_robot_id"] = self._draining_robot_id
                self._current_step_metrics["drain_events"].append(drain_event)
                self._current_step_metrics["quarantined_robot_id"] = self._quarantined_robot_id

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
        if self._has_implicit_legacy_fault_default(config):
            config = replace(config, fault_step=0, fault_count=0)
        self._validate_fault_robot_ids(config)
        print(f"\n{'='*60}")
        print(f"Simulation: {'WITH RACS' if self._with_racs else 'WITHOUT RACS'}")
        print(f"Sites: {list(self._sites.keys())} | Steps: {config.steps}")
        print(f"Fault injection: {config.fault_site} at step {config.fault_step}")
        print("=" * 60)

        for step in range(config.steps):
            simulated_time = step * self._step_duration_s

            for site in self._sites.values():
                for robot in site.robots:
                    robot.update_service_capacity(step)

            created_by_site: Dict[str, int] = {}
            for site in self._sites.values():
                expected_arrivals = config.tasks_per_step * site.demand_rate
                arrivals = site.arrival_count_for_step(expected_arrivals)
                created_by_site[site.site_id] = len(
                    site.create_tasks(
                        count=arrivals,
                        step=step,
                        deadline_steps=config.task_deadline_steps,
                    )
                )

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
                fault_site_obj.create_tasks(
                    count=30,
                    step=step,
                    deadline_steps=config.task_deadline_steps,
                )
                created_by_site[config.fault_site] += 30
                print(
                    f"\n[Step {step:3d}] FAULT INJECTED at {config.fault_site}: "
                    f"{len(faulted)} robots"
                )

            degrading_robot = self._get_degrading_robot()
            step_metrics: dict = {
                "step": step,
                "faulted_robot_ids": faulted_robot_ids,
                "degrading_robot_id": self._degrading_robot_id,
                "degradation_start_step": (
                    config.degradation_start_step if config.degradation_enabled else None
                ),
                "hard_failure_step": (
                    degrading_robot.hard_failure_step if degrading_robot is not None else None
                ),
                "counterfactual_failure_step": self._counterfactual_failure_step,
                "robot_failure_step": (
                    degrading_robot.hard_failure_step if degrading_robot is not None else None
                ),
                "baseline_reaction_step": None,
                "task_reassignment_step": None,
                "reassignment_events": [],
                "baseline_recovery_events": [],
                "fallback_recovery_events": [],
                "fallback_reaction_step": None,
                "risk_detection_step": self._risk_detection_step,
                "risk_detection_score": self._risk_detection_score,
                "risk_detection_level": self._risk_detection_level,
                "risk_detection_robot_anomaly": self._risk_detection_robot_anomaly,
                "intervention_step": self._intervention_step,
                "draining_robot_id": self._draining_robot_id,
                "quarantined_robot_id": self._quarantined_robot_id,
                "graceful_drain_completion_step": None,
                "predictive_quarantine_step": None,
                "drain_completed_before_failure": False,
                "drain_interrupted_by_failure": False,
                "drain_events": [],
                "predictive_recovery_events": [],
                "service_capacity_by_step": (
                    {
                        self._degrading_robot_id: round(
                            degrading_robot.current_service_capacity,
                            3,
                        )
                    }
                    if degrading_robot is not None and self._degrading_robot_id is not None
                    else {}
                ),
                "sites": {},
            }
            self._current_step = step
            self._current_step_metrics = step_metrics

            for sid, site in self._sites.items():
                site._last_step = step
                completed_this_step = site.service_in_progress(step)
                for robot in site.robots:
                    if robot.graceful_drain_completion_step == step:
                        if self._quarantined_robot_id is None:
                            self._quarantined_robot_id = robot.robot_id
                        step_metrics["quarantined_robot_id"] = self._quarantined_robot_id
                        step_metrics["graceful_drain_completion_step"] = step
                        step_metrics["predictive_quarantine_step"] = step
                        step_metrics["drain_completed_before_failure"] = (
                            not robot.hard_failed
                            or robot.hard_failure_step is None
                            or step < robot.hard_failure_step
                        )
                    if robot.drain_interrupted_by_failure:
                        step_metrics["drain_interrupted_by_failure"] = True
                reassignment_events = site.assign_queued_tasks(
                    step=step,
                    base_service_steps=config.base_service_steps,
                )
                if reassignment_events:
                    step_metrics["reassignment_events"].extend(reassignment_events)
                    step_metrics["task_reassignment_step"] = step
                telemetry = site.to_telemetry(
                    timestamp=simulated_time,
                    step_duration_s=self._step_duration_s,
                    base_service_steps=config.base_service_steps,
                )

                if self._with_racs and sid in self._agents:
                    self._agents[sid].tick(telemetry, now=simulated_time)

                if not self._with_racs:
                    recovery_events = site.recover_stranded_tasks_from_hard_failed_robots(
                        step,
                        reason="baseline",
                    )
                    if recovery_events:
                        step_metrics["baseline_recovery_events"].extend(recovery_events)
                        step_metrics["baseline_reaction_step"] = step
                else:
                    recovery_events = site.recover_stranded_tasks_from_hard_failed_robots(
                        step,
                        reason="fallback",
                    )
                    if recovery_events:
                        step_metrics["fallback_recovery_events"].extend(recovery_events)
                        step_metrics["fallback_reaction_step"] = step

                # Without RACS: simulate cascade manually
                if not self._with_racs and step > config.fault_step and sid != config.fault_site:
                    if self._sites[config.fault_site].queued_task_count > 30:
                        added = min(3, max(100 - site.queued_task_count, 0))
                        site.create_tasks(
                            count=added,
                            step=step,
                            deadline_steps=config.task_deadline_steps,
                        )
                        created_by_site[sid] += added

                fault_count = sum(1 for r in site.robots if r.faulted)
                throughput = telemetry.throughput_rate
                task_metrics = site.task_metrics(completed_this_step, step=step)
                step_metrics["sites"][sid] = {
                    "queue": site.queued_task_count,
                    "faults": fault_count,
                    "throughput": round(throughput, 3),
                    "tasks_created_step": created_by_site[sid],
                    **task_metrics,
                }

            self._metrics.append(step_metrics)
            self._current_step = None
            self._current_step_metrics = None

            if step % 10 == 0:
                self._print_step(step, step_metrics)

        self._print_summary()
        return self._metrics

    def _get_degrading_robot(self) -> Optional[SimRobot]:
        if self._degrading_robot_id is None:
            return None
        for site in self._sites.values():
            for robot in site.robots:
                if robot.robot_id == self._degrading_robot_id:
                    return robot
        return None

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
