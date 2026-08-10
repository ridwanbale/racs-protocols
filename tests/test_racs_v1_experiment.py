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
    run_experiment,
    run_paired_trial,
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
        "summary.json",
        "trials.csv",
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
