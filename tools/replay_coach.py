#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from realtime_coach import (
    RUNS_DIR,
    RealtimeCoach,
    load_pro_video_reference,
    load_spa_map,
    load_spa_world_gates,
    load_spa_world_path,
)
from reference_compare import REFERENCES_DIR, compare_run, slug
from coach_decision import write_decision_artifacts


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


def first_non_empty(rows, key, default=""):
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def replay_run(run_dir, use_world_gates=True, pro_video_reference_arg=None):
    rows = load_rows(run_dir)
    world_path = load_spa_world_path() if use_world_gates else None
    world_gates = [] if not use_world_gates or world_path else load_spa_world_gates()
    pro_video_reference = None
    if pro_video_reference_arg:
        pro_video_reference = load_pro_video_reference(
            None if pro_video_reference_arg == "latest" else pro_video_reference_arg
        )
    coach = RealtimeCoach(
        load_spa_map(),
        world_gates=world_gates,
        world_path=world_path,
        min_message_gap=0,
        pro_video_reference=pro_video_reference,
    )

    events = []
    for index, row in enumerate(rows):
        for message in coach.update(row):
            events.append(
                {
                    "row_index": index,
                    "packet_id": row.get("packet_id"),
                    "lap_count": row.get("lap_count"),
                    "lap_time_ms": row.get("lap_time_ms"),
                    "speed_kmh": row.get("speed_kmh"),
                    "normalized_car_position": row.get("normalized_car_position"),
                    "message": message,
                }
            )
    final_row = rows[-1] if rows else {}
    for message in coach.final_session_messages():
        events.append(
            {
                "row_index": len(rows) - 1 if rows else None,
                "packet_id": final_row.get("packet_id"),
                "lap_count": final_row.get("lap_count"),
                "lap_time_ms": final_row.get("lap_time_ms"),
                "speed_kmh": final_row.get("speed_kmh"),
                "normalized_car_position": final_row.get("normalized_car_position"),
                "message": message,
                "final_review": True,
            }
        )
    return rows, events, coach.decision_history


def write_reports(run_dir, rows, events, decisions=None, include_reference=True):
    json_path = run_dir / "coach_replay_events.json"
    md_path = run_dir / "coach_replay.md"
    json_path.write_text(json.dumps(events, indent=2), encoding="utf-8")

    lines = [
        "# ACC Coach Replay",
        "",
        f"Run: `{run_dir}`",
        f"Rows: {len(rows)}",
        f"Messages: {len(events)}",
        "",
        "## Messages",
        "",
    ]

    if not events:
        lines.append("No coach messages were generated from this run.")
    else:
        lines.append("| # | Lap | Lap Time | Speed | Message |")
        lines.append("|---:|---:|---:|---:|---|")
        for index, event in enumerate(events, start=1):
            lap = event.get("lap_count") or ""
            lap_time = event.get("lap_time_ms") or ""
            speed = event.get("speed_kmh") or ""
            message = str(event["message"]).replace("|", "\\|")
            lines.append(f"| {index} | {lap} | {lap_time} | {speed} | {message} |")

    if include_reference:
        reference_path = REFERENCES_DIR / f"{slug(first_non_empty(rows, 'track', 'Spa'))}_{slug(first_non_empty(rows, 'car_model'))}_personal_best.json"
        lines.extend(["", "## Reference Comparison", ""])
        if reference_path.exists():
            try:
                _, comparison_md, comparison = compare_run(reference_path, run_dir)
                lines.append(comparison["recommendation"]["text"])
                lines.append("")
                lines.append(f"Full report: `{comparison_md}`")
            except Exception as exc:
                lines.append(f"Reference comparison failed: {exc}")
        else:
            lines.append(f"No matching reference found at `{reference_path}`.")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if decisions:
        write_decision_artifacts(run_dir, decisions)
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="Replay a saved ACC telemetry run through the current real-time coach logic.")
    parser.add_argument("run", help="Run folder or run name, e.g. runs/acc-m3-fix-test-1 or acc-m3-fix-test-1")
    parser.add_argument(
        "--disable-world-gates",
        action="store_true",
        help="Replay with normalized lap-position gates instead of calibrated world-position gates.",
    )
    parser.add_argument(
        "--no-reference",
        action="store_true",
        help="Do not regenerate the post-run reference comparison section.",
    )
    parser.add_argument(
        "--pro-video-reference",
        default=None,
        help="Replay with a pro-video reference. Use 'latest' or a pro_video_reference.json path.",
    )
    args = parser.parse_args()

    run_dir = resolve_run(args.run)
    rows, events, decisions = replay_run(
        run_dir,
        use_world_gates=not args.disable_world_gates,
        pro_video_reference_arg=args.pro_video_reference,
    )
    json_path, md_path = write_reports(run_dir, rows, events, decisions=decisions, include_reference=not args.no_reference)

    print(f"Replay JSON: {json_path}")
    print(f"Replay report: {md_path}")
    print(f"Messages: {len(events)}")
    for event in events:
        print(event["message"])


if __name__ == "__main__":
    main()
