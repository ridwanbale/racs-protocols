"""Tests for the reproducible RACS V1 flagship experiment harness."""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import replace
from pathlib import Path

from racs.risk.risk_signals import RiskLevel
from simulations.experiments.racs_v1 import (
    CONDITION_BASELINE,
    CONDITION_HEALTHY,
    CONDITION_RACS,
    RacsV1ExperimentConfig,
    build_summary,
    default_experiment_config,
    derive_paired_metrics,
    robustness_experiment_config,
    run_experiment,
    run_paired_trial,
    _with_paired_downstream_attribution,
)


def workspace_output_root(name: str) -> Path:
    root = Path("results") / "_pytest_racs_v1" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def test_same_seed_and_config_produces_identical_trial_metrics() -> None:
    config = default_experiment_config(trial_count=1, first_seed=100)

    first = run_paired_trial(config, seed=100)
    second = run_paired_trial(config, seed=100)

    assert first == second


def test_paired_baseline_and_racs_share_physical_degradation() -> None:
    config = default_experiment_config(trial_count=1, first_seed=101)

    rows = {row["condition"]: row for row in run_paired_trial(config, seed=101)}

    assert rows[CONDITION_HEALTHY]["degrading_robot_id"] is None
    assert rows[CONDITION_BASELINE]["degrading_robot_id"] == rows[CONDITION_RACS]["degrading_robot_id"]
    assert (
        rows[CONDITION_BASELINE]["counterfactual_failure_step"]
        == rows[CONDITION_RACS]["counterfactual_failure_step"]
    )
    assert rows[CONDITION_BASELINE]["degradation_start_step"] == rows[CONDITION_RACS]["degradation_start_step"]


def test_healthy_baseline_and_racs_condition_semantics() -> None:
    config = default_experiment_config(trial_count=1, first_seed=102)

    rows = {row["condition"]: row for row in run_paired_trial(config, seed=102)}

    assert rows[CONDITION_HEALTHY]["counterfactual_failure_step"] is None
    assert rows[CONDITION_HEALTHY]["risk_detection_step"] is None
    assert rows[CONDITION_BASELINE]["counterfactual_failure_step"] is not None
    assert rows[CONDITION_BASELINE]["baseline_reaction_step"] is not None
    assert rows[CONDITION_BASELINE]["risk_detection_step"] is None
    assert rows[CONDITION_RACS]["counterfactual_failure_step"] == rows[CONDITION_BASELINE]["counterfactual_failure_step"]


def test_workload_sequence_is_equivalent_before_racs_intervention() -> None:
    config = default_experiment_config(trial_count=1, first_seed=103)

    rows = {row["condition"]: row for row in run_paired_trial(config, seed=103)}
    racs = rows[CONDITION_RACS]
    baseline = rows[CONDITION_BASELINE]
    intervention_step = racs["intervention_step"]

    assert intervention_step is not None
    assert (
        baseline["completed_tasks_by_step"][:intervention_step]
        == racs["completed_tasks_by_step"][:intervention_step]
    )
    assert (
        baseline["queue_length_by_step"][:intervention_step]
        == racs["queue_length_by_step"][:intervention_step]
    )


def test_pairwise_derived_metrics_have_documented_direction() -> None:
    config = default_experiment_config(trial_count=1, first_seed=104)

    paired = derive_paired_metrics(run_paired_trial(config, seed=104))

    assert paired["completed_task_delta"] == (
        next(row for row in run_paired_trial(config, seed=104) if row["condition"] == CONDITION_RACS)["tasks_completed"]
        - next(row for row in run_paired_trial(config, seed=104) if row["condition"] == CONDITION_BASELINE)["tasks_completed"]
    )
    assert "queue_auc_delta" in paired
    assert "baseline_throughput_degradation" in paired
    assert "racs_throughput_degradation" in paired
    assert "throughput_degradation_delta" in paired


def test_rerunning_experiment_produces_same_substantive_outputs() -> None:
    config = default_experiment_config(trial_count=2, first_seed=105)
    output_root = workspace_output_root("deterministic_outputs")

    first = run_experiment(config, output_root, experiment_id="first")
    second = run_experiment(config, output_root, experiment_id="second")

    assert first["trials"] == second["trials"]
    assert first["paired"] == second["paired"]
    assert first["summary"] == second["summary"]
    assert first["manifest"]["execution_timestamp_utc"] != second["manifest"]["execution_timestamp_utc"]
    assert first["manifest"]["scenario"] == second["manifest"]["scenario"]


