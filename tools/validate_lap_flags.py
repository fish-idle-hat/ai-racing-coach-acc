#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from reference_compare import completed_lap_segments, lap_summary
from realtime_coach import RUNS_DIR, lap_validity_state


def resolve_run(run):
    path = Path(run)
    if path.is_dir():
        return path
    candidate = RUNS_DIR / run
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Run not found: {run}")


def load_rows(run_dir):
    csv_path = run_dir / "telemetry.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing telemetry CSV: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def value_set(rows, key):
    values = sorted({str(row.get(key, "")) for row in rows if str(row.get(key, "")) != ""})
    return values


def main():
    parser = argparse.ArgumentParser(description="Inspect official ACC lap-validity fields captured in a telemetry run.")
    parser.add_argument("run", help="Run folder or run name")
    args = parser.parse_args()

    run_dir = resolve_run(args.run)
    rows = load_rows(run_dir)
    segments = completed_lap_segments(rows)
    lap_summaries = [lap_summary(segment, rows) for segment in segments]
    row_states = [lap_validity_state(row) for row in rows]
    known_states = [state for state in row_states if state != "unknown"]

    report = {
        "run": str(run_dir),
        "rows": len(rows),
        "field_values": {
            "is_valid_lap": value_set(rows, "is_valid_lap"),
            "is_valid_lap_candidate_wide": value_set(rows, "is_valid_lap_candidate_wide"),
            "is_valid_lap_candidate_ansi": value_set(rows, "is_valid_lap_candidate_ansi"),
            "current_lap_invalid": value_set(rows, "current_lap_invalid"),
            "current_lap_valid": value_set(rows, "current_lap_valid"),
        },
        "validity_states_seen": sorted(set(known_states)),
        "has_direct_validity": bool(known_states),
        "laps": [
            {
                "lap_number": lap["lap_number"],
                "completed": lap["completed_by_position_wrap"],
                "official_lap_validity": lap.get("official_lap_validity"),
                "valid_for_reference": lap["valid_for_reference"],
                "lap_time_display": lap["lap_time_display"],
            }
            for lap in lap_summaries
        ],
    }

    output_path = run_dir / "lap_flag_validation.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Validation report: {output_path}")
    print(f"Rows: {report['rows']}")
    print(f"Direct validity captured: {report['has_direct_validity']}")
    print(f"Validity states seen: {', '.join(report['validity_states_seen']) if report['validity_states_seen'] else 'none'}")
    print(f"Field values: {json.dumps(report['field_values'], indent=2)}")
    for lap in report["laps"]:
        complete = "complete" if lap["completed"] else "incomplete"
        print(
            f"Lap {lap['lap_number']} ({complete}): official={lap['official_lap_validity']} "
            f"reference_valid={lap['valid_for_reference']} time={lap['lap_time_display']}"
        )


if __name__ == "__main__":
    main()
