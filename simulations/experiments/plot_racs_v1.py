"""Generate RACS V1 reference plots from an existing experiment result.

This module is intentionally read-only with respect to simulation state: it
loads CSV/JSON outputs from a completed experiment directory and writes PNG
figures. It does not run simulations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import struct
import zlib
from pathlib import Path
from typing import Any, Iterable


DEFAULT_RESULT_DIR = Path("results/seeded_robustness_30")
DEFAULT_OUTPUT_DIR = Path("docs/experiments/figures/racs_v1")
WIP_LEVELS = [0, 1, 2, 4]

WHITE = (255, 255, 255)
BLACK = (35, 35, 35)
GREY = (210, 210, 210)
LIGHT_GREY = (238, 238, 238)
BLUE = (45, 112, 179)
RED = (190, 67, 67)
GREEN = (62, 145, 91)
PURPLE = (113, 84, 160)
ORANGE = (214, 130, 45)


FONT = {
    " ": ["000", "000", "000", "000", "000"],
    "-": ["000", "000", "111", "000", "000"],
    ".": ["0", "0", "0", "0", "1"],
    ":": ["0", "1", "0", "1", "0"],
    "/": ["001", "001", "010", "100", "100"],
    "(": ["01", "10", "10", "10", "01"],
    ")": ["10", "01", "01", "01", "10"],
    "=": ["000", "111", "000", "111", "000"],
    "+": ["000", "010", "111", "010", "000"],
    ">": ["100", "010", "001", "010", "100"],
    "<": ["001", "010", "100", "010", "001"],
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
    "A": ["111", "101", "111", "101", "101"],
    "B": ["110", "101", "110", "101", "110"],
    "C": ["111", "100", "100", "100", "111"],
    "D": ["110", "101", "101", "101", "110"],
    "E": ["111", "100", "110", "100", "111"],
    "F": ["111", "100", "110", "100", "100"],
    "G": ["111", "100", "101", "101", "111"],
    "H": ["101", "101", "111", "101", "101"],
    "I": ["111", "010", "010", "010", "111"],
    "J": ["001", "001", "001", "101", "111"],
    "K": ["101", "101", "110", "101", "101"],
    "L": ["100", "100", "100", "100", "111"],
    "M": ["101", "111", "111", "101", "101"],
    "N": ["101", "111", "111", "111", "101"],
    "O": ["111", "101", "101", "101", "111"],
    "P": ["111", "101", "111", "100", "100"],
    "Q": ["111", "101", "101", "111", "001"],
    "R": ["111", "101", "111", "110", "101"],
    "S": ["111", "100", "111", "001", "111"],
    "T": ["111", "010", "010", "010", "010"],
    "U": ["101", "101", "101", "101", "111"],
    "V": ["101", "101", "101", "101", "010"],
    "W": ["101", "101", "111", "111", "101"],
    "X": ["101", "101", "010", "101", "101"],
    "Y": ["101", "101", "010", "010", "010"],
    "Z": ["111", "001", "010", "100", "111"],
}


class Canvas:
    def __init__(self, width: int = 1100, height: int = 720) -> None:
        self.width = width
        self.height = height
        self.pixels = [[WHITE for _ in range(width)] for _ in range(height)]

    def set(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y][x] = color

    def line(self, x1: int, y1: int, x2: int, y2: int, color=BLACK) -> None:
        dx = abs(x2 - x1)
        dy = -abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx + dy
        while True:
            self.set(x1, y1, color)
            if x1 == x2 and y1 == y2:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x1 += sx
            if e2 <= dx:
                err += dx
                y1 += sy

    def rect(self, x: int, y: int, w: int, h: int, color=BLACK, fill=True) -> None:
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.set(xx, yy, color)
        else:
            self.line(x, y, x + w, y, color)
            self.line(x, y + h, x + w, y + h, color)
            self.line(x, y, x, y + h, color)
            self.line(x + w, y, x + w, y + h, color)

    def circle(self, cx: int, cy: int, r: int, color=BLUE) -> None:
        for y in range(cy - r, cy + r + 1):
            for x in range(cx - r, cx + r + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    self.set(x, y, color)

    def text(self, x: int, y: int, text: str, color=BLACK, scale: int = 3) -> None:
        cursor = x
        for ch in text.upper():
            rows = FONT.get(ch, FONT[" "])
            for row_idx, row in enumerate(rows):
                for col_idx, bit in enumerate(row):
                    if bit == "1":
                        self.rect(
                            cursor + col_idx * scale,
                            y + row_idx * scale,
                            scale,
                            scale,
                            color,
                        )
            cursor += (max(len(row) for row in rows) + 1) * scale

    def save_png(self, path: Path) -> None:
        rows = []
        for row in self.pixels:
            raw = bytearray([0])
            for r, g, b in row:
                raw.extend([r, g, b])
            rows.append(bytes(raw))
        data = zlib.compress(b"".join(rows), level=9)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
            self._chunk(handle, b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
            self._chunk(handle, b"IDAT", data)
            self._chunk(handle, b"IEND", b"")

    @staticmethod
    def _chunk(handle: Any, name: bytes, data: bytes) -> None:
        handle.write(struct.pack(">I", len(data)))
        handle.write(name)
        handle.write(data)
        handle.write(struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF))


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [{key: parse_cell(value) for key, value in row.items()} for row in csv.DictReader(handle)]


def parse_cell(value: str) -> Any:
    if value == "":
        return None
    if value in {"True", "False"}:
        return value == "True"
    try:
        if "." in value or "e" in value.lower():
            return float(value)
        return int(value)
    except ValueError:
        return value


def scale(value: float, low: float, high: float, start: int, end: int) -> int:
    if high == low:
        return (start + end) // 2
    return int(start + (value - low) * (end - start) / (high - low))


def draw_axes(canvas: Canvas, title: str, subtitle: str, x_label: str, y_label: str) -> None:
    canvas.text(45, 28, title, BLACK, 4)
    canvas.text(45, 62, subtitle, BLACK, 2)
    canvas.text(360, 680, x_label, BLACK, 2)
    canvas.text(18, 330, y_label, BLACK, 2)
    canvas.line(90, 620, 1030, 620, BLACK)
    canvas.line(90, 110, 90, 620, BLACK)


def distribution_plot(rows: list[dict[str, Any]], metric: str, title: str, path: Path) -> None:
    values = [float(row[metric]) for row in rows if row.get(metric) is not None]
    low = min(min(values), 0)
    high = max(max(values), 0)
    pad = max((high - low) * 0.08, 1.0)
    low -= pad
    high += pad
    canvas = Canvas()
    draw_axes(canvas, title, "RACS - BASELINE, N=120, NEGATIVE FAVORS RACS", "DELTA VALUE", "PAIR")
    zero_x = scale(0, low, high, 90, 1030)
    canvas.line(zero_x, 110, zero_x, 620, RED)
    sorted_values = sorted(values)
    for idx, value in enumerate(sorted_values):
        x = scale(value, low, high, 90, 1030)
        y = scale(idx, 0, max(len(sorted_values) - 1, 1), 610, 125)
        canvas.circle(x, y, 4, BLUE if value <= 0 else RED)
    canvas.text(90, 635, f"MIN {min(values):.3F}", BLACK, 2)
    canvas.text(470, 635, "ZERO", RED, 2)
    canvas.text(820, 635, f"MAX {max(values):.3F}", BLACK, 2)
    canvas.save_png(path)


def bar_plot(wip_rows: list[dict[str, Any]], path: Path) -> None:
    canvas = Canvas()
    draw_axes(canvas, "INTERVENTION BEFORE PROPAGATION BY WIP", "FRACTION OF 30 SEEDS", "DISTURBANCE TIME WIP", "FRACTION")
    bar_w = 115
    for idx, row in enumerate(wip_rows):
        frac = float(row["racs_before_baseline_cascade_fraction"])
        x = 160 + idx * 210
        h = int(frac * 430)
        y = 620 - h
        canvas.rect(x, y, bar_w, h, GREEN)
        canvas.text(x + 25, 632, str(row["workstation_wip_level"]), BLACK, 3)
        canvas.text(x - 5, y - 28, f"{frac:.3F}", BLACK, 2)
    for frac in [0.0, 0.5, 1.0]:
        y = scale(frac, 0, 1, 620, 190)
        canvas.line(90, y, 1030, y, LIGHT_GREY)
        canvas.text(48, y - 8, f"{frac:.1F}", BLACK, 2)
    canvas.save_png(path)


def cascade_timing_plot(rows: list[dict[str, Any]], path: Path) -> None:
    canvas = Canvas()
    draw_axes(canvas, "BASELINE CASCADE TIMING BY WIP", "RAW SEED DISTRIBUTIONS, N=30 PER WIP", "WIP LEVEL", "START STEP")
    for idx, wip in enumerate(WIP_LEVELS):
        vals = [
            int(row["baseline_degradation_induced_cascade_start_step"])
            for row in rows
            if row["workstation_wip_level"] == wip
            and row["baseline_degradation_induced_cascade_start_step"] is not None
        ]
        x = 175 + idx * 220
        for j, value in enumerate(vals):
            y = scale(value, 15, 60, 620, 135)
            canvas.circle(x + (j % 5 - 2) * 9, y, 4, PURPLE)
        med_y = scale(statistics.median(vals), 15, 60, 620, 135)
        canvas.line(x - 55, med_y, x + 55, med_y, ORANGE)
        canvas.text(x - 15, 632, str(wip), BLACK, 3)
    for step in [15, 30, 45, 60]:
        y = scale(step, 15, 60, 620, 135)
        canvas.line(90, y, 1030, y, LIGHT_GREY)
        canvas.text(45, y - 8, str(step), BLACK, 2)
    canvas.save_png(path)


def representative_seed(rows: list[dict[str, Any]]) -> tuple[int, int]:
    wip2 = [
        row for row in rows
        if row["workstation_wip_level"] == 2
        and row["condition"] == "degradation_reactive_baseline"
    ]
    median_queue = statistics.median(float(row["queue_auc"]) for row in wip2)
    selected = min(
        wip2,
        key=lambda row: (abs(float(row["queue_auc"]) - median_queue), int(row["seed"])),
    )
    return int(selected["seed"]), int(selected["workstation_wip_level"])


def timeline_plot(trials: list[dict[str, Any]], paired: list[dict[str, Any]], path: Path) -> tuple[int, int]:
    seed, wip = representative_seed(trials)
    by_condition = {
        row["condition"]: row
        for row in trials
        if row["seed"] == seed and row["workstation_wip_level"] == wip
    }
    paired_row = next(row for row in paired if row["seed"] == seed and row["workstation_wip_level"] == wip)
    canvas = Canvas()
    canvas.text(45, 28, "REPRESENTATIVE TIMELINE", BLACK, 4)
    canvas.text(45, 62, f"SEED {seed}, WIP {wip}, SELECTED BY MEDIAN BASELINE QUEUE AUC", BLACK, 2)
    # Older frozen trial CSVs did not include every coordination event field.
    # Plot all events available in the result artifact and keep missing events
    # explicit in the companion report instead of rerunning simulation.
    events = [
        ("DEGRADATION", by_condition["degradation_reactive_baseline"].get("degradation_start_step"), ORANGE),
        ("RACS DETECTION", by_condition["degradation_racs"].get("risk_detection_step"), BLUE),
        ("RACS DRAIN", by_condition["degradation_racs"].get("intervention_step"), BLUE),
        ("GRACEFUL DRAIN", by_condition["degradation_racs"].get("graceful_drain_completion_step"), GREEN),
        ("HARD FAILURE", by_condition["degradation_reactive_baseline"].get("hard_failure_step") or by_condition["degradation_reactive_baseline"].get("counterfactual_failure_step"), RED),
        ("BASELINE PROP", paired_row.get("baseline_degradation_induced_cascade_start_step"), PURPLE),
        ("RACS PROP", paired_row.get("racs_degradation_induced_cascade_start_step"), PURPLE),
    ]
    canvas.line(100, 360, 1000, 360, BLACK)
    for step in [0, 15, 30, 45, 60]:
        x = scale(step, 0, 60, 100, 1000)
        canvas.line(x, 345, x, 375, BLACK)
        canvas.text(x - 12, 390, str(step), BLACK, 2)
    y_offsets = [190, 235, 280, 325, 430, 475, 520]
    for (label, step, color), y in zip(events, y_offsets):
        if step is None:
            continue
        x = scale(int(step), 0, 60, 100, 1000)
        canvas.line(x, 360, x, y, color)
        canvas.circle(x, y, 7, color)
        canvas.text(max(40, x - 65), y - 30, f"{label} {step}", color, 2)
    canvas.text(440, 650, "SIMULATION STEP", BLACK, 2)
    canvas.save_png(path)
    return seed, wip


def generate_plots(result_dir: Path, output_dir: Path) -> dict[str, Any]:
    paired = load_rows(result_dir / "paired_results.csv")
    trials = load_rows(result_dir / "trials.csv")
    wip_summary = load_rows(result_dir / "wip_summary.csv")
    output_dir.mkdir(parents=True, exist_ok=True)
    distribution_plot(paired, "queue_auc_delta", "QUEUE AUC DELTA DISTRIBUTION", output_dir / "queue_auc_delta_distribution.png")
    distribution_plot(paired, "latency_delta", "LATENCY DELTA DISTRIBUTION", output_dir / "latency_delta_distribution.png")
    bar_plot(wip_summary, output_dir / "intervention_before_propagation_by_wip.png")
    cascade_timing_plot(paired, output_dir / "baseline_cascade_timing_by_wip.png")
    distribution_plot(paired, "net_missed_workstation_processing_deficit_delta", "DOWNSTREAM NET DEFICIT DELTA", output_dir / "downstream_net_deficit_delta_distribution.png")
    seed, wip = timeline_plot(trials, paired, output_dir / "representative_timeline.png")
    return {
        "result_dir": str(result_dir),
        "output_dir": str(output_dir),
        "representative_seed": seed,
        "representative_wip": wip,
        "representative_seed_rule": "seed closest to median baseline queue AUC at WIP 2, ties by lowest seed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate RACS V1 plots from existing result CSV files.")
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    info = generate_plots(args.result_dir, args.output_dir)
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
