"""Benchmark execution framework — runs all scenarios and saves results."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

from benchmarks.metrics import (
    BenchmarkResult,
    compute_avg_throughput,
    compute_cascade_radius,
    compute_recovery_time,
)
from simulations.scenarios import (
    crowdstrike_scenario,
    demand_spike,
    lane_backup,
    robot_failure,
)
from simulations.warehouse_sim import compare_with_without_racs

FAULT_SITE = "SITE_A"
FAULT_STEP = 10
SITES = ["SITE_A", "SITE_B", "SITE_C", "SITE_D"]


def _run_robot_failure_bench() -> List[BenchmarkResult]:
    results = []
    for with_racs in (False, True):
        r = robot_failure.run(steps=50, with_racs=with_racs)
        metrics = r["metrics"]
        fault_step = r["fault_step"]
        results.append(BenchmarkResult(
            scenario="robot_failure",
            with_racs=with_racs,
            recovery_time_steps=r["recovery_time_steps"],
            cascade_containment_radius=compute_cascade_radius(metrics, fault_step, FAULT_SITE),
            avg_throughput_post_fault=compute_avg_throughput(metrics, fault_step, SITES),
            peak_queue_depth=max(m["sites"].get(FAULT_SITE, {}).get("queue", 0) for m in metrics),
        ))
    return results


def _run_cascade_bench() -> List[BenchmarkResult]:
    results = []
    no_racs_metrics, racs_metrics = compare_with_without_racs(steps=60)
    for metrics, with_racs in [(no_racs_metrics, False), (racs_metrics, True)]:
        radius = compute_cascade_radius(metrics, FAULT_STEP, FAULT_SITE)
        recovery = compute_recovery_time(metrics, FAULT_STEP, FAULT_SITE)
        avg_t = compute_avg_throughput(metrics, FAULT_STEP, SITES)
        results.append(BenchmarkResult(
            scenario="cascade_prevention",
            with_racs=with_racs,
            recovery_time_steps=recovery,
            cascade_containment_radius=radius,
            avg_throughput_post_fault=avg_t,
            peak_queue_depth=max(m["sites"].get(FAULT_SITE, {}).get("queue", 0) for m in metrics),
            prevention_rate=max(0.0, 1.0 - radius / max(len(SITES) - 1, 1)) if with_racs else 0.0,
        ))
    return results


def _run_crowdstrike_bench() -> List[BenchmarkResult]:
    site_list = ["SITE_A", "SITE_B", "SITE_C", "SITE_D", "SITE_E"]
    no_racs = crowdstrike_scenario.run_without_racs(site_list)
    with_racs_r = crowdstrike_scenario.run_with_racs(site_list)
    return [
        BenchmarkResult(
            scenario="crowdstrike_update_failure",
            with_racs=False,
            safety_violation_count=no_racs["sites_failed"],
            prevention_rate=0.0,
        ),
        BenchmarkResult(
            scenario="crowdstrike_update_failure",
            with_racs=True,
            safety_violation_count=with_racs_r["sites_failed"],
            prevention_rate=with_racs_r["sites_protected"] / len(site_list),
        ),
    ]


def run_all(output_path: str = "benchmarks/baseline_results.json") -> List[dict]:
    print("\n" + "=" * 60)
    print("RACS BENCHMARK SUITE")
    print("=" * 60)

    all_results = []

    print("\n[1/3] Robot failure benchmark...")
    all_results.extend(_run_robot_failure_bench())

    print("\n[2/3] Cascade prevention benchmark...")
    all_results.extend(_run_cascade_bench())

    print("\n[3/3] CrowdStrike update failure benchmark...")
    all_results.extend(_run_crowdstrike_bench())

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "racs_version": "0.1.0",
        "results": [r.to_dict() for r in all_results],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults saved to {output_path}")
    _print_summary(all_results)
    return output["results"]


def _print_summary(results: List[BenchmarkResult]) -> None:
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    for r in results:
        mode = "WITH RACS" if r.with_racs else "NO RACS "
        print(f"  [{mode}] {r.scenario:<35} "
              f"recovery={r.recovery_time_steps or 'N/A':>6} steps  "
              f"cascade_radius={r.cascade_containment_radius or 0:>2}  "
              f"prevention={r.prevention_rate or 0:.0%}")


if __name__ == "__main__":
    run_all()
