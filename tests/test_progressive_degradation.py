"""Tests for progressive AMR service-capacity degradation."""

from __future__ import annotations

import random

import pytest

from simulations.warehouse_sim import SimulationScenarioConfig, TaskStatus, WarehouseSimulation


def degradation_config(**overrides) -> SimulationScenarioConfig:
    defaults = dict(
        steps=8,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=1,
        base_service_steps=2,
        task_deadline_steps=20,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=2,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.1,
        hard_failure_capacity_threshold=0.1,
    )
    defaults.update(overrides)
    return SimulationScenarioConfig(**defaults)


def site_metrics(metrics: list[dict]) -> list[dict]:
    return [step["sites"]["SITE_A"] for step in metrics]


def capacities(metrics: list[dict], robot_id: str = "SITE_A_R000") -> list[float]:
    return [step["service_capacity_by_step"][robot_id] for step in metrics]


def test_capacity_is_healthy_before_degradation_onset() -> None:
    config = degradation_config(steps=4, degradation_start_step=2)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert capacities(metrics)[:2] == [1.0, 1.0]


def test_degradation_start_step_zero_is_accepted() -> None:
    config = degradation_config(steps=2, degradation_start_step=0)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert capacities(metrics)[0] == 1.0


def test_degradation_start_step_last_step_is_accepted() -> None:
    config = degradation_config(steps=4, degradation_start_step=3)

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert capacities(metrics) == [1.0, 1.0, 1.0, 1.0]


def test_degradation_start_step_equal_to_steps_is_rejected() -> None:
    with pytest.raises(ValueError, match="degradation_start_step"):
        degradation_config(steps=4, degradation_start_step=4)


def test_capacity_decreases_monotonically_after_onset() -> None:
    config = degradation_config(
        steps=6,
        degradation_start_step=1,
        degradation_rate_per_step=0.2,
        minimum_service_capacity=0.1,
        hard_failure_capacity_threshold=0.1,
    )

    trajectory = capacities(WarehouseSimulation(config=config, with_racs=False).run())

    assert trajectory[1:] == sorted(trajectory[1:], reverse=True)
    assert trajectory[1] == 1.0
    assert trajectory[2] == 0.8


def test_zero_degradation_rate_keeps_capacity_constant() -> None:
    config = degradation_config(
        steps=4,
        degradation_start_step=0,
        degradation_rate_per_step=0.0,
        hard_failure_capacity_threshold=0.0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert capacities(metrics) == [1.0, 1.0, 1.0, 1.0]
    assert metrics[-1]["hard_failure_step"] is None


def test_negative_degradation_rate_is_rejected() -> None:
    with pytest.raises(ValueError, match="degradation_rate_per_step"):
        degradation_config(degradation_rate_per_step=-0.1)


def test_capacity_never_drops_below_configured_minimum() -> None:
    config = degradation_config(
        steps=8,
        degradation_start_step=0,
        degradation_rate_per_step=0.4,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )

    trajectory = capacities(WarehouseSimulation(config=config, with_racs=False).run())

    assert min(trajectory) == 0.2


def test_minimum_service_capacity_zero_is_rejected() -> None:
    with pytest.raises(ValueError, match="minimum_service_capacity"):
        degradation_config(minimum_service_capacity=0.0)


def test_minimum_service_capacity_above_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="minimum_service_capacity"):
        degradation_config(minimum_service_capacity=1.1)


def test_same_config_seed_produces_identical_degradation_trajectory() -> None:
    config = degradation_config(
        steps=6,
        robot_count=3,
        degradation_robot_id=None,
    )

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert [m["degrading_robot_id"] for m in first] == [m["degrading_robot_id"] for m in second]
    assert [m["service_capacity_by_step"] for m in first] == [
        m["service_capacity_by_step"] for m in second
    ]


def test_same_config_seed_selects_same_degrading_robot_for_racs_and_non_racs() -> None:
    config = degradation_config(
        steps=1,
        robot_count=4,
        degradation_robot_id=None,
        degradation_start_step=0,
    )

    no_racs = WarehouseSimulation(config=config, with_racs=False).run()
    with_racs = WarehouseSimulation(config=config, with_racs=True).run()

    assert no_racs[0]["degrading_robot_id"] == with_racs[0]["degrading_robot_id"]


def test_lower_service_capacity_changes_task_completion_timing() -> None:
    healthy = SimulationScenarioConfig(
        steps=5,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        base_service_steps=2,
    )
    degraded = degradation_config(
        steps=5,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        base_service_steps=2,
        degradation_start_step=0,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.1,
        hard_failure_capacity_threshold=0.1,
    )

    healthy_metrics = WarehouseSimulation(config=healthy, with_racs=False).run()
    degraded_metrics = WarehouseSimulation(config=degraded, with_racs=False).run()

    assert site_metrics(healthy_metrics)[2]["tasks_completed"] == 1
    assert site_metrics(degraded_metrics)[2]["tasks_completed"] == 0
    assert site_metrics(degraded_metrics)[3]["tasks_completed"] == 1


