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


def test_site_risk_and_robot_anomaly_together_can_quarantine() -> None:
    commands = []
    brain = NetworkBrain(on_command=lambda site_id, command: commands.append(command))

    brain.ingest_signal(make_signal(RiskLevel.MEDIUM, score=0.4, robot_anomaly=1.0), now=0.0)

    assert commands[0]["type"] == "quarantine_robot"
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

    assert metrics[0]["quarantined_robot_id"] == "SITE_A_R001"


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

    assert first[0]["quarantined_robot_id"] == second[0]["quarantined_robot_id"]


def test_quarantined_robot_receives_no_new_assignments() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    robot_id = metrics[-1]["quarantined_robot_id"]
    robot = next(r for r in sim._sites["SITE_A"].robots if r.robot_id == robot_id)

    assert robot.predictively_quarantined
    assert not robot.available
    assert robot.current_task_id is None


def test_predictive_quarantine_recovers_current_task() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    recovery_step = next(i for i, m in enumerate(metrics) if m["predictive_recovery_events"])
    event = metrics[recovery_step]["predictive_recovery_events"][0]
    task = sim._sites["SITE_A"].tasks[event["task_id"]]

    assert event["from_robot"] == metrics[recovery_step]["quarantined_robot_id"]
    assert task.created_step == 0
    assert task.deadline_step == 20


def test_predictive_reassignment_occurs_no_earlier_than_next_step() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()
    recovery_step = next(i for i, m in enumerate(metrics) if m["predictive_recovery_events"])
    reassignment_step = next(i for i, m in enumerate(metrics) if m["reassignment_events"])

    assert reassignment_step == recovery_step + 1
    assert metrics[reassignment_step]["task_reassignment_step"] == reassignment_step


def test_predictive_reassignment_preserves_deadline_and_created_step() -> None:
    sim = WarehouseSimulation(config=racs_config(task_deadline_steps=7), with_racs=True)

    metrics = sim.run()
    event = next(m["predictive_recovery_events"][0] for m in metrics if m["predictive_recovery_events"])
    task = sim._sites["SITE_A"].tasks[event["task_id"]]

    assert task.created_step == 0
    assert task.deadline_step == 7


def test_predictive_recovery_and_reassignment_counters_are_distinct() -> None:
    metrics = WarehouseSimulation(config=racs_config(), with_racs=True).run()
    recovery_count = sum(len(m["predictive_recovery_events"]) for m in metrics)
    predictive_reassignment_count = metrics[-1]["sites"]["SITE_A"]["predictive_tasks_reassigned"]

    assert recovery_count == 1
    assert predictive_reassignment_count == 1
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


def test_quarantine_does_not_mark_robot_hard_failed_immediately() -> None:
    sim = WarehouseSimulation(config=racs_config(), with_racs=True)

    metrics = sim.run()
    robot_id = next(m["quarantined_robot_id"] for m in metrics if m["quarantined_robot_id"])
    robot = next(r for r in sim._sites["SITE_A"].robots if r.robot_id == robot_id)
    intervention_step = next(i for i, m in enumerate(metrics) if m["intervention_step"] is not None)

    assert robot.predictively_quarantined
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
