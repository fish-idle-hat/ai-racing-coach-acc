#!/usr/bin/env python3
import argparse
import re
from pathlib import Path

from realtime_coach import RUNS_DIR
from replay_coach import replay_run, resolve_run, write_reports


START_RE = re.compile(r"^Lap (\d+) started\.$")
COMPLETE_RE = re.compile(r"^Lap (\d+) complete:")
ZONE_COUNT_RE = re.compile(r"^(?:Lap \d+ complete|Final incomplete lap review): (\d+) Spa zones reviewed\.")


def latest_runs(limit):
    runs = [
        path
        for path in RUNS_DIR.iterdir()
        if path.is_dir() and (path / "telemetry.csv").exists()
    ]
    return sorted(runs, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def check_messages(events):
    problems = []
    last_started_lap = None
    seen_started = False

    for index, event in enumerate(events, start=1):
        message = event["message"]
        lower = message.lower()

        if "weather data is not available" in lower:
            problems.append(f"Message {index}: unavailable weather phrase leaked into coach output.")
        if "this is becoming a habit" in lower:
            problems.append(f"Message {index}: old raw-count habit phrase leaked into coach output.")

        zone_match = ZONE_COUNT_RE.match(message)
        if zone_match and int(zone_match.group(1)) > 10:
            problems.append(f"Message {index}: impossible Spa zone count: {message}")

        start_match = START_RE.match(message)
        if start_match:
            last_started_lap = int(start_match.group(1))
            seen_started = True
            continue

        complete_match = COMPLETE_RE.match(message)
        if complete_match:
            completed_lap = int(complete_match.group(1))
            if last_started_lap is None:
                problems.append(f"Message {index}: lap {completed_lap} completed before any lap start.")
            elif completed_lap != last_started_lap:
                problems.append(
                    f"Message {index}: lap label mismatch, started lap {last_started_lap} but completed lap {completed_lap}."
                )

        if not seen_started and not message.startswith("Hi, "):
            problems.append(f"Message {index}: non-intro message before first clean lap start: {message}")

    return problems


def main():
    parser = argparse.ArgumentParser(description="Replay saved ACC runs and flag obvious coach-output regressions.")
    parser.add_argument("runs", nargs="*", help="Run names or folders. Defaults to latest telemetry runs.")
    parser.add_argument("--limit", type=int, default=6, help="Number of latest runs to check when no runs are specified.")
    args = parser.parse_args()

    run_dirs = [resolve_run(run) for run in args.runs] if args.runs else latest_runs(args.limit)
    lines = ["# ACC Coach Regression Report", ""]
    total_problems = 0

    for run_dir in run_dirs:
        rows, events, decisions = replay_run(run_dir)
        write_reports(run_dir, rows, events, decisions=decisions, include_reference=False)
        problems = check_messages(events)
        total_problems += len(problems)

        lines.extend(
            [
                f"## {run_dir.name}",
                "",
                f"- Rows: {len(rows)}",
                f"- Messages: {len(events)}",
                f"- Replay report: `{run_dir / 'coach_replay.md'}`",
            ]
        )
        if problems:
            lines.append("- Status: needs review")
            for problem in problems:
                lines.append(f"- Problem: {problem}")
        else:
            lines.append("- Status: pass")
        lines.append("")

    report_path = RUNS_DIR / "coach_regression_report.md"
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Regression report: {report_path}")
    print(f"Runs checked: {len(run_dirs)}")
    print(f"Problems: {total_problems}")
    if total_problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
