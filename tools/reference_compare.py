#!/usr/bin/env python3
import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
DATA_DIR = ROOT / "data"
REFERENCES_DIR = DATA_DIR / "references"
TRACK_MAPS_DIR = DATA_DIR / "track_maps"
INVALID_TIME_MS = 2_000_000_000
ZONE_LABELS = {
    "La Source": "Turn 1 La Source",
    "Eau Rouge/Raidillon/Kemmel": "Turns 2, 3, 4 Eau Rouge/Raidillon/Kemmel",
    "Les Combes/Malmedy": "Turns 5, 6, 7 Les Combes/Malmedy",
    "Bruxelles": "Turns 8, 9 Bruxelles",
    "No Name/Pouhon Entry": "Turns 10, 11 No Name/Pouhon entry",
    "Pouhon/Fagnes": "Turns 12, 13 Pouhon/Fagnes",
    "Campus/Stavelot": "Turns 14, 15 Campus/Stavelot",
    "Blanchimont": "Turns 16, 17 Blanchimont",
    "Bus Stop": "Turns 18, 19 Bus Stop chicane",
}


def zone_label(zone_name):
    return ZONE_LABELS.get(zone_name, zone_name or "Unknown zone")


def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    value = to_float(value)
    return int(value) if value is not None else None


def max_abs_fields(row, keys):
    values = [abs(to_float(row.get(key)) or 0.0) for key in keys]
    return max(values) if values else 0.0


def row_damage_total(row):
    total = to_float(row.get("car_damage_total"))
    if total is not None:
        return total
    parts = [
        to_float(row.get("car_damage_front")),
        to_float(row.get("car_damage_rear")),
        to_float(row.get("car_damage_left")),
        to_float(row.get("car_damage_right")),
        to_float(row.get("car_damage_center")),
    ]
    parts = [value for value in parts if value is not None]
    return sum(parts) if parts else None


def valid_ms(value):
    value = to_int(value)
    if value is None or value < 0 or value >= INVALID_TIME_MS:
        return None
    return value


def fmt_ms(ms):
    if ms is None:
        return "not captured"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def slug(value):
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "unknown"))
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_") or "unknown"


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


def first_non_empty(rows, key):
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def load_track_map(track):
    path = TRACK_MAPS_DIR / f"{slug(track)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def norm_pos(row):
    value = to_float(row.get("normalized_car_position"))
    if value is None or not 0 <= value <= 1:
        return None
    return value


def row_time(row):
    return to_float(row.get("received_at"))


def lap_progress_distance(row, lap_length_m):
    progress = norm_pos(row)
    return progress * lap_length_m if progress is not None else None


def zone_for_distance(track_map, distance_m):
    if distance_m is None:
        return None
    lap_length = (track_map or {}).get("lap_length_m", 7004)
    wrapped_distance = distance_m % lap_length
    for zone in (track_map or {}).get("braking_zones", []):
        start = zone["start_m"]
        end = zone["end_m"]
        if start <= wrapped_distance < min(end, lap_length):
            return zone
        if end > lap_length and (wrapped_distance >= start or wrapped_distance < end - lap_length):
            return zone
    return None


def completed_lap_segments(rows):
    segments = []
    start = None
    previous = None
    sequential_lap_number = 0

    for index, row in enumerate(rows):
        current = norm_pos(row)
        if current is None:
            continue
        if previous is not None and previous > 0.85 and current < 0.15:
            if start is None:
                start = index
                previous = current
                continue
            sequential_lap_number += 1
            acc_lap_count = to_int(rows[start].get("lap_count"))
            segments.append(
                {
                    "lap_number": sequential_lap_number,
                    "acc_lap_count": acc_lap_count,
                    "sequential_lap_number": sequential_lap_number,
                    "row_start": start,
                    "row_end": index - 1,
                    "completed_by_position_wrap": True,
                }
            )
            start = index
        previous = current

    if start is not None and start < len(rows) - 1:
        sequential_lap_number += 1
        acc_lap_count = to_int(rows[start].get("lap_count"))
        segments.append(
            {
                "lap_number": sequential_lap_number,
                "acc_lap_count": acc_lap_count,
                "sequential_lap_number": sequential_lap_number,
                "row_start": start,
                "row_end": len(rows) - 1,
                "completed_by_position_wrap": False,
            }
        )
    return segments


def stats(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"min": None, "max": None, "mean": None}
    return {"min": min(values), "max": max(values), "mean": statistics.fmean(values)}


def segment_duration(rows):
    times = [row_time(row) for row in rows if row_time(row) is not None]
    return max(times) - min(times) if len(times) >= 2 else None


def segment_lap_time_ms(rows):
    lap_times = [valid_ms(row.get("lap_time_ms")) for row in rows if valid_ms(row.get("lap_time_ms")) is not None]
    return max(lap_times) if lap_times else None


def segment_progress_span(rows):
    positions = [norm_pos(row) for row in rows if norm_pos(row) is not None]
    if not positions:
        return None, None
    return min(positions), max(positions)


def row_lap_validity_state(row):
    for key, valid_when_one in (
        ("current_lap_invalid", False),
        ("current_lap_valid", True),
        ("is_valid_lap", True),
    ):
        value = to_int(row.get(key))
        if value not in (0, 1):
            continue
        if valid_when_one:
            return "valid" if value == 1 else "invalid"
        return "invalid" if value == 1 else "valid"
    return "unknown"