def test_experiment_writes_expected_machine_readable_outputs() -> None:
    config = default_experiment_config(trial_count=2, first_seed=107)
    output_root = workspace_output_root("machine_readable")

    result = run_experiment(config, output_root, experiment_id="outputs")
    output_dir = output_root / "outputs"

    assert sorted(path.name for path in output_dir.iterdir()) == [
        "manifest.json",
        "paired_comparison.csv",
        "paired_results.csv",
        "summary.json",
        "trials.csv",
        "wip_summary.csv",
    ]
    with (output_dir / "trials.csv").open(newline="", encoding="utf-8") as handle:
        trial_rows = list(csv.DictReader(handle))
    assert len(trial_rows) == 6
    assert {row["condition"] for row in trial_rows} == {
        CONDITION_HEALTHY,
        CONDITION_BASELINE,
        CONDITION_RACS,
    }
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["racs_policy"]["attribution"] == "suspect-robot anomaly attribution"
    assert result["output_dir"].endswith("outputs")


def test_robustness_config_runs_all_wip_levels_and_pairs_arrivals() -> None:
    config = robustness_experiment_config(trial_count=1, first_seed=1000)

    result = run_experiment(
        config,
        workspace_output_root("robustness_wip"),
        experiment_id="robustness",
    )

    assert len(result["trials"]) == 12
    assert len(result["paired"]) == 4
    assert {row["workstation_wip_level"] for row in result["trials"]} == {0, 1, 2, 4}
    for wip in [0, 1, 2, 4]:
        rows = [
            row for row in result["trials"]
            if row["workstation_wip_level"] == wip
        ]
        assert len({row["arrival_sequence_hash"] for row in rows}) == 1
        assert len({tuple(row["workload_arrivals_by_step"]) for row in rows}) == 1
        assert len({row["preconditioned_workstation_units"] for row in rows}) == 1


def test_seeded_arrival_hash_differs_across_robustness_seeds() -> None:
    config = robustness_experiment_config(trial_count=2, first_seed=1000)
    rows = [
        row for row in run_paired_trial(config, 1000, workstation_wip_level=0)
        if row["condition"] == CONDITION_HEALTHY
    ] + [
        row for row in run_paired_trial(config, 1001, workstation_wip_level=0)
        if row["condition"] == CONDITION_HEALTHY
    ]

    assert rows[0]["arrival_sequence_hash"] != rows[1]["arrival_sequence_hash"]
    assert rows[0]["tasks_created"] == rows[1]["tasks_created"] == 51
    assert max(rows[0]["workload_arrivals_by_step"]) <= 2
    assert max(rows[1]["workload_arrivals_by_step"]) <= 2


def test_paired_conditions_share_identical_seeded_arrival_sequence() -> None:
    config = robustness_experiment_config(trial_count=1, first_seed=1002)

    rows = {
        row["condition"]: row
        for row in run_paired_trial(config, 1002, workstation_wip_level=2)
    }

    assert rows[CONDITION_HEALTHY]["arrival_sequence_hash"] == rows[CONDITION_BASELINE]["arrival_sequence_hash"]
    assert rows[CONDITION_BASELINE]["arrival_sequence_hash"] == rows[CONDITION_RACS]["arrival_sequence_hash"]
    assert rows[CONDITION_HEALTHY]["workload_arrivals_by_step"] == rows[CONDITION_BASELINE]["workload_arrivals_by_step"]
    assert rows[CONDITION_BASELINE]["workload_arrivals_by_step"] == rows[CONDITION_RACS]["workload_arrivals_by_step"]
    assert rows[CONDITION_BASELINE]["degrading_robot_id"] == rows[CONDITION_RACS]["degrading_robot_id"]
    assert rows[CONDITION_BASELINE]["counterfactual_failure_step"] == rows[CONDITION_RACS]["counterfactual_failure_step"]


def test_wip_summary_retains_unfavorable_outcomes() -> None:
    config = robustness_experiment_config(trial_count=2, first_seed=1000)

    result = run_experiment(
        config,
        workspace_output_root("unfavorable_robustness"),
        experiment_id="robustness",
    )

    assert len(result["wip_summary"]) == 4
    paired = result["paired"]
    assert len(paired) == 8
    assert {
        (row["seed"], row["workstation_wip_level"])
        for row in paired
    } == {
        (seed, wip)
        for seed in [1000, 1001]
        for wip in [0, 1, 2, 4]
    }
    assert all("outcome_categories" in row for row in paired)


