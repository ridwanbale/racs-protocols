"""Flagship RACS V1 warehouse degradation experiment.

The experiment compares paired healthy, reactive-baseline, and RACS conditions
for the same workload and physical degradation configuration. It records
descriptive outputs only; it does not claim statistical significance.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import platform
import statistics
import subprocess
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from racs.risk.risk_signals import RiskLevel
from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


EXPERIMENT_NAME = "racs_v1_flagship"
EXPERIMENT_VERSION = "1.0"
CONDITION_HEALTHY = "healthy_control"
CONDITION_BASELINE = "degradation_reactive_baseline"
CONDITION_RACS = "degradation_racs"


@dataclass(frozen=True)
class RacsV1ExperimentConfig:
    """Small V1-specific experiment configuration."""

    scenario: SimulationScenarioConfig
    trial_count: int = 30
    first_seed: int = 1000
    racs_intervention_risk_level: RiskLevel = RiskLevel.MEDIUM
    racs_robot_anomaly_threshold: float = 1.0

    def __post_init__(self) -> None:
        if self.trial_count <= 0:
            raise ValueError("trial_count must be greater than 0")

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(range(self.first_seed, self.first_seed + self.trial_count))


def default_experiment_config(
    trial_count: int = 30,
    first_seed: int = 1000,
) -> RacsV1ExperimentConfig:
    scenario = SimulationScenarioConfig(
        steps=10,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=2,
        seed=first_seed,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 60},
        initial_site_demand={"SITE_A": 1.0},
        tasks_per_step=2.0,
        base_service_steps=2,
        task_deadline_steps=20,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id=None,
        degradation_start_step=0,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )
    return RacsV1ExperimentConfig(
        scenario=scenario,
        trial_count=trial_count,
        first_seed=first_seed,
    )


def run_experiment(
    config: RacsV1ExperimentConfig,
    output_root: Path,
    *,
    experiment_id: Optional[str] = None,
) -> dict[str, Any]:
    experiment_id = experiment_id or _new_experiment_id()
    output_dir = output_root / experiment_id
    output_dir.mkdir(parents=True, exist_ok=False)

    trial_rows: list[dict[str, Any]] = []
    paired_rows: list[dict[str, Any]] = []
    for seed in config.seeds:
        paired_trials = run_paired_trial(config, seed)
        trial_rows.extend(paired_trials)
        paired_rows.append(derive_paired_metrics(paired_trials))

    summary = build_summary(trial_rows, paired_rows)
    manifest = build_manifest(config, experiment_id)

    _write_csv(output_dir / "trials.csv", trial_rows)
    _write_csv(output_dir / "paired_comparison.csv", paired_rows)
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "manifest.json", manifest)

    return {
        "experiment_id": experiment_id,
        "output_dir": str(output_dir),
        "manifest": manifest,
        "trials": trial_rows,
        "paired": paired_rows,
        "summary": summary,
    }


def run_paired_trial(
    config: RacsV1ExperimentConfig,
    seed: int,
) -> list[dict[str, Any]]:
    base = replace(config.scenario, seed=seed)
    healthy_config = replace(
        base,
        degradation_enabled=False,
        degradation_site=None,
        degradation_robot_id=None,
        degradation_rate_per_step=0.0,
        minimum_service_capacity=1.0,
        hard_failure_capacity_threshold=0.0,
        fault_count=0,
    )
    baseline_config = base
    racs_config = base

    return [
        run_trial(
            config,
            seed,
            CONDITION_HEALTHY,
            healthy_config,
            with_racs=False,
        ),
        run_trial(
            config,
            seed,
            CONDITION_BASELINE,
            baseline_config,
            with_racs=False,
        ),
        run_trial(
            config,
            seed,
            CONDITION_RACS,
            racs_config,
            with_racs=True,
        ),
    ]


def run_trial(
    experiment_config: RacsV1ExperimentConfig,
    seed: int,
    condition: str,
    scenario_config: SimulationScenarioConfig,
    *,
    with_racs: bool,
) -> dict[str, Any]:
    sim = WarehouseSimulation(
        config=scenario_config,
        with_racs=with_racs,
        racs_intervention_risk_level=experiment_config.racs_intervention_risk_level,
        racs_robot_anomaly_threshold=experiment_config.racs_robot_anomaly_threshold,
    )
    with redirect_stdout(io.StringIO()):
        metrics = sim.run()

    row = _summarize_trial(seed, condition, scenario_config, metrics)
    row["experiment_name"] = EXPERIMENT_NAME
    row["experiment_version"] = EXPERIMENT_VERSION
    return row


def derive_paired_metrics(trials: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_condition = {row["condition"]: row for row in trials}
    healthy = by_condition[CONDITION_HEALTHY]
    baseline = by_condition[CONDITION_BASELINE]
    racs = by_condition[CONDITION_RACS]
    healthy_completed = healthy["tasks_completed"]

    return {
        "experiment_name": EXPERIMENT_NAME,
        "experiment_version": EXPERIMENT_VERSION,
        "seed": baseline["seed"],
        "completed_task_delta": racs["tasks_completed"] - baseline["tasks_completed"],
        "late_task_delta": racs["tasks_late"] - baseline["tasks_late"],
        "queue_auc_delta": racs["queue_auc"] - baseline["queue_auc"],
        "peak_queue_delta": racs["peak_queue_length"] - baseline["peak_queue_length"],
        "latency_delta": (
            racs["average_completion_latency"]
            - baseline["average_completion_latency"]
        ),
        "downstream_completed_delta": (
            racs.get("downstream_tasks_completed", 0)
            - baseline.get("downstream_tasks_completed", 0)
        ),
        "workstation_starvation_delta": (
            racs.get("workstation_starvation_steps", 0)
            - baseline.get("workstation_starvation_steps", 0)
        ),
        "workstation_buffer_auc_delta": (
            racs.get("workstation_buffer_auc", 0)
            - baseline.get("workstation_buffer_auc", 0)
        ),
        "cascade_starvation_delta": (
            racs.get("cascade_starvation_steps", 0)
            - baseline.get("cascade_starvation_steps", 0)
        ),
        "baseline_throughput_degradation": _throughput_degradation(
            healthy_completed, baseline["tasks_completed"]
        ),
        "racs_throughput_degradation": _throughput_degradation(
            healthy_completed, racs["tasks_completed"]
        ),
        "throughput_degradation_delta": _optional_delta(
            _throughput_degradation(healthy_completed, racs["tasks_completed"]),
            _throughput_degradation(healthy_completed, baseline["tasks_completed"]),
        ),
    }


def build_summary(
    trial_rows: list[dict[str, Any]],
    paired_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    condition_metrics = [
        "tasks_completed",
        "tasks_late",
        "tasks_failed",
        "final_queue_length",
        "peak_queue_length",
        "queue_auc",
        "average_completion_latency",
        "downstream_tasks_completed",
        "preconditioned_workstation_units",
        "transport_delivered_to_workstation",
        "downstream_preconditioned_completed",
        "downstream_transport_completed",
        "workstation_starvation_steps",
        "workstation_buffer_auc",
        "cascade_starvation_steps",
        "affected_resource_count",
    ]
    delta_metrics = [
        "completed_task_delta",
        "late_task_delta",
        "queue_auc_delta",
        "peak_queue_delta",
        "latency_delta",
        "downstream_completed_delta",
        "workstation_starvation_delta",
        "workstation_buffer_auc_delta",
        "cascade_starvation_delta",
        "throughput_degradation_delta",
    ]
    degradation_metrics = [
        "baseline_throughput_degradation",
        "racs_throughput_degradation",
    ]
    by_condition: dict[str, dict[str, Any]] = {}
    for condition in [CONDITION_HEALTHY, CONDITION_BASELINE, CONDITION_RACS]:
        rows = [row for row in trial_rows if row["condition"] == condition]
        by_condition[condition] = {
            metric: _descriptive_stats([row.get(metric, 0) for row in rows])
            for metric in condition_metrics
        }

    deltas = {
        metric: {
            **_descriptive_stats([row.get(metric, 0) for row in paired_rows]),
            **_better_equal_worse(metric, paired_rows),
        }
        for metric in delta_metrics
    }
    degradation = {
        metric: _descriptive_stats([row[metric] for row in paired_rows])
        for metric in degradation_metrics
    }
    return {
        "condition_summaries": by_condition,
        "paired_delta_summaries": deltas,
        "throughput_degradation_summaries": degradation,
        "metric_direction": metric_directions(),
    }


def build_manifest(
    config: RacsV1ExperimentConfig,
    experiment_id: str,
) -> dict[str, Any]:
    return {
        "experiment_name": EXPERIMENT_NAME,
        "experiment_version": EXPERIMENT_VERSION,
        "experiment_id": experiment_id,
        "execution_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit_hash(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "scenario": _json_ready(asdict(config.scenario)),
        "seeds": list(config.seeds),
        "trial_count": config.trial_count,
        "condition_definitions": condition_definitions(),
        "racs_policy": {
            "intervention_risk_level": config.racs_intervention_risk_level.value,
            "robot_anomaly_threshold": config.racs_robot_anomaly_threshold,
            "attribution": "suspect-robot anomaly attribution",
            "policy_note": (
                "V1 policy parameters are explicit and reproducible; they are "
                "not claimed to be proven-optimal calibration."
            ),
        },
        "metric_definitions": metric_definitions(),
        "metric_direction": metric_directions(),
    }


def condition_definitions() -> dict[str, str]:
    return {
        CONDITION_HEALTHY: (
            "Same topology, robot count, workload, simulation duration, and seed; "
            "progressive degradation disabled; RACS predictive intervention disabled."
        ),
        CONDITION_BASELINE: (
            "Progressive degradation enabled with reactive hard-failure recovery; "
            "RACS predictive intervention disabled."
        ),
        CONDITION_RACS: (
            "Same progressive degradation, workload, seed, and trajectory as baseline; "
            "RACS predictive drain/cordon enabled with the same hard-failure fallback mechanics."
        ),
    }


def metric_definitions() -> dict[str, str]:
    return {
        "tasks_created": "Total SimTask objects created by the final simulation step.",
        "tasks_completed": "Total tasks with COMPLETED status by the final simulation step. Higher is better.",
        "tasks_late": "Completed tasks whose completed_step is greater than deadline_step. Lower is better.",
        "tasks_failed": "Total tasks with FAILED status. The V1 simulator does not yet model timeout failure, so this is expected to remain zero unless failure semantics are added.",
        "final_queue_length": "Queued task count at the final simulation step. Lower is better.",
        "peak_queue_length": "Maximum queued task count over simulation steps. Lower is better.",
        "queue_auc": "Sum of queued task count over simulation steps. Lower is better.",
        "average_completion_latency": "Mean completed_step - created_step for completed tasks, in simulation steps. Lower is better.",
        "robot_utilization_summary": "Mean, min, and max of per-robot busy_steps / elapsed_steps at the final step.",
        "completed_task_delta": "RACS completed tasks minus baseline completed tasks for the same seed. Higher is better.",
        "late_task_delta": "RACS late tasks minus baseline late tasks for the same seed. Lower is better.",
        "queue_auc_delta": "RACS queue_auc minus baseline queue_auc for the same seed. Lower is better.",
        "peak_queue_delta": "RACS peak_queue_length minus baseline peak_queue_length for the same seed. Lower is better.",
        "latency_delta": "RACS average_completion_latency minus baseline average_completion_latency. Lower is better.",
        "throughput_degradation_delta": "RACS throughput_degradation minus baseline throughput_degradation. Lower is better.",
        "throughput_degradation": "(healthy.completed - condition.completed) / healthy.completed; None if healthy.completed is zero. Lower is better.",
        "detection_lead_time": "counterfactual_failure_step - risk_detection_step when both are defined; higher positive values mean earlier detection.",
        "intervention_lead_time": "counterfactual_failure_step - intervention_step when both are defined; higher positive values mean earlier intervention.",
        "downstream_tasks_completed": "Total units processed by the downstream workstation. Higher is better.",
        "preconditioned_workstation_units": "Units staged into the workstation buffer at degradation_start_step by scenario preconditioning.",
        "transport_delivered_to_workstation": "Transport-completed AMR tasks deposited into the workstation input buffer during the run.",
        "downstream_preconditioned_completed": "Preconditioned staged units processed by the workstation.",
        "downstream_transport_completed": "AMR-delivered units processed by the workstation.",
        "workstation_starvation_steps": "Steps where the enabled workstation had integer processing entitlement but insufficient delivered input after startup eligibility. Lower is better.",
        "workstation_buffer_auc": "Sum of workstation input-buffer units over simulation steps. Interpretation is scenario-dependent.",
        "cascade_started": "True when downstream workstation starvation occurs at or after degradation onset.",
        "cascade_start_step": "First qualifying downstream starvation step at or after degradation onset, else None.",
        "cascade_starvation_steps": "Count of qualifying downstream starvation steps at or after degradation onset. Steps may be non-contiguous. Lower is better.",
        "affected_resource_count": "1 when impact remains transport-local; 2 once workstation starvation occurs.",
    }


def metric_directions() -> dict[str, str]:
    return {
        "tasks_completed": "higher_better",
        "tasks_late": "lower_better",
        "tasks_failed": "lower_better",
        "final_queue_length": "lower_better",
        "peak_queue_length": "lower_better",
        "queue_auc": "lower_better",
        "average_completion_latency": "lower_better",
        "completed_task_delta": "higher_better",
        "late_task_delta": "lower_better",
        "queue_auc_delta": "lower_better",
        "peak_queue_delta": "lower_better",
        "latency_delta": "lower_better",
        "baseline_throughput_degradation": "lower_better",
        "racs_throughput_degradation": "lower_better",
        "throughput_degradation_delta": "lower_better",
        "downstream_tasks_completed": "higher_better",
        "preconditioned_workstation_units": "context_dependent",
        "transport_delivered_to_workstation": "higher_better",
        "downstream_preconditioned_completed": "context_dependent",
        "downstream_transport_completed": "higher_better",
        "workstation_starvation_steps": "lower_better",
        "workstation_buffer_auc": "context_dependent",
        "cascade_starvation_steps": "lower_better",
        "affected_resource_count": "lower_better",
        "downstream_completed_delta": "higher_better",
        "workstation_starvation_delta": "lower_better",
        "workstation_buffer_auc_delta": "context_dependent",
        "cascade_starvation_delta": "lower_better",
    }


def _summarize_trial(
    seed: int,
    condition: str,
    scenario_config: SimulationScenarioConfig,
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    final = metrics[-1]
    site_ids = list(scenario_config.site_ids)
    queue_by_step = [
        sum(step["sites"][site_id]["queue"] for site_id in site_ids)
        for step in metrics
    ]
    completed_by_step = [
        sum(step["sites"][site_id]["tasks_completed_step"] for site_id in site_ids)
        for step in metrics
    ]
    workstation_buffer_by_step = [
        sum(step["sites"][site_id]["workstation_input_buffer"] for site_id in site_ids)
        for step in metrics
    ]
    workstation_completed_by_step = [
        sum(step["sites"][site_id]["workstation_completed_this_step"] for site_id in site_ids)
        for step in metrics
    ]
    final_site_metrics = [final["sites"][site_id] for site_id in site_ids]
    total_created = sum(site["tasks_created"] for site in final_site_metrics)
    total_completed = sum(site["tasks_completed"] for site in final_site_metrics)
    total_late = sum(site["tasks_late"] for site in final_site_metrics)
    total_failed = sum(site["tasks_failed"] for site in final_site_metrics)
    total_reassigned = sum(site["tasks_reassigned"] for site in final_site_metrics)
    total_predictive_reassigned = sum(
        site["predictive_tasks_reassigned"] for site in final_site_metrics
    )
    total_downstream_completed = sum(
        site["downstream_tasks_completed"] for site in final_site_metrics
    )
    total_preconditioned_units = sum(
        site["preconditioned_workstation_units"] for site in final_site_metrics
    )
    total_transport_delivered = sum(
        site["transport_delivered_to_workstation"] for site in final_site_metrics
    )
    total_downstream_preconditioned = sum(
        site["downstream_preconditioned_completed"] for site in final_site_metrics
    )
    total_downstream_transport = sum(
        site["downstream_transport_completed"] for site in final_site_metrics
    )
    total_starvation_steps = sum(
        site["workstation_starvation_steps"] for site in final_site_metrics
    )
    cascade_start_steps = [
        site["cascade_start_step"]
        for site in final_site_metrics
        if site["cascade_start_step"] is not None
    ]
    cascade_started = any(site["cascade_started"] for site in final_site_metrics)
    avg_latency = _weighted_average(
        [
            (site["avg_completion_latency"], site["tasks_completed"])
            for site in final_site_metrics
        ]
    )
    per_robot_utilization = {}
    per_robot_completed = {}
    for site_id in site_ids:
        site = final["sites"][site_id]
        per_robot_utilization.update(site["per_robot_utilization"])
        per_robot_completed.update(site["per_robot_completed"])

    detection_step = _first_non_none(metrics, "risk_detection_step")
    intervention_step = _first_non_none(metrics, "intervention_step")
    counterfactual_failure_step = final["counterfactual_failure_step"]

    return {
        "seed": seed,
        "condition": condition,
        "degrading_robot_id": final["degrading_robot_id"],
        "degradation_start_step": final["degradation_start_step"],
        "counterfactual_failure_step": counterfactual_failure_step,
        "tasks_created": total_created,
        "tasks_completed": total_completed,
        "tasks_late": total_late,
        "tasks_failed": total_failed,
        "final_queue_length": queue_by_step[-1],
        "peak_queue_length": max(queue_by_step),
        "queue_auc": sum(queue_by_step),
        "average_completion_latency": round(avg_latency, 3),
        "completed_tasks_by_step": completed_by_step,
        "queue_length_by_step": queue_by_step,
        "workstation_buffer_by_step": workstation_buffer_by_step,
        "workstation_completed_by_step": workstation_completed_by_step,
        "downstream_tasks_completed": total_downstream_completed,
        "preconditioned_workstation_units": total_preconditioned_units,
        "transport_delivered_to_workstation": total_transport_delivered,
        "downstream_preconditioned_completed": total_downstream_preconditioned,
        "downstream_transport_completed": total_downstream_transport,
        "workstation_starvation_steps": total_starvation_steps,
        "workstation_buffer_auc": sum(workstation_buffer_by_step),
        "cascade_started": cascade_started,
        "cascade_start_step": min(cascade_start_steps) if cascade_start_steps else None,
        "cascade_starvation_steps": sum(
            site["cascade_starvation_steps"] for site in final_site_metrics
        ),
        "affected_resource_count": max(
            site["affected_resource_count"] for site in final_site_metrics
        ),
        "robot_utilization_summary": _utilization_summary(per_robot_utilization.values()),
        "per_robot_completed": per_robot_completed,
        "per_robot_utilization": per_robot_utilization,
        "baseline_reaction_step": _first_non_none(metrics, "baseline_reaction_step"),
        "tasks_reassigned": total_reassigned,
        "reassignment_events": _collect_events(metrics, "reassignment_events"),
        "baseline_recovery_events": _collect_events(metrics, "baseline_recovery_events"),
        "risk_detection_step": detection_step,
        "risk_detection_score": _first_non_none(metrics, "risk_detection_score"),
        "risk_detection_level": _first_non_none(metrics, "risk_detection_level"),
        "intervention_step": intervention_step,
        "quarantined_robot_id": _first_non_none(metrics, "quarantined_robot_id"),
        "predictive_tasks_reassigned": total_predictive_reassigned,
        "predictive_recovery_events": _collect_events(metrics, "predictive_recovery_events"),
        "detection_lead_time": _lead_time(counterfactual_failure_step, detection_step),
        "intervention_lead_time": _lead_time(counterfactual_failure_step, intervention_step),
    }


def _first_non_none(metrics: list[dict[str, Any]], key: str) -> Any:
    for step in metrics:
        if step.get(key) is not None:
            return step[key]
    return None


def _collect_events(metrics: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    events = []
    for step in metrics:
        events.extend(step.get(key, []))
    return events


def _lead_time(
    counterfactual_failure_step: Optional[int],
    event_step: Optional[int],
) -> Optional[int]:
    if counterfactual_failure_step is None or event_step is None:
        return None
    return counterfactual_failure_step - event_step


def _weighted_average(values: list[tuple[float, int]]) -> float:
    weight = sum(count for _, count in values)
    if weight == 0:
        return 0.0
    return sum(value * count for value, count in values) / weight


def _utilization_summary(values: Iterable[float]) -> dict[str, float]:
    values = list(values)
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": round(sum(values) / len(values), 3),
        "min": min(values),
        "max": max(values),
    }


def _throughput_degradation(
    healthy_completed: int,
    condition_completed: int,
) -> Optional[float]:
    if healthy_completed == 0:
        return None
    return round((healthy_completed - condition_completed) / healthy_completed, 6)


def _optional_delta(
    left: Optional[float],
    right: Optional[float],
) -> Optional[float]:
    if left is None or right is None:
        return None
    return round(left - right, 6)


def _descriptive_stats(values: Iterable[Any]) -> dict[str, Optional[float]]:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return {"mean": None, "median": None, "stdev": None, "min": None, "max": None}
    return {
        "mean": round(statistics.fmean(numeric), 6),
        "median": round(statistics.median(numeric), 6),
        "stdev": round(statistics.stdev(numeric), 6) if len(numeric) > 1 else 0.0,
        "min": min(numeric),
        "max": max(numeric),
    }


def _better_equal_worse(metric: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    direction = metric_directions()[metric]
    values = [row.get(metric, 0) for row in rows if row.get(metric, 0) is not None]
    if direction == "context_dependent":
        equal = sum(1 for value in values if value == 0)
        return {"racs_better_count": 0, "equal_count": equal, "racs_worse_count": 0}
    if direction == "higher_better":
        better = sum(1 for value in values if value > 0)
        worse = sum(1 for value in values if value < 0)
    else:
        better = sum(1 for value in values if value < 0)
        worse = sum(1 for value in values if value > 0)
    equal = sum(1 for value in values if value == 0)
    return {"racs_better_count": better, "equal_count": equal, "racs_worse_count": worse}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(value) for key, value in row.items()})


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(_json_ready(value), sort_keys=True)
    return value


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_json_ready(data), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, RiskLevel):
        return value.value
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _new_experiment_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{EXPERIMENT_NAME}_{timestamp}"


def _git_commit_hash() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the reproducible RACS V1 flagship warehouse experiment."
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=30,
        help="Number of paired seeds/trials to run. Default: 30.",
    )
    parser.add_argument(
        "--first-seed",
        type=int,
        default=1000,
        help="First seed in the contiguous paired seed range. Default: 1000.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results"),
        help="Directory under which a unique experiment output directory is created.",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Optional output subdirectory name. By default a timestamped ID is used.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    config = default_experiment_config(
        trial_count=args.seeds,
        first_seed=args.first_seed,
    )
    result = run_experiment(config, args.output, experiment_id=args.experiment_id)
    print(f"Wrote RACS V1 experiment outputs to {result['output_dir']}")
    print(f"Trials: {len(result['trials'])}")
    print(f"Paired comparisons: {len(result['paired'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
