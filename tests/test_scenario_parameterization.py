"""Tests for explicit warehouse simulation scenario parameterization."""

from __future__ import annotations

import random

import pytest

import benchmarks.runner as benchmark_runner
from benchmarks.metrics import BenchmarkResult
from simulations.scenarios import demand_spike, lane_backup, robot_failure
from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


def faulted_ids(metrics: list[dict]) -> list[str]:
    return [
        robot_id
        for step_metrics in metrics
        for robot_id in step_metrics.get("faulted_robot_ids", [])
    ]


def max_faults(metrics: list[dict], site_id: str) -> int:
    return max(step["sites"][site_id]["faults"] for step in metrics)


def test_robot_failure_faults_exactly_one_robot() -> None:
    result = robot_failure.run(steps=12, with_racs=False)

    assert result["fault_count"] == 1
    assert len(faulted_ids(result["metrics"])) == 1
    assert max_faults(result["metrics"], "SITE_A") == 1


def test_lane_backup_does_not_inject_robot_faults() -> None:
    result = lane_backup.run(steps=8, with_racs=False)

    assert result["fault_count"] == 0
    assert faulted_ids(result["metrics"]) == []
    assert max_faults(result["metrics"], "SITE_A") == 0


def test_demand_spike_does_not_inject_robot_faults() -> None:
    result = demand_spike.run(steps=18, with_racs=False)

    assert result["fault_count"] == 0
    assert faulted_ids(result["metrics"]) == []
    assert max_faults(result["metrics"], "SITE_A") == 0


def test_fault_count_controls_how_many_robots_are_faulted() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=3,
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert len(faulted_ids(metrics)) == 3
    assert max_faults(metrics, "SITE_A") == 3


def test_same_config_and_seed_selects_identical_fault_robot_ids() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=3,
    )

    first = WarehouseSimulation(config=config, with_racs=False).run()
    second = WarehouseSimulation(config=config, with_racs=False).run()

    assert faulted_ids(first) == faulted_ids(second)


def test_baseline_and_racs_share_identical_fault_selection() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=3,
    )

    baseline = WarehouseSimulation(config=config, with_racs=False).run()
    racs = WarehouseSimulation(config=config, with_racs=True).run()

    assert faulted_ids(baseline) == faulted_ids(racs)


def test_explicit_fault_robot_ids_are_honored_exactly() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=2,
        fault_robot_ids=("SITE_A_R003", "SITE_A_R007"),
    )

    metrics = WarehouseSimulation(config=config, with_racs=False).run()

    assert faulted_ids(metrics) == ["SITE_A_R003", "SITE_A_R007"]


def test_invalid_fault_robot_ids_fail_clearly() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=1,
        fault_robot_ids=("SITE_B_R001",),
    )

    with pytest.raises(ValueError, match="fault_robot_ids"):
        WarehouseSimulation(config=config, with_racs=False).run()


def test_simulation_does_not_mutate_global_random_state() -> None:
    state = random.getstate()
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        seed=123,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=3,
    )

    WarehouseSimulation(config=config, with_racs=False).run()

    assert random.getstate() == state


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"steps": 0}, "steps"),
        ({"step_duration_s": 0}, "step_duration_s"),
        ({"robot_count": 0}, "robot_count"),
        ({"fault_count": -1}, "fault_count"),
        ({"fault_step": -1}, "fault_step"),
        ({"fault_step": 50}, "fault_step"),
        ({"fault_site": "UNKNOWN"}, "fault_site"),
        ({"fault_count": 21}, "fault_count"),
        ({"initial_site_queue": {"SITE_A": -1}}, "initial_site_queue"),
        ({"initial_site_queue": {"UNKNOWN": 1}}, "initial_site_queue"),
        ({"initial_site_demand": {"SITE_A": -0.1}}, "initial_site_demand"),
        ({"initial_site_demand": {"UNKNOWN": 1.0}}, "initial_site_demand"),
        (
            {"fault_count": 2, "fault_robot_ids": ("SITE_A_R001",)},
            "fault_robot_ids",
        ),
        (
            {"fault_count": 2, "fault_robot_ids": ("SITE_A_R001", "SITE_A_R001")},
            "fault_robot_ids",
        ),
    ],
)
def test_invalid_scenario_configuration_is_rejected(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SimulationScenarioConfig(**kwargs)


def test_fault_step_zero_is_accepted() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        fault_site="SITE_A",
        fault_step=0,
    )

    assert config.fault_step == 0


