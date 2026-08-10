"""Tests for RACS predictive AMR quarantine intervention."""

from __future__ import annotations

from racs.agents.network_brain import NetworkBrain
from racs.risk.risk_signals import RiskLevel, RiskSignal, TelemetryInput
from racs.safety.constraints import ConstraintAction, SafetyGate
from simulations.warehouse_sim import SimulationScenarioConfig, TaskStatus, WarehouseSimulation


def racs_config(**overrides) -> SimulationScenarioConfig:
    defaults = dict(
        steps=10,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 60},
        tasks_per_step=2,
        base_service_steps=2,
        task_deadline_steps=20,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=0,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )
    defaults.update(overrides)
    return SimulationScenarioConfig(**defaults)


def published_signals_for(sim: WarehouseSimulation) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    original_ingest = sim._brain.ingest_signal

    def capture(signal: RiskSignal, now: float | None = None):
        signals.append(signal)
        return original_ingest(signal, now=now)

    sim._brain.ingest_signal = capture
    return signals


def make_signal(
    level: RiskLevel,
    score: float,
    robot_anomaly: float,
    suspect_robot_id: str = "SITE_A_R000",
) -> RiskSignal:
    return RiskSignal(
        site_id="SITE_A",
        congestion_probability=score,
        failure_likelihood=score,
        recovery_latency_seconds=0.0,
        level=level,
        source_telemetry=TelemetryInput(
            site_id="SITE_A",
            queue_length=50,
            robot_active_count=2,
            robot_fault_count=0,
            throughput_rate=0.5,
            error_rate_5min=0.0,
            suspect_robot_id=suspect_robot_id,
            suspect_robot_anomaly=robot_anomaly,
        ),
    )


def test_racs_does_not_quarantine_before_qualifying_risk_signal() -> None:
    config = racs_config(
        steps=4,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=1,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )

    metrics = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_robot_anomaly_threshold=0.0,
    ).run()

    assert all(step["intervention_step"] is None for step in metrics)
    assert all(step["quarantined_robot_id"] is None for step in metrics)


def test_healthy_high_load_site_risk_alone_does_not_quarantine() -> None:
    config = SimulationScenarioConfig(
        steps=10,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 60},
        tasks_per_step=2,
        base_service_steps=2,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)
    signals = published_signals_for(sim)

    metrics = sim.run()

    assert any(signal.level == RiskLevel.MEDIUM for signal in signals)
    assert max(signal.source_telemetry.suspect_robot_anomaly for signal in signals) == 0.0
    assert metrics[-1]["intervention_step"] is None
    assert metrics[-1]["quarantined_robot_id"] is None


def test_racs_detection_uses_published_risk_not_hidden_degradation_config() -> None:
    config = racs_config(
        steps=4,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )

    metrics = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_robot_anomaly_threshold=0.0,
    ).run()

    assert metrics[-1]["hard_failure_step"] == 1
    assert metrics[-1]["intervention_step"] is None
    assert metrics[-1]["quarantined_robot_id"] is None


def test_robot_anomaly_without_qualifying_site_risk_does_not_quarantine() -> None:
    commands = []
    brain = NetworkBrain(on_command=lambda site_id, command: commands.append(command))

    brain.ingest_signal(make_signal(RiskLevel.LOW, score=0.2, robot_anomaly=2.0), now=0.0)

    assert commands == []


def test_site_risk_and_robot_anomaly_together_can_drain() -> None:
    commands = []
    brain = NetworkBrain(on_command=lambda site_id, command: commands.append(command))

    brain.ingest_signal(make_signal(RiskLevel.MEDIUM, score=0.4, robot_anomaly=1.0), now=0.0)

    assert commands[0]["type"] == "drain_robot"
    assert commands[0]["robot_id"] == "SITE_A_R000"


def test_detection_step_score_and_level_are_recorded() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()

    detection_step = next(i for i, m in enumerate(metrics) if m["risk_detection_step"] is not None)

    assert metrics[detection_step]["risk_detection_step"] == detection_step
    assert metrics[detection_step]["risk_detection_score"] >= 0.3
    assert metrics[detection_step]["risk_detection_level"] == "MEDIUM"
    assert metrics[detection_step]["risk_detection_robot_anomaly"] >= 1.0