def test_racs_can_have_no_intervention() -> None:
    scenario = replace(
        default_experiment_config(trial_count=1).scenario,
        steps=4,
        initial_site_queue={"SITE_A": 0},
        tasks_per_step=0,
        degradation_rate_per_step=0.5,
        minimum_service_capacity=0.5,
        hard_failure_capacity_threshold=0.5,
    )
    config = RacsV1ExperimentConfig(scenario=scenario, trial_count=1)

    rows = {row["condition"]: row for row in run_paired_trial(config, seed=108)}

    assert rows[CONDITION_RACS]["intervention_step"] is None
    assert rows[CONDITION_RACS]["counterfactual_failure_step"] == 1


def test_racs_can_have_zero_lead_time() -> None:
    scenario = replace(
        default_experiment_config(trial_count=1).scenario,
        degradation_rate_per_step=0.4,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )
    config = RacsV1ExperimentConfig(scenario=scenario, trial_count=1)

    rows = {row["condition"]: row for row in run_paired_trial(config, seed=109)}

    assert rows[CONDITION_RACS]["intervention_lead_time"] == 0


def test_runner_records_unfavorable_racs_metric_without_filtering() -> None:
    trials = [
        {"condition": CONDITION_HEALTHY, "tasks_completed": 10},
        {
            "condition": CONDITION_BASELINE,
            "experiment_name": "x",
            "experiment_version": "1",
            "seed": 1,
            "tasks_completed": 8,
            "tasks_late": 1,
            "queue_auc": 20,
            "peak_queue_length": 5,
            "average_completion_latency": 3.0,
        },
        {
            "condition": CONDITION_RACS,
            "experiment_name": "x",
            "experiment_version": "1",
            "seed": 1,
            "tasks_completed": 7,
            "tasks_late": 2,
            "queue_auc": 25,
            "peak_queue_length": 6,
            "average_completion_latency": 4.0,
        },
    ]

    paired = derive_paired_metrics(trials)

    assert paired["completed_task_delta"] == -1
    assert paired["late_task_delta"] == 1
    assert paired["queue_auc_delta"] == 5
    assert paired["latency_delta"] == 1.0


def test_summary_counts_racs_better_equal_and_worse() -> None:
    paired = [
        {
            "completed_task_delta": 1,
            "late_task_delta": -1,
            "queue_auc_delta": 0,
            "peak_queue_delta": 2,
            "latency_delta": -0.5,
            "baseline_throughput_degradation": 0.2,
            "racs_throughput_degradation": 0.1,
            "throughput_degradation_delta": -0.1,
        },
        {
            "completed_task_delta": -1,
            "late_task_delta": 1,
            "queue_auc_delta": 0,
            "peak_queue_delta": -2,
            "latency_delta": 0.5,
            "baseline_throughput_degradation": 0.2,
            "racs_throughput_degradation": 0.3,
            "throughput_degradation_delta": 0.1,
        },
    ]
    trials = []
    for condition in [CONDITION_HEALTHY, CONDITION_BASELINE, CONDITION_RACS]:
        for idx in range(2):
            trials.append({
                "condition": condition,
                "tasks_completed": 1,
                "tasks_late": 0,
                "tasks_failed": 0,
                "final_queue_length": 0,
                "peak_queue_length": 0,
                "queue_auc": 0,
                "average_completion_latency": 0.0,
                "seed": idx,
            })

    summary = build_summary(trials, paired)

    completed = summary["paired_delta_summaries"]["completed_task_delta"]
    assert completed["racs_better_count"] == 1
    assert completed["racs_worse_count"] == 1
    assert summary["paired_delta_summaries"]["queue_auc_delta"]["equal_count"] == 2


def test_policy_thresholds_are_recorded_in_manifest() -> None:
    config = RacsV1ExperimentConfig(
        scenario=default_experiment_config(trial_count=1).scenario,
        trial_count=1,
        first_seed=110,
        racs_intervention_risk_level=RiskLevel.HIGH,
        racs_robot_anomaly_threshold=2.0,
    )
    output_root = workspace_output_root("policy_manifest")

    result = run_experiment(config, output_root, experiment_id="policy")

    assert result["manifest"]["racs_policy"]["intervention_risk_level"] == "HIGH"
    assert result["manifest"]["racs_policy"]["robot_anomaly_threshold"] == 2.0


