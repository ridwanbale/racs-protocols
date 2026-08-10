"""Tests for the conventional reactive baseline recovery policy."""

from __future__ import annotations

from simulations.warehouse_sim import SimulationScenarioConfig, TaskStatus, WarehouseSimulation


def baseline_config(**overrides) -> SimulationScenarioConfig:
    defaults = dict(
        steps=4,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        base_service_steps=1,
        task_deadline_steps=10,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )
    defaults.update(overrides)
    return SimulationScenarioConfig(**defaults)


def test_degrading_robot_remains_eligible_before_hard_failure() -> None:
    config = baseline_config(steps=1, degradation_rate_per_step=0.0)
    sim = WarehouseSimulation(config=config, with_racs=False)

    sim.run()
    robot = sim._sites["SITE_A"].robots[0]

    assert robot.current_task_id == "SITE_A_T000000"
    assert robot.available
    assert not robot.hard_failed


def test_baseline_does_not_react_before_hard_failure() -> None:
    config = baseline_config(steps=2, degradation_rate_per_step=0.2, hard_failure_capacity_threshold=0.5)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert metrics[0]["hard_failure_step"] is None
    assert metrics[0]["baseline_reaction_step"] is None
    assert metrics[0]["baseline_recovery_events"] == []


def test_hard_failure_makes_robot_unavailable() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=2), with_racs=False)

    sim.run()
    robot = sim._sites["SITE_A"].robots[0]

    assert robot.hard_failed
    assert robot.faulted
    assert not robot.available


def test_stranded_task_is_requeued_after_hard_failure() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=2), with_racs=False)

    metrics = sim.run()
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert metrics[1]["baseline_reaction_step"] == 1
    assert metrics[1]["baseline_recovery_events"] == [
        {
            "task_id": "SITE_A_T000000",
            "from_robot": "SITE_A_R000",
            "step": 1,
            "reason": "baseline",
        }
    ]
    assert task.status == TaskStatus.QUEUED
    assert task.assigned_robot_id is None
    assert task.started_step is None


def test_recovered_task_preserves_creation_and_deadline() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=2, task_deadline_steps=7), with_racs=False)

    sim.run()
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert task.created_step == 0
    assert task.deadline_step == 7


def test_failed_robot_cannot_receive_reassigned_work() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=3), with_racs=False)

    metrics = sim.run()
    event = metrics[2]["reassignment_events"][0]

    assert event["from_robot"] == "SITE_A_R000"
    assert event["to_robot"] == "SITE_A_R001"
    assert sim._sites["SITE_A"].robots[0].current_task_id is None


def test_recovered_task_can_be_completed_by_healthy_robot() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=4), with_racs=False)

    metrics = sim.run()
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert metrics[2]["task_reassignment_step"] == 2
    assert task.assigned_robot_id == "SITE_A_R001"
    assert task.status == TaskStatus.COMPLETED
    assert task.completed_step == 3


def test_recovered_task_is_not_double_counted_or_lost() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=4), with_racs=False)

    metrics = sim.run()
    site = sim._sites["SITE_A"]

    assert len(site.tasks) == 1
    assert metrics[-1]["sites"]["SITE_A"]["tasks_completed"] == 1
    assert metrics[-1]["sites"]["SITE_A"]["tasks_reassigned"] == 1


def test_recovered_but_unassigned_task_is_not_counted_reassigned() -> None:
    sim = WarehouseSimulation(config=baseline_config(steps=3, robot_count=1), with_racs=False)

    metrics = sim.run()
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert metrics[1]["baseline_recovery_events"] == [
        {
            "task_id": "SITE_A_T000000",
            "from_robot": "SITE_A_R000",
            "step": 1,
            "reason": "baseline",
        }
    ]
    assert all(step["reassignment_events"] == [] for step in metrics)
    assert metrics[-1]["sites"]["SITE_A"]["tasks_reassigned"] == 0
    assert task.status == TaskStatus.QUEUED


def test_reaction_and_reassignment_timing_are_deterministic() -> None:
    config = baseline_config(steps=4)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["baseline_reaction_step"] for m in first] == [None, 1, None, None]
    assert [m["task_reassignment_step"] for m in first] == [None, None, 2, None]
    assert [m["baseline_recovery_events"] for m in first] == [
        m["baseline_recovery_events"] for m in second
    ]
    assert [m["reassignment_events"] for m in first] == [
        m["reassignment_events"] for m in second
    ]
    assert first[2]["reassignment_events"][0]["reason"] == "baseline"


def test_healthy_run_is_unchanged() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=1,
        base_service_steps=1,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["sites"]["SITE_A"]["tasks_completed"] for m in metrics] == [0, 1, 2, 3]
    assert all(m["baseline_recovery_events"] == [] for m in metrics)


def test_progressive_degradation_trajectory_is_unchanged_by_baseline_recovery() -> None:
    config = baseline_config(steps=4)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["service_capacity_by_step"]["SITE_A_R000"] for m in metrics] == [
        1.0,
        0.5,
        0.5,
        0.5,
    ]
