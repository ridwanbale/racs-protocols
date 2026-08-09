"""Tests for the minimal deterministic task/service model."""

from __future__ import annotations

import random

import pytest

from simulations.warehouse_sim import SimulationScenarioConfig, TaskStatus, WarehouseSimulation


def single_site_config(**overrides) -> SimulationScenarioConfig:
    defaults = dict(
        steps=5,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=1,
        base_service_steps=2,
        task_deadline_steps=5,
    )
    defaults.update(overrides)
    return SimulationScenarioConfig(**defaults)


def site_metrics(metrics: list[dict]) -> list[dict]:
    return [step["sites"]["SITE_A"] for step in metrics]


def test_healthy_task_arrivals_are_deterministic() -> None:
    config = single_site_config(steps=4, tasks_per_step=2)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["tasks_created_step"] for m in site_metrics(first)] == [2, 2, 2, 2]
    assert [m["tasks_created_step"] for m in site_metrics(first)] == [
        m["tasks_created_step"] for m in site_metrics(second)
    ]


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (0.5, [0, 1, 0, 1]),
        (1.5, [1, 2, 1, 2]),
        (2.5, [2, 3, 2, 3]),
        (2.0, [2, 2, 2, 2]),
    ],
)
def test_fractional_arrivals_use_deterministic_credit(rate: float, expected: list[int]) -> None:
    config = single_site_config(steps=4, tasks_per_step=rate)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["tasks_created_step"] for m in site_metrics(metrics)] == expected


def test_fractional_arrival_sequence_is_repeatable() -> None:
    config = single_site_config(steps=6, tasks_per_step=2.5)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["tasks_created_step"] for m in site_metrics(first)] == [
        m["tasks_created_step"] for m in site_metrics(second)
    ]


def test_multiple_sites_maintain_independent_arrival_credit() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A", "SITE_B"),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0, "SITE_B": 0},
        initial_site_demand={"SITE_A": 0.5, "SITE_B": 1.5},
        tasks_per_step=1.0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["sites"]["SITE_A"]["tasks_created_step"] for m in metrics] == [0, 1, 0, 1]
    assert [m["sites"]["SITE_B"]["tasks_created_step"] for m in metrics] == [1, 2, 1, 2]


def test_negative_tasks_per_step_is_rejected() -> None:
    with pytest.raises(ValueError, match="tasks_per_step"):
        single_site_config(tasks_per_step=-0.1)


def test_tasks_are_assigned_to_available_robots() -> None:
    config = single_site_config(steps=1, robot_count=2, tasks_per_step=2)
    sim = WarehouseSimulation(config=config, with_racs=False)

    sim.run()

    in_progress = [
        task for task in sim._sites["SITE_A"].tasks.values()
        if task.status == TaskStatus.IN_PROGRESS
    ]
    assert len(in_progress) == 2
    assert {task.assigned_robot_id for task in in_progress} == {
        "SITE_A_R000",
        "SITE_A_R001",
    }


def test_one_robot_cannot_process_two_tasks_simultaneously() -> None:
    config = single_site_config(steps=1, robot_count=1, tasks_per_step=3)
    sim = WarehouseSimulation(config=config, with_racs=False)

    sim.run()

    site = sim._sites["SITE_A"]
    assert sum(1 for robot in site.robots if robot.current_task_id is not None) == 1
    assert site.queued_task_count == 2


def test_tasks_require_nonzero_service_time() -> None:
    config = single_site_config(steps=2, robot_count=1, tasks_per_step=1, base_service_steps=1)
    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(metrics)[0]["tasks_completed"] == 0
    assert site_metrics(metrics)[1]["tasks_completed"] == 1


def test_completion_latency_is_deterministic() -> None:
    config = single_site_config(steps=4, robot_count=1, tasks_per_step=1, base_service_steps=2)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(first)[2]["avg_completion_latency"] == 2.0
    assert [m["avg_completion_latency"] for m in site_metrics(first)] == [
        m["avg_completion_latency"] for m in site_metrics(second)
    ]


@pytest.mark.parametrize(
    ("step_duration_s", "expected_latency_s"),
    [
        (1.0, 3.0),
        (0.5, 1.5),
        (2.0, 6.0),
    ],
)
def test_telemetry_latency_reports_simulated_seconds(
    step_duration_s: float,
    expected_latency_s: float,
) -> None:
    config = single_site_config(
        steps=4,
        step_duration_s=step_duration_s,
        robot_count=1,
        tasks_per_step=1,
        base_service_steps=3,
    )
    sim = WarehouseSimulation(config=config, with_racs=False)

    sim.run()
    telemetry = sim._sites["SITE_A"].to_telemetry(
        timestamp=3 * step_duration_s,
        step_duration_s=step_duration_s,
    )

    assert telemetry.avg_task_latency_s == expected_latency_s


