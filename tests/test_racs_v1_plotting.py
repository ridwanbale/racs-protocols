"""Tests for read-only RACS V1 result plotting utilities."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

from simulations.experiments.plot_racs_v1 import generate_plots


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_plotter_reads_existing_results_and_writes_pngs() -> None:
    root = Path("results") / "_pytest_plotting"
    if root.exists():
        shutil.rmtree(root)
    result_dir = root / "result"
    output_dir = root / "figures"
    result_dir.mkdir(parents=True)

    paired_rows = []
    trial_rows = []
    for wip in [0, 1, 2, 4]:
        paired_rows.append({
            "seed": 1000 + wip,
            "workstation_wip_level": wip,
            "queue_auc_delta": -wip,
            "latency_delta": -0.1 * wip,
            "net_missed_workstation_processing_deficit_delta": -1 if wip else 0,
            "racs_before_baseline_cascade_fraction": "",
            "baseline_degradation_induced_cascade_start_step": 20 + wip,
            "racs_degradation_induced_cascade_start_step": 21 + wip,
        })
        for condition in [
            "healthy_control",
            "degradation_reactive_baseline",
            "degradation_racs",
        ]:
            trial_rows.append({
                "seed": 1000 + wip,
                "workstation_wip_level": wip,
                "condition": condition,
                "queue_auc": 100 + wip,
                "degradation_start_step": 15 if condition != "healthy_control" else "",
                "risk_detection_step": 18 if condition == "degradation_racs" else "",
                "intervention_step": 19 if condition == "degradation_racs" else "",
                "counterfactual_failure_step": 23 if condition != "healthy_control" else "",
            })

    wip_rows = [
        {
            "workstation_wip_level": wip,
            "racs_before_baseline_cascade_fraction": frac,
        }
        for wip, frac in [(0, 0.5), (1, 0.6), (2, 0.7), (4, 0.8)]
    ]
    write_csv(result_dir / "paired_results.csv", paired_rows)
    write_csv(result_dir / "trials.csv", trial_rows)
    write_csv(result_dir / "wip_summary.csv", wip_rows)

    info = generate_plots(result_dir, output_dir)

    assert info["representative_wip"] == 2
    for name in [
        "queue_auc_delta_distribution.png",
        "latency_delta_distribution.png",
        "intervention_before_propagation_by_wip.png",
        "baseline_cascade_timing_by_wip.png",
        "downstream_net_deficit_delta_distribution.png",
        "representative_timeline.png",
    ]:
        data = (output_dir / name).read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