def test_degradation_increases_completion_latency_under_workload() -> None:
    config = degradation_config(
        steps=12,
        tasks_per_step=1,
        base_service_steps=1,
        degradation_start_step=0,
        degradation_rate_per_step=0.05,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()
    sim = WarehouseSimulation(config=config, with_racs=False)
    sim.run()
    latencies = [
        task.completion_latency_steps
        for task in sim._sites["SITE_A"].tasks.values()
        if task.status == TaskStatus.COMPLETED and task.completion_latency_steps is not None
    ]

    assert metrics[-1]["sites"]["SITE_A"]["avg_completion_latency"] > 1.0
    assert latencies[-1] > latencies[0]


def test_degradation_can_accumulate_queue_under_workload() -> None:
    config = degradation_config(
        steps=10,
        tasks_per_step=1,
        base_service_steps=1,
        degradation_start_step=0,
        degradation_rate_per_step=0.08,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert site_metrics(metrics)[-1]["queue"] > site_metrics(metrics)[0]["queue"]


def test_hard_failure_occurs_at_capacity_threshold() -> None:
    config = degradation_config(
        steps=5,
        tasks_per_step=0,
        degradation_start_step=0,
        degradation_rate_per_step=0.2,
        minimum_service_capacity=0.4,
        hard_failure_capacity_threshold=0.4,
    )

    sim = WarehouseSimulation(config=config, with_racs=False)
    metrics = sim.run()

    assert metrics[3]["hard_failure_step"] == 3
    assert sim._sites["SITE_A"].robots[0].hard_failure_step == 3


def test_threshold_one_causes_hard_failure_at_onset() -> None:
    config = degradation_config(
        steps=3,
        degradation_start_step=1,
        degradation_rate_per_step=0.0,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=1.0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert metrics[0]["hard_failure_step"] is None
    assert metrics[1]["hard_failure_step"] == 1


def test_threshold_below_minimum_capacity_never_hard_fails() -> None:
    config = degradation_config(
        steps=5,
        degradation_start_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.4,
        hard_failure_capacity_threshold=0.3,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert capacities(metrics)[-1] == 0.4
    assert metrics[-1]["hard_failure_step"] is None


def test_threshold_zero_allows_no_hard_failure_trajectory() -> None:
    config = degradation_config(
        steps=5,
        degradation_start_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.1,
        hard_failure_capacity_threshold=0.0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert metrics[-1]["hard_failure_step"] is None


def test_racs_fallback_recovers_task_after_hard_failure_without_service_progress() -> None:
    config = degradation_config(
        steps=3,
        initial_site_queue={"SITE_A": 1},
        tasks_per_step=0,
        base_service_steps=10,
        degradation_start_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )
    sim = WarehouseSimulation(config=config, with_racs=True)

    metrics = sim.run()
    robot = sim._sites["SITE_A"].robots[0]
    task = sim._sites["SITE_A"].tasks["SITE_A_T000000"]

    assert robot.hard_failed
    assert robot.remaining_service_work == 0.0
    assert robot.current_task_id is None
    assert metrics[1]["fallback_recovery_events"] == [
        {
            "task_id": "SITE_A_T000000",
            "from_robot": "SITE_A_R000",
            "step": 1,
            "reason": "fallback",
        }
    ]
    assert task.status == TaskStatus.QUEUED


def test_healthy_task_service_behavior_is_unchanged_when_degradation_disabled() -> None:
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

    assert [m["tasks_completed"] for m in site_metrics(metrics)] == [0, 1, 2, 3]


def test_binary_fault_scenarios_still_work_when_degradation_is_disabled() -> None:
    config = SimulationScenarioConfig(
        steps=2,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=1,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert len(metrics[1]["faulted_robot_ids"]) == 1
    assert metrics[1]["sites"]["SITE_A"]["faults"] == 1


def test_binary_fault_and_progressive_degradation_cannot_be_combined() -> None:
    with pytest.raises(ValueError, match="binary robot faults"):
        degradation_config(fault_count=1)


def test_degradation_only_config_ignores_unrelated_legacy_fault_default() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_start_step=0,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert metrics[0]["degrading_robot_id"] == "SITE_A_R000"
    assert all(not step["faulted_robot_ids"] for step in metrics)


def test_invalid_degradation_robot_fails_clearly() -> None:
    config = degradation_config(degradation_robot_id="SITE_A_R999")

    with pytest.raises(ValueError, match="degradation_robot_id"):
        WarehouseSimulation(config=config, with_racs=False)


def test_degradation_requires_explicit_site() -> None:
    with pytest.raises(ValueError, match="degradation_site"):
        degradation_config(degradation_site=None)


def test_hard_failure_threshold_above_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="hard_failure_capacity_threshold"):
        degradation_config(hard_failure_capacity_threshold=1.1)


def test_degradation_does_not_mutate_global_random_state() -> None:
    state = random.getstate()
    config = degradation_config(robot_count=3, degradation_robot_id=None)

    WarehouseSimulation(config=config, with_racs=False).run()

    assert random.getstate() == state