def test_deadline_boundary_completion_is_not_late() -> None:
    config = single_site_config(
        steps=2,
        robot_count=1,
        tasks_per_step=1,
        base_service_steps=1,
        task_deadline_steps=1,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(metrics)[1]["tasks_completed"] == 1
    assert site_metrics(metrics)[1]["tasks_late"] == 0


def test_completion_after_deadline_is_late() -> None:
    config = single_site_config(
        steps=3,
        robot_count=1,
        tasks_per_step=1,
        base_service_steps=2,
        task_deadline_steps=1,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(metrics)[2]["tasks_completed"] == 1
    assert site_metrics(metrics)[2]["tasks_late"] == 1


def test_queued_task_count_matches_telemetry_queue_length() -> None:
    config = single_site_config(steps=1, robot_count=1, tasks_per_step=3)
    sim = WarehouseSimulation(config=config, with_racs=False)

    metrics = sim.run()
    telemetry = sim._sites["SITE_A"].to_telemetry(timestamp=0.0)

    assert site_metrics(metrics)[0]["queue"] == 2
    assert telemetry.queue_length == sim._sites["SITE_A"].queued_task_count


def test_robot_fault_queue_shock_creates_real_tasks() -> None:
    config = single_site_config(
        steps=2,
        robot_count=2,
        tasks_per_step=0,
        fault_step=1,
        fault_count=1,
        task_deadline_steps=5,
    )
    sim = WarehouseSimulation(config=config, with_racs=False)

    metrics = sim.run()
    site = sim._sites["SITE_A"]
    shock_tasks = [task for task in site.tasks.values() if task.created_step == 1]

    assert len(shock_tasks) == 30
    assert {task.deadline_step for task in shock_tasks} == {6}
    assert site.queued_task_count == site_metrics(metrics)[1]["queue"]


def test_non_racs_downstream_cascade_creates_real_tasks() -> None:
    config = SimulationScenarioConfig(
        steps=2,
        site_ids=("SITE_A", "SITE_B"),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=1,
        initial_site_queue={"SITE_A": 2, "SITE_B": 0},
        tasks_per_step=0,
        task_deadline_steps=5,
    )
    sim = WarehouseSimulation(config=config, with_racs=False)

    metrics = sim.run()
    site_b = sim._sites["SITE_B"]
    cascade_tasks = [task for task in site_b.tasks.values() if task.created_step == 1]

    assert len(cascade_tasks) == 3
    assert {task.deadline_step for task in cascade_tasks} == {6}
    assert site_b.queued_task_count == metrics[1]["sites"]["SITE_B"]["queue"]


def test_faulted_robot_keeps_in_progress_task_until_recovery_policy_exists() -> None:
    config = single_site_config(
        steps=2,
        robot_count=1,
        tasks_per_step=1,
        base_service_steps=3,
        fault_step=1,
        fault_count=1,
        fault_robot_ids=("SITE_A_R000",),
    )
    sim = WarehouseSimulation(config=config, with_racs=False)

    sim.run()
    site = sim._sites["SITE_A"]
    robot = site.robots[0]
    first_task = site.tasks["SITE_A_T000000"]

    assert robot.faulted
    assert robot.current_task_id == first_task.task_id
    assert first_task.status == TaskStatus.IN_PROGRESS


def test_healthy_run_has_deterministic_completion_count() -> None:
    config = single_site_config(steps=6, robot_count=2, tasks_per_step=2, base_service_steps=2)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(first)[-1]["tasks_completed"] == 4
    assert site_metrics(first)[-1]["tasks_completed"] == site_metrics(second)[-1]["tasks_completed"]


def test_same_config_seed_produces_identical_arrival_and_completion_sequences() -> None:
    config = single_site_config(steps=5, robot_count=2, tasks_per_step=2, base_service_steps=2)

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert [
        (m["tasks_created_step"], m["tasks_completed_step"], m["queue"])
        for m in site_metrics(first)
    ] == [
        (m["tasks_created_step"], m["tasks_completed_step"], m["queue"])
        for m in site_metrics(second)
    ]


def test_task_service_model_does_not_mutate_global_random_state() -> None:
    state = random.getstate()
    config = single_site_config(steps=5, robot_count=2, tasks_per_step=2)

    WarehouseSimulation(config=config, with_racs=False).run()

    assert random.getstate() == state


def test_per_robot_completion_and_busy_step_accounting() -> None:
    config = single_site_config(steps=4, robot_count=1, tasks_per_step=1, base_service_steps=1)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()
    final = site_metrics(metrics)[-1]

    assert final["per_robot_completed"]["SITE_A_R000"] == 3
    assert final["per_robot_busy_steps"]["SITE_A_R000"] == 3
    assert final["per_robot_utilization"]["SITE_A_R000"] == 0.75
