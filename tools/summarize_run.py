#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from mac_receiver import summarize


def main():
    parser = argparse.ArgumentParser(description="Regenerate summary.json for a recorded telemetry run")
    parser.add_argument("run_dir", help="Path to a run folder, for example runs/acc-test-3")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    csv_path = run_dir / "telemetry.csv"
    summary_path = run_dir / "summary.json"

    if not csv_path.exists():
        raise SystemExit(f"Missing telemetry CSV: {csv_path}")

    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    times = [float(row["received_at"]) for row in rows if row.get("received_at")]
    started_at = min(times) if times else 0.0
    ended_at = max(times) if times else started_at
    summary = summarize(rows, started_at, ended_at)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