def test_attribution_uses_observable_task_state_not_degradation_robot_id() -> None:
    config = racs_config(
        steps=2,
        initial_site_queue={"SITE_A": 80},
        tasks_per_step=0,
        base_service_steps=20,
        degradation_robot_id="SITE_A_R000",
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )

    metrics = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_robot_anomaly_threshold=0.0,
    ).run()

    assert metrics[0]["draining_robot_id"] == "SITE_A_R001"


def test_attribution_tie_breaking_is_deterministic() -> None:
    config = racs_config(
        steps=2,
        initial_site_queue={"SITE_A": 80},
        tasks_per_step=0,
        base_service_steps=20,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )

    first = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_robot_anomaly_threshold=0.0,
    ).run()
    second = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_robot_anomaly_threshold=0.0,
    ).run()

    assert first[0]["draining_robot_id"] == second[0]["draining_robot_id"]


def test_draining_robot_receives_no_new_assignments() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    robot_id = metrics[-1]["draining_robot_id"]
    robot = next(r for r in sim._sites["SITE_A"].robots if r.robot_id == robot_id)

    assert robot.predictively_draining or robot.predictively_quarantined or robot.hard_failed
    assert not robot.available
    assert robot.current_task_id is None


def test_predictive_drain_does_not_recover_current_task_immediately() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    drain_step = next(i for i, m in enumerate(metrics) if m["drain_events"])
    event = metrics[drain_step]["drain_events"][0]
    task = sim._sites["SITE_A"].tasks[event["current_task_id"]]

    assert event["robot_id"] == metrics[drain_step]["draining_robot_id"]
    assert metrics[drain_step]["predictive_recovery_events"] == []
    assert task.created_step == 0
    assert task.deadline_step == 20


def test_predictive_drain_current_task_keeps_progress_until_completion() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()
    drain_step = next(i for i, m in enumerate(metrics) if m["drain_events"])

    assert metrics[drain_step]["predictive_recovery_events"] == []
    assert all(m["task_reassignment_step"] is None for m in metrics[:drain_step + 1])


def test_predictive_drain_preserves_deadline_and_created_step() -> None:
    sim = WarehouseSimulation(config=racs_config(task_deadline_steps=7), with_racs=True)

    metrics = sim.run()
    event = next(m["drain_events"][0] for m in metrics if m["drain_events"])
    task = sim._sites["SITE_A"].tasks[event["current_task_id"]]

    assert task.created_step == 0
    assert task.deadline_step == 7


def test_predictive_drain_does_not_count_recovery_or_reassignment() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()
    recovery_count = sum(len(m["predictive_recovery_events"]) for m in metrics)
    predictive_reassignment_count = metrics[-1]["sites"]["SITE_A"]["predictive_tasks_reassigned"]

    assert recovery_count == 0
    assert predictive_reassignment_count == 0
    assert all(m["baseline_recovery_events"] == [] for m in metrics)


def test_intervention_risk_threshold_is_configurable_to_medium() -> None:
    metrics = WarehouseSimulation(
        config=racs_config(),
        with_racs=True,
        racs_intervention_risk_level=RiskLevel.MEDIUM,
    ).run()

    assert metrics[-1]["intervention_step"] is not None


def test_intervention_risk_threshold_is_configurable_to_high() -> None:
    commands = []
    brain = NetworkBrain(
        on_command=lambda site_id, command: commands.append(command),
        intervention_risk_level=RiskLevel.HIGH,
    )

    brain.ingest_signal(make_signal(RiskLevel.MEDIUM, score=0.4, robot_anomaly=2.0), now=0.0)
    brain.ingest_signal(make_signal(RiskLevel.HIGH, score=0.6, robot_anomaly=2.0), now=10.0)

    assert len(commands) == 1
    assert commands[0]["risk_level"] == "HIGH"


def test_drain_does_not_mark_robot_hard_failed_immediately() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    robot_id = next(m["draining_robot_id"] for m in metrics if m["draining_robot_id"])
    robot = next(r for r in sim._sites["SITE_A"].robots if r.robot_id == robot_id)
    intervention_step = next(i for i, m in enumerate(metrics) if m["intervention_step"] is not None)

    assert robot.predictively_draining or robot.predictively_quarantined or robot.hard_failed
    assert metrics[intervention_step]["hard_failure_step"] is None
    assert metrics[intervention_step]["intervention_step"] < metrics[-1]["counterfactual_failure_step"]