def workstation_resilience_config(wip: int = 0) -> RacsV1ExperimentConfig:
    scenario = replace(
        default_experiment_config(trial_count=1).scenario,
        steps=60,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=4,
        seed=1000,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        initial_site_demand={"SITE_A": 1.0},
        tasks_per_step=0.85,
        base_service_steps=4,
        task_deadline_steps=16,
        workstation_enabled=True,
        workstation_processing_rate=0.85,
        initial_workstation_buffer={"SITE_A": 0},
        workstation_buffer_at_degradation_start={"SITE_A": wip},
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=15,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )
    return RacsV1ExperimentConfig(scenario=scenario, trial_count=1)


def test_paired_healthy_starvation_does_not_count_as_degradation_induced_cascade() -> None:
    rows = {
        row["condition"]: row
        for row in run_paired_trial(workstation_resilience_config(wip=0), seed=1000)
    }

    assert rows[CONDITION_HEALTHY]["missed_workstation_processing_units_by_step"][17] == 1
    assert rows[CONDITION_BASELINE]["missed_workstation_processing_units_by_step"][17] == 1
    assert rows[CONDITION_BASELINE]["positive_excess_missed_processing_units_by_step"][17] == 0
    assert rows[CONDITION_BASELINE]["degradation_induced_cascade_start_step"] == 19


def test_degraded_starvation_when_healthy_processes_counts_as_excess_loss() -> None:
    rows = {
        row["condition"]: row
        for row in run_paired_trial(workstation_resilience_config(wip=0), seed=1000)
    }

    assert rows[CONDITION_HEALTHY]["missed_workstation_processing_units_by_step"][19] == 0
    assert rows[CONDITION_BASELINE]["missed_workstation_processing_units_by_step"][19] == 1
    assert rows[CONDITION_BASELINE]["positive_excess_missed_processing_units_by_step"][19] == 1
    assert rows[CONDITION_BASELINE]["cumulative_positive_excess_missed_processing_units"] > 0


def test_phase_shifted_misses_separate_onset_from_net_severity() -> None:
    healthy = {
        "missed_workstation_processing_units_by_step": [1, 0],
        "downstream_completed_by_step_cumulative": [0, 1],
    }
    condition = {
        "missed_workstation_processing_units_by_step": [0, 1],
        "downstream_completed_by_step_cumulative": [1, 1],
    }

    result = _with_paired_downstream_attribution(condition, healthy)

    assert result["positive_excess_missed_processing_units_by_step"] == [0, 1]
    assert result["cumulative_positive_excess_missed_processing_units"] == 1
    assert result["signed_missed_processing_delta_by_step"] == [-1, 1]
    assert result["net_missed_workstation_processing_deficit_vs_healthy"] == 0
    assert result["degradation_induced_cascade_started"] is True
    assert result["degradation_induced_cascade_start_step"] == 1


def test_paired_causal_attribution_uses_identical_processing_cadence_and_wip() -> None:
    rows = {
        row["condition"]: row
        for row in run_paired_trial(workstation_resilience_config(wip=2), seed=1000)
    }

    assert rows[CONDITION_HEALTHY]["preconditioned_workstation_units"] == 2
    assert rows[CONDITION_BASELINE]["preconditioned_workstation_units"] == 2
    assert rows[CONDITION_RACS]["preconditioned_workstation_units"] == 2
    assert len(rows[CONDITION_HEALTHY]["missed_workstation_processing_units_by_step"]) == len(
        rows[CONDITION_BASELINE]["missed_workstation_processing_units_by_step"]
    )
    assert rows[CONDITION_BASELINE]["degradation_induced_cascade_start_step"] == 38
    assert rows[CONDITION_RACS]["degradation_induced_cascade_start_step"] == 39


def test_paired_metrics_include_excess_downstream_service_loss_delta() -> None:
    paired = derive_paired_metrics(
        run_paired_trial(workstation_resilience_config(wip=2), seed=1000)
    )

    assert "positive_excess_missed_processing_delta" in paired
    assert paired["positive_excess_missed_processing_delta"] == 0
    assert "net_missed_workstation_processing_deficit_delta" in paired
    assert paired["net_missed_workstation_processing_deficit_delta"] == 0
    assert "downstream_completion_deficit_delta" in paired