def test_fault_step_last_step_is_accepted() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        fault_site="SITE_A",
        fault_step=2,
    )

    assert config.fault_step == config.steps - 1


def test_fault_step_equal_to_steps_is_rejected() -> None:
    with pytest.raises(ValueError, match="fault_step"):
        SimulationScenarioConfig(
            steps=3,
            site_ids=("SITE_A",),
            fault_site="SITE_A",
            fault_step=3,
        )


def test_fault_count_equal_to_robot_count_is_accepted() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        site_ids=("SITE_A",),
        robot_count=2,
        fault_site="SITE_A",
        fault_step=1,
        fault_count=2,
    )

    assert config.fault_count == config.robot_count


def test_fault_count_greater_than_robot_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="fault_count"):
        SimulationScenarioConfig(
            steps=3,
            site_ids=("SITE_A",),
            robot_count=2,
            fault_site="SITE_A",
            fault_step=1,
            fault_count=3,
        )


def test_empty_site_ids_is_rejected_clearly() -> None:
    with pytest.raises(ValueError, match="fault_site"):
        SimulationScenarioConfig(site_ids=())


def test_config_takes_precedence_over_legacy_constructor_arguments() -> None:
    config = SimulationScenarioConfig(
        steps=3,
        step_duration_s=0.5,
        site_ids=("CONFIG_SITE",),
        seed=123,
        fault_site="CONFIG_SITE",
        fault_step=1,
        fault_count=1,
    )

    sim = WarehouseSimulation(
        site_ids=["LEGACY_SITE"],
        seed=999,
        step_duration_s=10.0,
        config=config,
        with_racs=False,
    )
    metrics = sim.run()

    assert list(metrics[0]["sites"]) == ["CONFIG_SITE"]
    assert len(faulted_ids(metrics)) == 1
    assert faulted_ids(metrics) == faulted_ids(
        WarehouseSimulation(config=config, with_racs=False).run()
    )


def test_legacy_constructor_and_run_arguments_still_work() -> None:
    sim = WarehouseSimulation(site_ids=["SITE_A"], with_racs=False, seed=123)

    metrics = sim.run(
        steps=3,
        fault_site="SITE_A",
        fault_at_step=1,
        fault_count=1,
    )

    assert len(metrics) == 3
    assert len(faulted_ids(metrics)) == 1


def test_benchmark_robot_failure_uses_actual_scenario_fault_step(monkeypatch) -> None:
    observed_fault_steps: list[int] = []

    def fake_robot_failure_run(steps: int, with_racs: bool) -> dict:
        return {
            "fault_step": 8,
            "recovery_time_steps": 0,
            "metrics": [
                {"step": 7, "sites": {"SITE_A": {"queue": 10, "throughput": 1.0}}},
                {"step": 8, "sites": {"SITE_A": {"queue": 20, "throughput": 0.8}}},
            ],
        }

    def capture_cascade_radius(metrics, fault_step, fault_site, affected_threshold=0.1):
        observed_fault_steps.append(fault_step)
        return 0

    def capture_avg_throughput(metrics, start_step, site_ids):
        observed_fault_steps.append(start_step)
        return 1.0

    monkeypatch.setattr(benchmark_runner.robot_failure, "run", fake_robot_failure_run)
    monkeypatch.setattr(benchmark_runner, "compute_cascade_radius", capture_cascade_radius)
    monkeypatch.setattr(benchmark_runner, "compute_avg_throughput", capture_avg_throughput)

    results = benchmark_runner._run_robot_failure_bench()

    assert all(isinstance(result, BenchmarkResult) for result in results)
    assert observed_fault_steps == [8, 8, 8, 8]