def test_degradation_trajectory_is_unchanged_by_predictive_intervention() -> None:
    config = racs_config()

    baseline = WarehouseSimulation(config=config, with_racs=False).run()
    racs = WarehouseSimulation(config=config, with_racs=True).run()

    assert [m["service_capacity_by_step"] for m in baseline] == [
        m["service_capacity_by_step"] for m in racs
    ]
    assert baseline[-1]["counterfactual_failure_step"] == racs[-1]["counterfactual_failure_step"]


def test_pre_intervention_workload_and_degradation_match_baseline() -> None:
    config = racs_config()

    baseline = WarehouseSimulation(config=config, with_racs=False).run()
    racs = WarehouseSimulation(config=config, with_racs=True).run()
    intervention_step = next(i for i, m in enumerate(racs) if m["intervention_step"] is not None)

    assert [
        (m["sites"]["SITE_A"]["tasks_created_step"], m["service_capacity_by_step"])
        for m in baseline[:intervention_step]
    ] == [
        (m["sites"]["SITE_A"]["tasks_created_step"], m["service_capacity_by_step"])
        for m in racs[:intervention_step]
    ]


def test_racs_may_fail_to_intervene_before_hard_failure() -> None:
    config = racs_config(
        steps=4,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )

    metrics = WarehouseSimulation(config=config, with_racs=True).run()

    assert metrics[-1]["hard_failure_step"] == 1
    assert metrics[-1]["intervention_step"] is None


def test_high_threshold_can_result_in_no_pre_failure_intervention() -> None:
    metrics = WarehouseSimulation(
        config=racs_config(),
        with_racs=True,
        racs_intervention_risk_level=RiskLevel.HIGH,
    ).run()

    assert metrics[-1]["counterfactual_failure_step"] == 8
    assert (
        metrics[-1]["intervention_step"] is None
        or metrics[-1]["intervention_step"] >= metrics[-1]["counterfactual_failure_step"]
    )


def test_healthy_run_does_not_predictively_quarantine() -> None:
    config = SimulationScenarioConfig(
        steps=5,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=1,
        base_service_steps=1,
    )

    metrics = WarehouseSimulation(config=config, with_racs=True).run()

    assert all(m["quarantined_robot_id"] is None for m in metrics)


def test_counterfactual_failure_step_is_policy_independent() -> None:
    config = racs_config()

    baseline = WarehouseSimulation(config=config, with_racs=False).run()
    racs = WarehouseSimulation(config=config, with_racs=True).run()

    assert baseline[-1]["counterfactual_failure_step"] == 8
    assert baseline[-1]["counterfactual_failure_step"] == racs[-1]["counterfactual_failure_step"]


def test_malformed_quarantine_command_without_robot_id_is_rejected() -> None:
    result = SafetyGate().evaluate({"type": "quarantine_robot", "risk_score": 0.1})

    assert result.allowed is False
    assert result.action == ConstraintAction.BLOCK
    assert "robot_id" in result.reason


def test_malformed_drain_command_without_robot_id_is_rejected() -> None:
    result = SafetyGate().evaluate({"type": "drain_robot", "risk_score": 0.1})

    assert result.allowed is False
    assert result.action == ConstraintAction.BLOCK
    assert "robot_id" in result.reason


def test_reactive_baseline_remains_unchanged() -> None:
    config = racs_config(
        steps=4,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["baseline_reaction_step"] for m in metrics] == [None, 1, None, None]
    assert [m["task_reassignment_step"] for m in metrics] == [None, None, 2, None]


def test_racs_fallback_recovers_stranded_work_at_hard_failure_without_prediction() -> None:
    config = racs_config(
        steps=3,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )
    sim = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_intervention_risk_level=RiskLevel.CRITICAL,
    )

    metrics = sim.run()
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert metrics[1]["fallback_reaction_step"] == 1
    assert metrics[1]["fallback_recovery_events"] == [
        {
            "task_id": "SITE_A_T000000",
            "from_robot": "SITE_A_R000",
            "step": 1,
            "reason": "fallback",
        }
    ]
    assert task.created_step == 0
    assert task.deadline_step == 20
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_robot_id == "SITE_A_R001"


