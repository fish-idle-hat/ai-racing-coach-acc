#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from replay_coach import replay_run, resolve_run
from reference_compare import completed_lap_segments, lap_summary, load_track_map
from realtime_coach import RUNS_DIR, lap_validity_state, to_float


def load_rows(run_dir):
    csv_path = run_dir / "telemetry.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing telemetry CSV: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def has_any_message(events, text):
    return any(text in event.get("message", "") for event in events)


def has_prefix_message(events, prefix):
    return any(str(event.get("message", "")).startswith(prefix) for event in events)


def has_lap_started_message(events):
    for event in events:
        message = str(event.get("message", "")).strip()
        if not message.startswith("Lap ") or not message.endswith(" started."):
            continue
        lap_text = message.removeprefix("Lap ").removesuffix(" started.")
        if lap_text.isdigit():
            return True
    return False


def max_spa_zone_count(events):
    counts = []
    for event in events:
        message = str(event.get("message", ""))
        marker = " Spa zones reviewed."
        if marker not in message:
            continue
        prefix = message.split(marker, 1)[0]
        try:
            counts.append(int(prefix.rsplit(" ", 1)[-1]))
        except ValueError:
            pass
    return max(counts) if counts else 0


def column_has_values(rows, column):
    return any(str(row.get(column, "")) != "" for row in rows)


def numeric_delta(rows, column):
    values = [to_float(row.get(column)) for row in rows if str(row.get(column, "")) != ""]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values) - min(values)


def validate_run(run_dir):
    rows, events, decisions = replay_run(run_dir)
    track_map = load_track_map("Spa")
    segments = completed_lap_segments(rows)
    laps = [lap_summary(segment, rows) for segment in segments]
    states = [lap_validity_state(row) for row in rows]
    known_states = [state for state in states if state != "unknown"]
    accepted_laps = [lap for lap in laps if lap["valid_for_reference"]]
    rejected_laps = [lap for lap in laps if not lap["valid_for_reference"]]

    checks = {
        "m2_has_intro": has_prefix_message(events, "Hi, I am your private driving coach Rachel."),
        "m2_has_lap_start": has_lap_started_message(events),
        "m2_has_lap_summary": any("complete:" in event.get("message", "") for event in events),
        "m2_has_next_correction": has_any_message(events, "Next correction:"),
        "m2_has_decision_artifacts": (run_dir / "coach_decisions.json").exists()
        and (run_dir / "coach_ai_context.json").exists()
        and (run_dir / "coach_decision_report.md").exists(),
        "m3d_has_direct_validity": bool(known_states),
        "m3d_has_valid_state": "valid" in known_states,
        "m3d_has_invalid_state_or_rejected_lap": "invalid" in known_states or bool(rejected_laps),
        "m3d_excludes_invalid_from_reference": all(lap["official_lap_validity"] != "invalid" for lap in accepted_laps),
        "m3d_has_lap_segmentation": bool(laps),
        "m3d_has_incident_fields": any(
            column_has_values(rows, column)
            for column in (
                "car_damage_total",
                "number_of_tyres_out",
                "local_angular_vel_x",
                "wheel_slip_fl",
            )
        ),
        "m3d_has_world_position": (
            numeric_delta(rows, "car_world_x") is not None
            and numeric_delta(rows, "car_world_z") is not None
            and numeric_delta(rows, "car_world_x") > 1
            and numeric_delta(rows, "car_world_z") > 1
        ),
        "m3d_track_map_loaded": track_map is not None,
        "m4_no_old_raw_count_habit_phrase": not has_any_message(events, "This is becoming a habit."),
        "m4_zone_counts_are_plausible": max_spa_zone_count(events) <= 10,
        "m4_has_structured_decision_pattern": any(
            decision.get("pattern") in {
                "habit",
                "repeated_this_session",
                "seen_before",
                "recurring_watchlist",
                "one_time_watchlist",
            }
            for decision in decisions
        ),
        "m4_has_ai_context_for_future_llm": (run_dir / "coach_ai_context.json").exists(),
    }
    return {
        "run": str(run_dir),
        "rows": len(rows),
        "messages": len(events),
        "decisions": len(decisions),
        "laps": laps,
        "known_validity_states": sorted(set(known_states)),
        "accepted_reference_laps": len(accepted_laps),
        "rejected_reference_laps": len(rejected_laps),
        "checks": checks,
        "passed": all(checks.values()),
    }


def write_report(results):
    report_path = RUNS_DIR / "milestone_acceptance_report.md"
    lines = ["# Milestone Acceptance Report", ""]
    for result in results:
        status = "PASS" if result["passed"] else "NEEDS REVIEW"
        lines.extend(
            [
                f"## {Path(result['run']).name} - {status}",
                "",
                f"- Rows: {result['rows']}",
                f"- Coach messages: {result['messages']}",
                f"- Decisions: {result['decisions']}",
                f"- Validity states: {', '.join(result['known_validity_states']) or 'none'}",
                f"- Reference laps accepted/rejected: {result['accepted_reference_laps']}/{result['rejected_reference_laps']}",
                "",
                "### Checks",
                "",
            ]
        )
        for name, passed in result["checks"].items():
            mark = "PASS" if passed else "FAIL"
            lines.append(f"- {mark}: {name}")
        lines.extend(["", "### Laps", ""])
        for lap in result["laps"]:
            completion = "complete" if lap.get("completed_by_position_wrap") else "incomplete"
            lines.append(
                f"- Lap {lap['lap_number']} ({completion}): official={lap['official_lap_validity']}, "
                f"reference_valid={lap['valid_for_reference']}, time={lap['lap_time_display']}"
            )
        lines.append("")
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report_path


def main():
    parser = argparse.ArgumentParser(description="Validate Milestone 2 and 3D acceptance criteria against saved runs.")
    parser.add_argument("runs", nargs="+", help="Run names or folders")
    args = parser.parse_args()

    results = [validate_run(resolve_run(run)) for run in args.runs]
    report_path = write_report(results)
    print(f"Milestone acceptance report: {report_path}")
    for result in results:
        print(f"{Path(result['run']).name}: {'PASS' if result['passed'] else 'NEEDS REVIEW'}")
        for name, passed in result["checks"].items():
            if not passed:
                print(f"  FAIL {name}")
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