def official_lap_validity(lap_rows):
    states = [row_lap_validity_state(row) for row in lap_rows]
    states = [state for state in states if state != "unknown"]
    if not states:
        return "unknown"
    if "invalid" in states:
        return "invalid"
    return "valid"


def official_invalid_trigger(lap_rows, track_map):
    previous = None
    lap_length_m = (track_map or {}).get("lap_length_m", 7004)
    for row in lap_rows:
        state = row_lap_validity_state(row)
        if state not in {"valid", "invalid"}:
            continue
        if previous == "valid" and state == "invalid":
            distance_m = lap_progress_distance(row, lap_length_m)
            zone = zone_for_distance(track_map, distance_m)
            return {
                "row": row,
                "zone": zone["name"] if zone else "Unknown zone",
                "distance_m": distance_m,
                "lap_time_ms": valid_ms(row.get("lap_time_ms")),
                "speed_kmh": to_float(row.get("speed_kmh")),
            }
        previous = state
    return None


def valid_lap(segment, rows):
    lap_rows = rows[segment["row_start"] : segment["row_end"] + 1]
    duration = segment_duration(lap_rows)
    lap_time = segment_lap_time_ms(lap_rows)
    min_progress, max_progress = segment_progress_span(lap_rows)
    max_speed = max((to_float(row.get("speed_kmh")) or 0) for row in lap_rows)
    timer_matches_duration = (
        lap_time is None
        or duration is None
        or abs(duration - (lap_time / 1000)) <= 5
    )
    official_validity = official_lap_validity(lap_rows)
    return (
        segment.get("completed_by_position_wrap")
        and official_validity != "invalid"
        and duration is not None
        and 90 <= duration <= 220
        and (lap_time is None or 90_000 <= lap_time <= 220_000)
        and timer_matches_duration
        and min_progress is not None
        and min_progress < 0.20
        and max_progress is not None
        and max_progress > 0.80
        and max_speed >= 150
    )


def lap_summary(segment, rows):
    lap_rows = rows[segment["row_start"] : segment["row_end"] + 1]
    speeds = [to_float(row.get("speed_kmh")) for row in lap_rows]
    return {
        **segment,
        "valid_for_reference": valid_lap(segment, rows),
        "official_lap_validity": official_lap_validity(lap_rows),
        "duration_seconds": round(segment_duration(lap_rows), 3) if segment_duration(lap_rows) is not None else None,
        "lap_time_ms": segment_lap_time_ms(lap_rows),
        "lap_time_display": fmt_ms(segment_lap_time_ms(lap_rows)),
        "max_speed_kmh": round(max(v for v in speeds if v is not None), 3) if any(v is not None for v in speeds) else None,
    }


def track_zones(track_map):
    lap_length = (track_map or {}).get("lap_length_m", 7004)
    zones = []
    for zone in (track_map or {}).get("braking_zones", []):
        zones.append(
            {
                "name": zone["name"],
                "start_progress": zone["start_m"] / lap_length,
                "end_progress": min(zone["end_m"], lap_length) / lap_length,
                "start_m": zone["start_m"],
                "end_m": min(zone["end_m"], lap_length),
            }
        )
    return zones


def rows_for_zone(lap_rows, zone):
    output = []
    for row in lap_rows:
        progress = norm_pos(row)
        if progress is None:
            continue
        if zone["start_progress"] <= progress < zone["end_progress"]:
            output.append(row)
    return output


def first_after(rows, predicate):
    for row in rows:
        if predicate(row):
            return row
    return None


def last_matching(rows, predicate):
    found = None
    for row in rows:
        if predicate(row):
            found = row
    return found


def braking_events(rows):
    events = []
    start = None
    for index, row in enumerate(rows):
        active = (to_float(row.get("brake")) or 0) >= 0.10 and (to_float(row.get("speed_kmh")) or 0) >= 40
        if active and start is None:
            start = index
        if not active and start is not None:
            events.append((start, index - 1))
            start = None
    if start is not None:
        events.append((start, len(rows) - 1))
    return events


