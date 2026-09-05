#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from reference_compare import ROOT, completed_lap_segments, lap_summary, load_rows, slug


TURN_MAP = ROOT / "data" / "track_maps" / "spa_turns.json"
RUNS_DIR = ROOT / "runs"


def resolve_run(run):
    path = Path(run)
    if path.is_dir():
        return path
    candidate = RUNS_DIR / run
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Run not found: {run}")


def f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lap_distance(row, lap_length):
    pos = f(row.get("normalized_car_position"))
    return pos * lap_length if pos is not None and 0 <= pos <= 1 else None


def rows_for_turn(rows, turn, lap_length):
    output = []
    for row in rows:
        d = lap_distance(row, lap_length)
        if d is not None and turn["start_m"] <= d < min(turn["end_m"], lap_length + 200):
            output.append(row)
    return output


def turn_metrics(rows, turn, lap_length):
    if not rows:
        return None
    speeds = [f(row.get("speed_kmh")) for row in rows if f(row.get("speed_kmh")) is not None]
    brakes = [f(row.get("brake")) for row in rows if f(row.get("brake")) is not None]
    throttles = [f(row.get("throttle")) for row in rows if f(row.get("throttle")) is not None]
    steers = [abs(f(row.get("steer")) or 0) for row in rows]
    apex_row = min(rows, key=lambda row: abs((lap_distance(row, lap_length) or 0) - turn["apex_m"]))
    brake_rows = [row for row in rows if (f(row.get("brake")) or 0) >= 0.25]
    throttle_rows = [row for row in rows if (f(row.get("throttle")) or 0) >= 0.35 and (f(row.get("brake")) or 0) < 0.10]
    return {
        "turn": turn["turn"],
        "name": turn["name"],
        "sample_count": len(rows),
        "entry_speed_kmh": round(f(rows[0].get("speed_kmh")) or 0, 3),
        "apex_speed_kmh": round(f(apex_row.get("speed_kmh")) or 0, 3),
        "min_speed_kmh": round(min(speeds), 3) if speeds else None,
        "exit_speed_kmh": round(f(rows[-1].get("speed_kmh")) or 0, 3),
        "max_brake": round(max(brakes), 3) if brakes else None,
        "max_abs_steer": round(max(steers), 3) if steers else None,
        "avg_throttle": round(sum(throttles) / len(throttles), 4) if throttles else None,
        "first_brake_distance_m": round(lap_distance(brake_rows[0], lap_length), 3) if brake_rows else None,
        "first_throttle_distance_m": round(lap_distance(throttle_rows[0], lap_length), 3) if throttle_rows else None,
    }


def build_report(run_dir):
    rows = load_rows(run_dir)
    turn_map = json.loads(TURN_MAP.read_text(encoding="utf-8"))
    lap_length = turn_map["lap_length_m"]
    turns = turn_map["turns"]
    laps = [lap_summary(seg, rows) for seg in completed_lap_segments(rows)]
    output = {"run": str(run_dir), "laps": []}
    for lap in laps:
        lap_rows = rows[lap["row_start"] : lap["row_end"] + 1]
        output["laps"].append(
            {
                "lap_number": lap["lap_number"],
                "completed": lap["completed_by_position_wrap"],
                "official_lap_validity": lap.get("official_lap_validity"),
                "lap_time_display": lap["lap_time_display"],
                "turns": [m for turn in turns if (m := turn_metrics(rows_for_turn(lap_rows, turn, lap_length), turn, lap_length))],
            }
        )
    return output


def write_report(run_dir, report):
    json_path = run_dir / "turn_timing.json"
    md_path = run_dir / "turn_timing.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Turn-Level Timing", "", f"Run: `{run_dir}`", ""]
    for lap in report["laps"]:
        lines.extend([f"## Lap {lap['lap_number']} - {lap['lap_time_display']}", ""])
        lines.append("| Turn | Apex Speed | Min Speed | Exit Speed | Max Brake | Max Steer | First Brake m | First Throttle m |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for item in lap["turns"]:
            lines.append(
                f"| T{item['turn']} {item['name']} | {item['apex_speed_kmh']} | {item['min_speed_kmh']} | {item['exit_speed_kmh']} | {item['max_brake']} | {item['max_abs_steer']} | {item['first_brake_distance_m']} | {item['first_throttle_distance_m']} |"
            )
        lines.append("")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="Generate approximate turn/apex timing report from ACC telemetry.")
    parser.add_argument("run")
    args = parser.parse_args()
    run_dir = resolve_run(args.run)
    report = build_report(run_dir)
    json_path, md_path = write_report(run_dir, report)
    print(f"Turn timing JSON: {json_path}")
    print(f"Turn timing report: {md_path}")


if __name__ == "__main__":
    main()
