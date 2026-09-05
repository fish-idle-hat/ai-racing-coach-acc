#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path

from realtime_coach import RUNS_DIR, max_abs_fields, row_damage_total, to_float, to_int


FIELDS = [
    "car_damage_front",
    "car_damage_rear",
    "car_damage_left",
    "car_damage_right",
    "car_damage_center",
    "car_damage_total",
    "number_of_tyres_out",
    "local_angular_vel_x",
    "local_angular_vel_y",
    "local_angular_vel_z",
    "wheel_slip_fl",
    "wheel_slip_fr",
    "wheel_slip_rl",
    "wheel_slip_rr",
]


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


def stats(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"captured": False, "min": None, "max": None, "delta": None}
    return {
        "captured": True,
        "min": min(values),
        "max": max(values),
        "delta": max(values) - min(values),
    }


def numeric_values(rows, key):
    return [to_float(row.get(key)) for row in rows if str(row.get(key, "")) != ""]


def main():
    parser = argparse.ArgumentParser(description="Inspect ACC incident evidence fields from a telemetry run.")
    parser.add_argument("run", help="Run folder or run name")
    args = parser.parse_args()

    run_dir = resolve_run(args.run)
    rows = load_rows(run_dir)
    damage_totals = [row_damage_total(row) for row in rows]
    tyres_out = [to_int(row.get("number_of_tyres_out")) for row in rows if str(row.get("number_of_tyres_out", "")) != ""]
    angular = [
        max_abs_fields(row, ("local_angular_vel_x", "local_angular_vel_y", "local_angular_vel_z"))
        for row in rows
        if any(str(row.get(key, "")) != "" for key in ("local_angular_vel_x", "local_angular_vel_y", "local_angular_vel_z"))
    ]
    wheel_slip = [
        max_abs_fields(row, ("wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr"))
        for row in rows
        if any(str(row.get(key, "")) != "" for key in ("wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr"))
    ]

    report = {
        "run": str(run_dir),
        "rows": len(rows),
        "field_presence": {field: any(str(row.get(field, "")) != "" for row in rows) for field in FIELDS},
        "ranges": {
            **{field: stats(numeric_values(rows, field)) for field in FIELDS},
            "derived_damage_total": stats(damage_totals),
            "derived_max_abs_local_angular_vel": stats(angular),
            "derived_max_wheel_slip": stats(wheel_slip),
            "derived_number_of_tyres_out": stats(tyres_out),
        },
    }

    output_path = run_dir / "incident_field_validation.json"
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Incident field report: {output_path}")
    print(f"Rows: {report['rows']}")
    print(f"Damage total: {report['ranges']['derived_damage_total']}")
    print(f"Tyres out: {report['ranges']['derived_number_of_tyres_out']}")
    print(f"Angular velocity: {report['ranges']['derived_max_abs_local_angular_vel']}")
    print(f"Wheel slip: {report['ranges']['derived_max_wheel_slip']}")
    missing = [field for field, present in report["field_presence"].items() if not present]
    if missing:
        print("Missing fields:", ", ".join(missing))


if __name__ == "__main__":
    main()