def primary_braking_event(rows):
    candidates = []
    for start, end in braking_events(rows):
        event_rows = rows[start : end + 1]
        if len(event_rows) < 3:
            continue
        speeds = [to_float(row.get("speed_kmh")) for row in event_rows if to_float(row.get("speed_kmh")) is not None]
        brakes = [to_float(row.get("brake")) for row in event_rows if to_float(row.get("brake")) is not None]
        if not speeds or not brakes:
            continue
        speed_drop = max(speeds) - min(speeds)
        max_brake = max(brakes)
        if speed_drop < 8 and max_brake < 0.25:
            continue
        candidates.append(
            {
                "start": start,
                "end": end,
                "speed_drop": speed_drop,
                "max_brake": max_brake,
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda event: (event["speed_drop"], event["max_brake"]))


def gear_shift_events(rows):
    events = []
    previous_gear = None
    for row in rows:
        gear = to_int(row.get("gear"))
        if gear is None:
            continue
        if previous_gear is not None and gear > previous_gear:
            events.append(
                {
                    "lap_progress": norm_pos(row),
                    "gear_from": previous_gear,
                    "gear_to": gear,
                    "rpm": to_int(row.get("rpm")),
                    "speed_kmh": to_float(row.get("speed_kmh")),
                }
            )
        previous_gear = gear
    return events


def sample_path(rows, max_points=160):
    points = []
    for row in rows:
        progress = norm_pos(row)
        x = to_float(row.get("car_world_x"))
        z = to_float(row.get("car_world_z"))
        if progress is None or x is None or z is None or abs(x) < 1e-20 and abs(z) < 1e-20:
            continue
        if (to_float(row.get("speed_kmh")) or 0) < 10:
            continue
        points.append({"progress": progress, "x": x, "z": z})

    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    return points[::step]


def nearest_path_point(path, progress):
    if not path or progress is None:
        return None
    return min(path, key=lambda point: abs(point["progress"] - progress))


def avg_path_delta(rows, reference_path):
    deltas = []
    for row in rows:
        progress = norm_pos(row)
        x = to_float(row.get("car_world_x"))
        z = to_float(row.get("car_world_z"))
        ref = nearest_path_point(reference_path, progress)
        if ref is None or x is None or z is None:
            continue
        deltas.append(math.hypot(x - ref["x"], z - ref["z"]))
    if not deltas:
        return {"mean_m": None, "max_m": None}
    return {"mean_m": round(statistics.fmean(deltas), 3), "max_m": round(max(deltas), 3)}


def zone_metrics(zone_rows, lap_length_m, reference_path=None):
    if not zone_rows:
        return {}

    speeds = [to_float(row.get("speed_kmh")) for row in zone_rows]
    throttle = [to_float(row.get("throttle")) for row in zone_rows]
    brake = [to_float(row.get("brake")) for row in zone_rows]
    steer = [abs(to_float(row.get("steer")) or 0) for row in zone_rows]

    brake_event = primary_braking_event(zone_rows)
    brake_start = zone_rows[brake_event["start"]] if brake_event else None
    brake_end = zone_rows[brake_event["end"]] if brake_event else None
    event_rows = zone_rows[brake_event["start"] : brake_event["end"] + 1] if brake_event else []
    heavy_brake_start = first_after(event_rows, lambda row: (to_float(row.get("brake")) or 0) >= 0.50 and (to_float(row.get("speed_kmh")) or 0) >= 40)
    post_brake_rows = zone_rows
    if brake_end is not None:
        end_index = zone_rows.index(brake_end)
        post_brake_rows = zone_rows[end_index:]
    throttle_pickup = first_after(post_brake_rows, lambda row: (to_float(row.get("throttle")) or 0) >= 0.35 and (to_float(row.get("brake")) or 0) < 0.10)
    full_throttle = first_after(post_brake_rows, lambda row: (to_float(row.get("throttle")) or 0) >= 0.95 and (to_float(row.get("brake")) or 0) < 0.05)
    min_speed_row = min(zone_rows, key=lambda row: to_float(row.get("speed_kmh")) or 10**9)

    sample_count = len(zone_rows)
    damage_values = [row_damage_total(row) for row in zone_rows]
    damage_values = [value for value in damage_values if value is not None]
    tyres_out_values = [to_int(row.get("number_of_tyres_out")) for row in zone_rows]
    tyres_out_values = [value for value in tyres_out_values if value is not None]
    angular_values = [
        max_abs_fields(row, ("local_angular_vel_x", "local_angular_vel_y", "local_angular_vel_z"))
        for row in zone_rows
    ]
    wheel_slip_values = [
        max_abs_fields(row, ("wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr"))
        for row in zone_rows
    ]
    high_steer_throttle = sum(
        1
        for row in zone_rows
        if abs(to_float(row.get("steer")) or 0) >= 0.32
        and (to_float(row.get("throttle")) or 0) >= 0.45
        and (to_float(row.get("speed_kmh")) or 0) >= 55
    )
    pedal_overlap = sum(
        1
        for row in zone_rows
        if (to_float(row.get("brake")) or 0) >= 0.15 and (to_float(row.get("throttle")) or 0) >= 0.20
    )

    def distance(row):
        return round(lap_progress_distance(row, lap_length_m), 3) if row is not None and lap_progress_distance(row, lap_length_m) is not None else None

    def lap_time(row):
        return valid_ms(row.get("lap_time_ms")) if row is not None else None

    return {
        "sample_count": sample_count,
        "entry_speed_kmh": round(to_float(zone_rows[0].get("speed_kmh")) or 0, 3),
        "exit_speed_kmh": round(to_float(zone_rows[-1].get("speed_kmh")) or 0, 3),
        "min_speed_kmh": round(min(v for v in speeds if v is not None), 3) if any(v is not None for v in speeds) else None,
        "min_speed_distance_m": distance(min_speed_row),
        "max_speed_kmh": round(max(v for v in speeds if v is not None), 3) if any(v is not None for v in speeds) else None,
        "max_brake": round(max(v for v in brake if v is not None), 3) if any(v is not None for v in brake) else None,
        "primary_brake_speed_drop_kmh": round(brake_event["speed_drop"], 3) if brake_event else None,
        "max_abs_steer": round(max(steer), 3) if steer else None,
        "avg_throttle": round(statistics.fmean(v for v in throttle if v is not None), 4) if any(v is not None for v in throttle) else None,
        "brake_start_distance_m": distance(brake_start),
        "heavy_brake_start_distance_m": distance(heavy_brake_start),
        "brake_end_distance_m": distance(brake_end),
        "brake_start_lap_time_ms": lap_time(brake_start),
        "brake_end_lap_time_ms": lap_time(brake_end),
        "trail_brake_duration_ms": (
            lap_time(brake_end) - lap_time(brake_start)
            if brake_start is not None and brake_end is not None and lap_time(brake_start) is not None and lap_time(brake_end) is not None
            else None
        ),
        "throttle_pickup_distance_m": distance(throttle_pickup),
        "full_throttle_distance_m": distance(full_throttle),
        "throttle_pickup_lap_time_ms": lap_time(throttle_pickup),
        "full_throttle_lap_time_ms": lap_time(full_throttle),
        "high_steer_throttle_share": round(high_steer_throttle / sample_count, 4),
        "pedal_overlap_share": round(pedal_overlap / sample_count, 4),
        "entry_damage_total": round(damage_values[0], 3) if damage_values else None,
        "max_damage_total": round(max(damage_values), 3) if damage_values else None,
        "max_damage_delta": round(max(0.0, max(damage_values) - damage_values[0]), 3) if damage_values else 0.0,
        "max_number_of_tyres_out": max(tyres_out_values) if tyres_out_values else 0,
        "max_abs_local_angular_vel": round(max(angular_values), 3) if angular_values else 0.0,
        "max_wheel_slip": round(max(wheel_slip_values), 3) if wheel_slip_values else 0.0,
        "upshift_count": len(gear_shift_events(zone_rows)),
        "upshift_events": gear_shift_events(zone_rows)[:8],
        "racing_line_delta": avg_path_delta(zone_rows, reference_path) if reference_path else {"mean_m": None, "max_m": None},
    }


def build_lap_profile(run_dir, rows, lap, track_map, reference_path=None):
    lap_rows = rows[lap["row_start"] : lap["row_end"] + 1]
    lap_length_m = (track_map or {}).get("lap_length_m", 7004)
    return {
        "run": str(run_dir),
        "lap_number": lap["lap_number"],
        "row_start": lap["row_start"],
        "row_end": lap["row_end"],
        "lap_time_ms": lap["lap_time_ms"],
        "lap_time_display": fmt_ms(lap["lap_time_ms"]),
        "duration_seconds": lap["duration_seconds"],
        "path_sample": sample_path(lap_rows),
        "zones": {
            zone["name"]: zone_metrics(rows_for_zone(lap_rows, zone), lap_length_m, reference_path=reference_path)
            for zone in track_zones(track_map)
        },
    }


def load_lap_profiles(run_dirs):
    profiles = []
    for run_dir in run_dirs:
        rows = load_rows(run_dir)
        track = first_non_empty(rows, "track") or "Spa"
        track_map = load_track_map(track)
        if track_map is None:
            continue
        laps = [lap_summary(segment, rows) for segment in completed_lap_segments(rows)]
        for lap in laps:
            if lap["valid_for_reference"]:
                profiles.append((run_dir, rows, track, first_non_empty(rows, "car_model"), lap, track_map))
    return profiles


def build_reference(run_dirs, output=None):
    profiles = load_lap_profiles(run_dirs)
    if not profiles:
        raise SystemExit("No valid completed laps found. Capture at least two clean laps with normalized position data.")

    best_item = min(profiles, key=lambda item: item[4]["lap_time_ms"] or int((item[4]["duration_seconds"] or 999) * 1000))
    run_dir, rows, track, car, lap, track_map = best_item
    lap_profile = build_lap_profile(run_dir, rows, lap, track_map)
    reference = {
        "schema": "acc_ai_coach_reference_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "track": track,
        "car_model": car,
        "reference_type": "personal_best",
        "source_run": str(run_dir),
        "source_lap_number": lap["lap_number"],
        "lap_time_ms": lap["lap_time_ms"],
        "lap_time_display": fmt_ms(lap["lap_time_ms"]),
        "lap_profile": lap_profile,
    }

    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(output) if output else REFERENCES_DIR / f"{slug(track)}_{slug(car)}_personal_best.json"
    output_path.write_text(json.dumps(reference, indent=2), encoding="utf-8")
    return output_path, reference, len(profiles)


def metric_delta(metrics, reference_metrics, key):
    value = metrics.get(key)
    reference = reference_metrics.get(key)
    if value is None or reference is None:
        return None
    return round(value - reference, 3)


def zone_findings(zone_name, metrics, reference_metrics):
    findings = []

    brake_delta = metric_delta(metrics, reference_metrics, "brake_start_distance_m")
    if brake_delta is not None and abs(brake_delta) >= 25:
        if brake_delta < 0:
            findings.append(("braked_early", abs(brake_delta), f"braked {abs(brake_delta):.0f} m earlier than reference"))
        else:
            findings.append(("braked_late", brake_delta, f"braked {brake_delta:.0f} m later than reference"))

    trail_delta = metric_delta(metrics, reference_metrics, "trail_brake_duration_ms")
    if trail_delta is not None and abs(trail_delta) >= 250:
        if trail_delta < 0:
            findings.append(("less_trail_brake", abs(trail_delta) / 1000, f"trail-braked {abs(trail_delta) / 1000:.1f}s less than reference"))
        else:
            findings.append(("longer_brake", trail_delta / 1000, f"stayed on brake {trail_delta / 1000:.1f}s longer than reference"))

    throttle_delta = metric_delta(metrics, reference_metrics, "throttle_pickup_distance_m")
    if throttle_delta is not None and abs(throttle_delta) >= 25:
        if throttle_delta > 0:
            findings.append(("late_throttle", throttle_delta, f"picked up throttle {throttle_delta:.0f} m later than reference"))
        else:
            findings.append(("early_throttle", abs(throttle_delta), f"picked up throttle {abs(throttle_delta):.0f} m earlier than reference"))

    min_speed_delta = metric_delta(metrics, reference_metrics, "min_speed_kmh")
    if min_speed_delta is not None and abs(min_speed_delta) >= 5:
        if min_speed_delta < 0:
            findings.append(("low_min_speed", abs(min_speed_delta), f"minimum speed was {abs(min_speed_delta):.0f} km/h lower than reference"))
        else:
            findings.append(("higher_min_speed", min_speed_delta, f"minimum speed was {min_speed_delta:.0f} km/h higher than reference"))

    exit_delta = metric_delta(metrics, reference_metrics, "exit_speed_kmh")
    if exit_delta is not None and abs(exit_delta) >= 5:
        if exit_delta < 0:
            findings.append(("low_exit_speed", abs(exit_delta), f"exit speed was {abs(exit_delta):.0f} km/h lower than reference"))
        else:
            findings.append(("higher_exit_speed", exit_delta, f"exit speed was {exit_delta:.0f} km/h higher than reference"))

    line_mean = (metrics.get("racing_line_delta") or {}).get("mean_m")
    if line_mean is not None and line_mean >= 4:
        findings.append(("line_difference", line_mean, f"racing line averaged {line_mean:.1f} m away from reference"))

    upshift_delta = metric_delta(metrics, reference_metrics, "upshift_count")
    if upshift_delta is not None and upshift_delta != 0:
        direction = "more" if upshift_delta > 0 else "fewer"
        findings.append(("shift_count", abs(upshift_delta), f"made {abs(upshift_delta):.0f} {direction} upshifts than reference"))

    if metrics.get("high_steer_throttle_share") is not None and metrics["high_steer_throttle_share"] >= 0.08:
        findings.append(("throttle_with_steering", metrics["high_steer_throttle_share"], "used throttle while steering load was high"))

    if metrics.get("pedal_overlap_share") is not None and metrics["pedal_overlap_share"] >= 0.04:
        findings.append(("pedal_overlap", metrics["pedal_overlap_share"], "overlapped brake and throttle"))

    scored = []
    for reason, amount, text in findings:
        score = amount
        if reason in {"late_throttle", "low_exit_speed", "low_min_speed", "braked_early"}:
            score *= 1.3
        scored.append({"zone": zone_name, "reason": reason, "score": round(score, 3), "text": text})
    return sorted(scored, key=lambda item: item["score"], reverse=True)


def incident_evidence(metrics):
    evidence = []
    damage_delta = metrics.get("max_damage_delta") or 0.0
    tyres_out = metrics.get("max_number_of_tyres_out") or 0
    angular_vel = metrics.get("max_abs_local_angular_vel") or 0.0
    wheel_slip = metrics.get("max_wheel_slip") or 0.0
    if damage_delta >= 0.5:
        evidence.append(f"damage increased by {damage_delta:.1f}")
    if tyres_out >= 3:
        evidence.append(f"{tyres_out} tyres were reported out")
    if angular_vel >= 2.0:
        evidence.append(f"rotation rate spiked to {angular_vel:.1f}")
    if wheel_slip >= 8.0:
        evidence.append(f"wheel slip peaked at {wheel_slip:.1f}")
    return evidence


def classify_incident(metrics, fallback):
    damage_delta = metrics.get("max_damage_delta") or 0.0
    tyres_out = metrics.get("max_number_of_tyres_out") or 0
    angular_vel = metrics.get("max_abs_local_angular_vel") or 0.0
    wheel_slip = metrics.get("max_wheel_slip") or 0.0
    max_steer = metrics.get("max_abs_steer") or 0.0
    evidence = incident_evidence(metrics)
    if damage_delta >= 0.5:
        cause = "likely wall contact or car-to-barrier contact"
    elif tyres_out >= 3:
        cause = "likely off-track or track-limits event"
    elif angular_vel >= 2.0:
        cause = "likely spin or big recovery"
    elif wheel_slip >= 8.0 and max_steer >= 0.55:
        cause = "likely slide from excessive slip while steering"
    else:
        cause = fallback
    if evidence:
        return f"{cause}; evidence: {', '.join(evidence)}"
    if "telemetry cannot confirm" not in cause:
        return f"{cause}; no direct damage, tyres-out, or spin evidence was captured"
    return cause


def major_incident_findings(laps, rows, track_map):
    incidents = []
    high_speed_minimums = {
        "Eau Rouge/Raidillon/Kemmel": 130,
        "No Name/Pouhon Entry": 90,
        "Pouhon/Fagnes": 90,
        "Campus/Stavelot": 80,
        "Blanchimont": 145,
    }
    braking_zone_minimums = {
        "Les Combes/Malmedy": 80,
        "Bruxelles": 65,
        "Bus Stop": 45,
    }

    for lap in laps:
        lap_rows = rows[lap["row_start"] : lap["row_end"] + 1]
        trigger = official_invalid_trigger(lap_rows, track_map)
        if trigger is not None:
            zone_name = trigger["zone"]
            time_text = f" around {fmt_ms(trigger['lap_time_ms'])}" if trigger.get("lap_time_ms") is not None else ""
            speed_text = f" at {trigger['speed_kmh']:.0f} km/h" if trigger.get("speed_kmh") is not None else ""
            summary = (
                f"ACC invalidated the lap here{time_text}{speed_text}. "
                "Treat this as the main lap-losing event before smaller driving details."
            )
            incidents.append(
                {
                    "lap_number": lap["lap_number"],
                    "completed": lap.get("completed_by_position_wrap", False),
                    "zone": zone_name,
                    "zone_label": zone_label(zone_name),
                    "reason": "official_invalid_trigger",
                    "score": 1200.0,
                    "summary": summary,
                    "text": f"{zone_label(zone_name)}: {summary}",
                }
            )
        profile = build_lap_profile(Path(""), rows, lap, track_map)
        for zone_name, metrics in profile["zones"].items():
            if not metrics or metrics.get("sample_count", 0) < 20:
                continue
            min_speed = metrics.get("min_speed_kmh")
            if min_speed is None:
                continue

            threshold = high_speed_minimums.get(zone_name)
            if threshold is not None and min_speed < threshold:
                max_speed = metrics.get("max_speed_kmh") or 0
                if min_speed < 15 and max_speed >= threshold:
                    max_steer = metrics.get("max_abs_steer") or 0
                    steer_throttle_share = metrics.get("high_steer_throttle_share") or 0
                    if steer_throttle_share >= 0.08:
                        fallback = "likely ran wide or went off track while adding throttle with steering still loaded"
                    elif max_steer >= 0.70:
                        fallback = "likely spin or off-track recovery"
                    else:
                        fallback = "major stop or recovery; telemetry cannot confirm whether it was off track, a spin, or wall contact"
                    summary = (
                        f"{classify_incident(metrics, fallback)}. "
                        f"Speed fell from high-speed running to {min_speed:.0f} km/h."
                    )
                else:
                    summary = f"major speed loss detected. Minimum speed dropped to {min_speed:.0f} km/h in a section that should stay fast."
                incidents.append(
                    {
                        "lap_number": lap["lap_number"],
                        "completed": lap.get("completed_by_position_wrap", False),
                        "zone": zone_name,
                        "zone_label": zone_label(zone_name),
                        "reason": "major_speed_loss",
                        "score": round(100 + threshold - min_speed, 3),
                        "summary": summary + " Treat that lap as invalid-style first; fix stability before fine reference deltas.",
                        "text": f"{zone_label(zone_name)}: {summary} Treat that lap as invalid-style first; fix stability before fine reference deltas.",
                    }
                )
                continue

            threshold = braking_zone_minimums.get(zone_name)
            max_steer = metrics.get("max_abs_steer") or 0
            if threshold is not None and min_speed < threshold and max_steer >= 0.55:
                cause = classify_incident(metrics, "major corner push or recovery")
                incidents.append(
                    {
                        "lap_number": lap["lap_number"],
                        "completed": lap.get("completed_by_position_wrap", False),
                        "zone": zone_name,
                        "zone_label": zone_label(zone_name),
                        "reason": "major_corner_push",
                        "score": round(80 + threshold - min_speed, 3),
                        "summary": (
                            f"{cause}. Minimum speed fell to {min_speed:.0f} km/h with high steering input. "
                            "That points to a major push or recovery, not a small throttle-detail issue."
                        ),
                        "text": (
                            f"{zone_label(zone_name)}: {cause}. Minimum speed fell to {min_speed:.0f} km/h with high steering input. "
                            "That points to a major push or recovery, not a small throttle-detail issue."
                        ),
                    }
                )
                continue

            max_speed = metrics.get("max_speed_kmh") or 0
            if min_speed < 8 and max_speed >= 80:
                if max_steer >= 0.70:
                    fallback = "likely spin or off-track recovery"
                else:
                    fallback = "major stop or recovery; telemetry cannot confirm wall contact versus off-track"
                summary = (
                    f"{classify_incident(metrics, fallback)}. "
                    f"Speed fell from {max_speed:.0f} km/h to {min_speed:.0f} km/h."
                )
                incidents.append(
                    {
                        "lap_number": lap["lap_number"],
                        "completed": lap.get("completed_by_position_wrap", False),
                        "zone": zone_name,
                        "zone_label": zone_label(zone_name),
                        "reason": "major_stop_recovery",
                        "score": round(90 + max_speed - min_speed, 3),
                        "summary": summary + " Treat that lap as invalid-style first.",
                        "text": f"{zone_label(zone_name)}: {summary} Treat that lap as invalid-style first.",
                    }
                )

    return sorted(incidents, key=lambda item: item["score"], reverse=True)


def compare_run(reference_path, run_dir):
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
    rows = load_rows(run_dir)
    track = first_non_empty(rows, "track") or reference.get("track") or "Spa"
    car = first_non_empty(rows, "car_model")
    track_map = load_track_map(track)
    if track_map is None:
        raise SystemExit(f"No track map for {track}")

    reference_profile = reference["lap_profile"]
    reference_path_sample = reference_profile.get("path_sample") or []
    laps = [lap_summary(segment, rows) for segment in completed_lap_segments(rows)]
    major_incidents = major_incident_findings(laps, rows, track_map)
    valid_laps = [lap for lap in laps if lap["valid_for_reference"]]
    comparisons = []

    for lap in valid_laps:
        profile = build_lap_profile(run_dir, rows, lap, track_map, reference_path=reference_path_sample)
        lap_delta_ms = (
            lap["lap_time_ms"] - reference.get("lap_time_ms")
            if lap.get("lap_time_ms") is not None and reference.get("lap_time_ms") is not None
            else None
        )
        zone_items = []
        for zone_name, metrics in profile["zones"].items():
            reference_metrics = reference_profile.get("zones", {}).get(zone_name, {})
            if not metrics or not reference_metrics:
                continue
            zone_items.append(
                {
                    "zone": zone_name,
                    "zone_label": zone_label(zone_name),
                    "metrics": metrics,
                    "reference_metrics": reference_metrics,
                    "deltas": {
                        key: metric_delta(metrics, reference_metrics, key)
                        for key in (
                            "brake_start_distance_m",
                            "trail_brake_duration_ms",
                            "throttle_pickup_distance_m",
                            "full_throttle_distance_m",
                            "min_speed_kmh",
                            "exit_speed_kmh",
                            "upshift_count",
                        )
                    },
                    "findings": zone_findings(zone_name, metrics, reference_metrics),
                }
            )
        ranked = sorted(
            [finding for item in zone_items for finding in item["findings"]],
            key=lambda item: item["score"],
            reverse=True,
        )
        comparisons.append(
            {
                "lap_number": lap["lap_number"],
                "lap_time_ms": lap["lap_time_ms"],
                "lap_time_display": lap["lap_time_display"],
                "lap_delta_ms": lap_delta_ms,
                "zones": zone_items,
                "top_findings": ranked[:8],
            }
        )

    result = {
        "schema": "acc_ai_coach_reference_comparison_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run": str(run_dir),
        "track": track,
        "car_model": car,
        "reference": {
            "path": str(reference_path),
            "source_run": reference.get("source_run"),
            "source_lap_number": reference.get("source_lap_number"),
            "lap_time_ms": reference.get("lap_time_ms"),
            "lap_time_display": reference.get("lap_time_display"),
        },
        "laps_detected": laps,
        "valid_laps_detected": len(valid_laps),
        "major_incidents": major_incidents,
        "comparisons": comparisons,
        "recommendation": make_recommendation(comparisons, major_incidents),
    }

    json_path = run_dir / "reference_comparison.json"
    md_path = run_dir / "reference_comparison.md"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(result), encoding="utf-8")
    return json_path, md_path, result