def test_racs_fallback_recovery_matches_baseline_queue_and_reassignment_timing() -> None:
    config = racs_config(
        steps=4,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )

    baseline = WarehouseSimulation(config=config, with_racs=False).run()
    racs = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_intervention_risk_level=RiskLevel.CRITICAL,
    ).run()

    assert [m["sites"]["SITE_A"]["queue"] for m in racs] == [
        m["sites"]["SITE_A"]["queue"] for m in baseline
    ]
    assert [m["task_reassignment_step"] for m in racs] == [None, None, 2, None]
    assert racs[2]["reassignment_events"][0]["reason"] == "fallback"
    assert racs[-1]["sites"]["SITE_A"]["tasks_reassigned"] == baseline[-1]["sites"]["SITE_A"]["tasks_reassigned"]


def test_graceful_drain_then_physical_hard_failure_does_not_recover_twice() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()

    assert any(m["graceful_drain_completion_step"] is not None for m in metrics)
    assert sum(len(m["predictive_recovery_events"]) for m in metrics) == 0
    assert sum(len(m["fallback_recovery_events"]) for m in metrics) == 0
    assert metrics[-1]["hard_failure_step"] == metrics[-1]["counterfactual_failure_step"]
    assert metrics[-1]["sites"]["SITE_A"]["predictive_tasks_reassigned"] == 0


def test_racs_fallback_recovery_is_not_same_step_reassignment() -> None:
    config = racs_config(
        steps=3,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )

    metrics = WarehouseSimulation(
        config=config,
        with_racs=True,
        racs_intervention_risk_level=RiskLevel.CRITICAL,
    ).run()

    assert metrics[1]["fallback_recovery_events"]
    assert metrics[1]["reassignment_events"] == []
    assert metrics[2]["reassignment_events"]


def test_drain_command_when_robot_is_idle_immediately_quarantines() -> None:
    config = racs_config(
        steps=1,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=0,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)
    site = sim._sites["SITE_A"]

    event = site.drain_robot("SITE_A_R000", step=0)
    robot = site.robots[0]

    assert event["immediate_quarantine"] is True
    assert robot.predictively_quarantined
    assert not robot.predictively_draining
    assert not robot.available


def test_drain_command_with_task_continues_current_task_and_preserves_work() -> None:
    config = racs_config(
        steps=1,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)
    site = sim._sites["SITE_A"]
    site.assign_queued_tasks(step=0, base_service_steps=4)
    robot = site.robots[0]
    robot.remaining_service_work = 2.5

    event = site.drain_robot(robot.robot_id, step=0)

    assert event["immediate_quarantine"] is False
    assert robot.predictively_draining
    assert robot.current_task_id == "SITE_A_T000000"
    assert robot.remaining_service_work == 2.5


def test_draining_robot_is_excluded_from_new_assignment_after_completion() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=1,
        base_service_steps=1,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)
    site = sim._sites["SITE_A"]
    site.assign_queued_tasks(step=0, base_service_steps=1)
    site.drain_robot("SITE_A_R000", step=0)

    completed = site.service_in_progress(step=1)
    reassignment_events = site.assign_queued_tasks(step=1, base_service_steps=1)

    assert [task.task_id for task in completed] == ["SITE_A_T000000"]
    assert reassignment_events == []
    assert site.robots[0].predictively_quarantined
    assert site.robots[0].current_task_id is None


def test_hard_failure_while_draining_uses_fallback_once() -> None:
    config = racs_config(
        steps=1,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        base_service_steps=10,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)
    site = sim._sites["SITE_A"]
    site.assign_queued_tasks(step=0, base_service_steps=10)
    site.drain_robot("SITE_A_R000", step=0)
    robot = site.robots[0]

    robot.hard_failed = True
    robot.faulted = True
    robot.available = False
    robot.drain_interrupted_by_failure = True
    events = site.recover_stranded_tasks_from_hard_failed_robots(step=1, reason="fallback")
    second_events = site.recover_stranded_tasks_from_hard_failed_robots(step=1, reason="fallback")

    assert events == [
        {
            "task_id": "SITE_A_T000000",
            "from_robot": "SITE_A_R000",
            "step": 1,
            "reason": "fallback",
        }
    ]
    assert second_events == []
    assert robot.current_task_id is None