def make_recommendation(comparisons, major_incidents=None):
    major_incidents = major_incidents or []
    if major_incidents:
        top = major_incidents[0]
        return {
            "available": True,
            "focus_zone": top["zone"],
            "score": top["score"],
            "top_reasons": major_incidents[:3],
            "text": f"Focus {top['zone_label']}: {top.get('summary', top['text'])}",
        }

    all_findings = [finding for comparison in comparisons for finding in comparison["top_findings"]]
    if not all_findings:
        return {
            "available": False,
            "text": "No strong reference-comparison issue detected yet.",
        }
    by_zone = {}
    for finding in all_findings:
        item = by_zone.setdefault(finding["zone"], {"zone": finding["zone"], "score": 0, "findings": []})
        item["score"] += finding["score"]
        item["findings"].append(finding)
    best = max(by_zone.values(), key=lambda item: item["score"])
    top = sorted(best["findings"], key=lambda finding: finding["score"], reverse=True)[:3]
    reason_text = "; ".join(finding["text"] for finding in top)
    return {
        "available": True,
        "focus_zone": best["zone"],
        "score": round(best["score"], 3),
        "top_reasons": top,
        "text": f"Focus {zone_label(best['zone'])}: {reason_text}.",
    }


def markdown_report(result):
    lines = [
        "# ACC Reference Comparison",
        "",
        f"Run: `{result['run']}`",
        f"Track: {result['track']}",
        f"Car: {result['car_model'] or 'not captured'}",
        f"Reference: {result['reference']['lap_time_display']} from `{result['reference']['source_run']}` lap {result['reference']['source_lap_number']}",
        f"Valid laps compared: {result['valid_laps_detected']}",
        "",
        "## Recommendation",
        "",
    ]
    rec = result["recommendation"]
    lines.append(rec["text"])
    if result.get("major_incidents"):
        lines.extend(["", "## Major Incidents", ""])
        for incident in result["major_incidents"][:6]:
            completion = "" if incident.get("completed") else " incomplete"
            lines.append(f"- Lap {incident['lap_number']}{completion}: {incident['text']}")
    lines.extend(["", "## Lap Findings", ""])
    if not result["comparisons"]:
        lines.append("No valid completed laps were detected for reference comparison.")
    for comparison in result["comparisons"]:
        delta = comparison["lap_delta_ms"]
        delta_text = f"{delta:+} ms" if delta is not None else "delta not captured"
        lines.extend(
            [
                f"### Lap {comparison['lap_number']} - {comparison['lap_time_display']} ({delta_text})",
                "",
            ]
        )
        if not comparison["top_findings"]:
            lines.append("- No strong zone-level differences detected.")
        else:
            for finding in comparison["top_findings"]:
                lines.append(f"- {zone_label(finding['zone'])}: {finding['text']}")
        lines.append("")

    lines.extend(["## Zone Detail", ""])
    for comparison in result["comparisons"]:
        lines.append(f"### Lap {comparison['lap_number']}")
        for item in comparison["zones"]:
            deltas = item["deltas"]
            useful = {key: value for key, value in deltas.items() if value is not None}
            if not useful:
                continue
            lines.append(f"- {zone_label(item['zone'])}: " + ", ".join(f"{key}={value:+}" for key, value in useful.items()))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def update_plan_file():
    plan_path = ROOT / "MILESTONE_1_PLAN.md"
    text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    marker = "## Milestone 3A - Personal-Best Reference Comparison"
    if marker in text:
        return
    addition = """

## Milestone 3A - Personal-Best Reference Comparison

Status: implemented as offline tool; ready for live-coach integration after validation

Build the first reference comparison engine the AI coach will reason from.

Current output includes:

- `tools/reference_compare.py`
- personal-best reference JSON under `data/references/`
- automatic best valid lap selection from one or more captured runs
- corner/zone comparison against the reference lap
- JSON report: `runs/<run-name>/reference_comparison.json`
- Markdown report: `runs/<run-name>/reference_comparison.md`

Current comparison signals:

- brake point delta
- heavy-brake point delta
- trail-brake duration delta
- throttle pickup delta
- full-throttle point delta
- minimum speed delta
- exit speed delta
- upshift count difference
- racing-line distance from the reference path when world position exists
- throttle while steering load is high
- brake/throttle overlap

Known limitation:

- comparison is against the user's own best lap first, not pro telemetry yet
- zones are still broad Spa sections, not every individual turn apex
- live voice coaching has not yet been wired to the reference-comparison report
"""
    plan_path.write_text(text.rstrip() + addition, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Milestone 3 reference comparison engine")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a personal-best reference from one or more runs")
    build.add_argument("runs", nargs="+", help="Run folders or run names")
    build.add_argument("--output", default=None, help="Optional output reference JSON path")

    compare = sub.add_parser("compare", help="Compare a run against a reference JSON")
    compare.add_argument("run", help="Run folder or run name to compare")
    compare.add_argument("--reference", default=None, help="Reference JSON path. Defaults to matching track/car personal best when possible.")

    auto = sub.add_parser("auto", help="Build a reference and compare a run in one command")
    auto.add_argument("run", help="Run folder or run name to compare")
    auto.add_argument("--reference-runs", nargs="*", default=None, help="Runs to search for the personal best. Defaults to the compare run.")
    auto.add_argument("--output", default=None, help="Optional reference JSON path")

    args = parser.parse_args()

    if args.command == "build":
        run_dirs = [resolve_run(run) for run in args.runs]
        output_path, reference, profile_count = build_reference(run_dirs, args.output)
        update_plan_file()
        print(f"Reference saved: {output_path}")
        print(f"Best lap: {reference['lap_time_display']} from {reference['source_run']} lap {reference['source_lap_number']}")
        print(f"Valid candidate laps searched: {profile_count}")
        return

    if args.command == "compare":
        run_dir = resolve_run(args.run)
        rows = load_rows(run_dir)
        reference = args.reference
        if reference is None:
            reference = REFERENCES_DIR / f"{slug(first_non_empty(rows, 'track') or 'Spa')}_{slug(first_non_empty(rows, 'car_model'))}_personal_best.json"
        if not Path(reference).exists():
            raise SystemExit(f"Reference not found: {reference}. Run the build command first.")
        json_path, md_path, result = compare_run(reference, run_dir)
        update_plan_file()
        print(f"Comparison JSON: {json_path}")
        print(f"Comparison report: {md_path}")
        print(result["recommendation"]["text"])
        return

    if args.command == "auto":
        run_dir = resolve_run(args.run)
        reference_runs = [resolve_run(run) for run in args.reference_runs] if args.reference_runs else [run_dir]
        output_path, reference, profile_count = build_reference(reference_runs, args.output)
        json_path, md_path, result = compare_run(output_path, run_dir)
        update_plan_file()
        print(f"Reference saved: {output_path}")
        print(f"Best lap: {reference['lap_time_display']} from {reference['source_run']} lap {reference['source_lap_number']}")
        print(f"Valid candidate laps searched: {profile_count}")
        print(f"Comparison JSON: {json_path}")
        print(f"Comparison report: {md_path}")
        print(result["recommendation"]["text"])


if __name__ == "__main__":
    main()
