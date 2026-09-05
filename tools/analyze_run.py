#!/usr/bin/env python3
import argparse
import csv
import json
import re
import shutil
import statistics
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRACK_MAPS_DIR = ROOT / "data" / "track_maps"
SPA_REFERENCE_IMAGE = TRACK_MAPS_DIR / "spa_reference_map.png"
INVALID_TIME_MS = 2_000_000_000
TIME_DISPLAY_RE = re.compile(r"^\d{1,3}:\d{2}[:.]\d{3}$")

SPA_SCHEMATIC_POINTS = [
    (164, 350),
    (174, 278),
    (246, 204),
    (360, 176),
    (472, 148),
    (612, 82),
    (730, 128),
    (718, 228),
    (612, 270),
    (506, 292),
    (440, 350),
    (520, 438),
    (668, 484),
    (626, 572),
    (492, 594),
    (352, 548),
    (256, 462),
    (164, 350),
]


def to_float(value):
    try:
        if value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    value_float = to_float(value)
    if value_float is None:
        return None
    return int(value_float)


def to_valid_time_ms(value):
    parsed = to_int(value)
    if parsed is None or parsed < 0 or parsed >= INVALID_TIME_MS:
        return None
    return parsed


def to_completed_lap_time_ms(value):
    parsed = to_valid_time_ms(value)
    if parsed is None or parsed == 0:
        return None
    return parsed


def stats(values):
    values = [value for value in values if value is not None]
    if not values:
        return {"min": None, "max": None, "mean": None}
    return {
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
    }


def fmt_ms(ms):
    if ms is None:
        return "not captured"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def round_optional(value, digits=3):
    return round(value, digits) if value is not None else None


def normalize_track_key(track):
    if not track:
        return ""
    return "".join(char.lower() for char in str(track) if char.isalnum())


def load_track_map(track):
    track_key = normalize_track_key(track)
    if not track_key:
        return None

    path = TRACK_MAPS_DIR / f"{track_key}.json"
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def braking_zone_for_distance(track_map, lap_distance_m):
    if track_map is None or lap_distance_m is None:
        return None

    for zone in track_map.get("braking_zones", []):
        if zone["start_m"] <= lap_distance_m < zone["end_m"]:
            return zone

    return None


def polyline_lengths(points):
    lengths = []
    total = 0.0
    for index in range(len(points) - 1):
        ax, ay = points[index]
        bx, by = points[index + 1]
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        lengths.append(length)
        total += length
    return lengths, total


def point_at_fraction(points, fraction):
    fraction = max(0.0, min(1.0, fraction))
    lengths, total = polyline_lengths(points)
    target = total * fraction
    walked = 0.0

    for index, length in enumerate(lengths):
        if walked + length >= target:
            local = (target - walked) / length if length else 0.0
            ax, ay = points[index]
            bx, by = points[index + 1]
            return ax + (bx - ax) * local, ay + (by - ay) * local
        walked += length

    return points[-1]


def svg_escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def has_world_position(rows):
    world_x = [to_float(row.get("car_world_x")) for row in rows if to_float(row.get("car_world_x")) is not None]
    world_z = [to_float(row.get("car_world_z")) for row in rows if to_float(row.get("car_world_z")) is not None]
    if not world_x or not world_z:
        return False
    return max(world_x) - min(world_x) > 1 and max(world_z) - min(world_z) > 1


def first_non_empty(rows, key):
    for row in rows:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def is_valid_time_display(value):
    if value in (None, "", "-:--:---"):
        return False
    text = str(value)
    if text.startswith("35791:"):
        return False
    return bool(TIME_DISPLAY_RE.match(text))


def first_valid_time_display(rows, key):
    for row in rows:
        value = row.get(key)
        if is_valid_time_display(value):
            return value
    return ""


def load_rows(run_dir):
    csv_path = run_dir / "telemetry.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing telemetry CSV: {csv_path}")
    with csv_path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def lap_windows(rows):
    windows = []
    current_lap = None
    current_start = None

    for index, row in enumerate(rows):
        lap_count = to_int(row.get("lap_count"))
        if lap_count is None:
            continue
        if current_lap is None:
            current_lap = lap_count
            current_start = index
            continue
        if lap_count != current_lap:
            windows.append((current_lap, current_start, index - 1))
            current_lap = lap_count
            current_start = index

    if current_lap is not None and current_start is not None:
        windows.append((current_lap, current_start, len(rows) - 1))

    return windows


def timed_lap_windows(rows):
    windows = []
    current_start = None
    previous_time = None

    for index, row in enumerate(rows):
        lap_time = to_valid_time_ms(row.get("lap_time_ms"))
        if lap_time is None:
            continue

        if current_start is None:
            current_start = index
            previous_time = lap_time
            continue

        if previous_time is not None and previous_time > 30_000 and lap_time < 10_000:
            windows.append(
                {
                    "kind": "timed_lap",
                    "row_start": current_start,
                    "row_end": index - 1,
                    "completed_by_timer_reset": True,
                }
            )
            current_start = index

        previous_time = lap_time

    if current_start is not None:
        windows.append(
            {
                "kind": "timed_lap",
                "row_start": current_start,
                "row_end": len(rows) - 1,
                "completed_by_timer_reset": False,
            }
        )

    return windows


def row_time(row):
    return to_float(row.get("received_at"))


def contiguous_events(segment, predicate, min_duration_seconds=0.25):
    events = []
    start = None

    for index, row in enumerate(segment):
        active = predicate(row)
        if active and start is None:
            start = index
        if not active and start is not None:
            events.append((start, index - 1))
            start = None

    if start is not None:
        events.append((start, len(segment) - 1))

    filtered = []
    for start, end in events:
        start_time = row_time(segment[start])
        end_time = row_time(segment[end])
        duration = (end_time - start_time) if start_time is not None and end_time is not None else None
        if duration is None or duration >= min_duration_seconds:
            filtered.append((start, end))

    return filtered


def summarize_braking_event(segment, start, end):
    rows = segment[start : end + 1]
    speeds = [to_float(row.get("speed_kmh")) for row in rows]
    brakes = [to_float(row.get("brake")) for row in rows]
    start_time = row_time(segment[start])
    end_time = row_time(segment[end])
    duration = (end_time - start_time) if start_time is not None and end_time is not None else None
    start_speed = to_float(segment[start].get("speed_kmh"))
    end_speed = to_float(segment[end].get("speed_kmh"))
    start_lap_time = to_valid_time_ms(segment[start].get("lap_time_ms"))
    end_lap_time = to_valid_time_ms(segment[end].get("lap_time_ms"))
    sector = to_int(segment[start].get("current_sector_index"))
    start_distance = to_float(segment[start].get("distance_traveled"))
    end_distance = to_float(segment[end].get("distance_traveled"))
    min_speed = min(value for value in speeds if value is not None) if any(value is not None for value in speeds) else None
    max_brake = max(value for value in brakes if value is not None) if any(value is not None for value in brakes) else None

    throttle_pickup = None
    for offset, row in enumerate(segment[end + 1 :], start=end + 1):
        throttle = to_float(row.get("throttle"))
        if throttle is not None and throttle >= 0.2:
            pickup_time = row_time(row)
            if end_time is not None and pickup_time is not None:
                throttle_pickup = round(pickup_time - end_time, 3)
            break

    return {
        "row_start_offset": start,
        "row_end_offset": end,
        "sector_index": sector,
        "lap_time_start_ms": start_lap_time,
        "lap_time_end_ms": end_lap_time,
        "lap_time_start_display": fmt_ms(start_lap_time),
        "lap_time_end_display": fmt_ms(end_lap_time),
        "distance_start_m": start_distance,
        "distance_end_m": end_distance,
        "duration_seconds": round(duration, 3) if duration is not None else None,
        "start_speed_kmh": start_speed,
        "end_speed_kmh": end_speed,
        "min_speed_kmh": min_speed,
        "speed_drop_kmh": round(start_speed - min_speed, 3) if start_speed is not None and min_speed is not None else None,
        "max_brake": max_brake,
        "throttle_pickup_after_brake_seconds": throttle_pickup,
    }


def summarize_control_usage(segment):
    total = len(segment) or 1

    def share(predicate):
        return round(sum(1 for row in segment if predicate(row)) / total, 4)

    return {
        "full_throttle_share": share(lambda row: (to_float(row.get("throttle")) or 0) >= 0.95),
        "braking_share": share(lambda row: (to_float(row.get("brake")) or 0) >= 0.1),
        "heavy_braking_share": share(lambda row: (to_float(row.get("brake")) or 0) >= 0.8),
        "coasting_share": share(lambda row: (to_float(row.get("throttle")) or 0) < 0.05 and (to_float(row.get("brake")) or 0) < 0.05 and (to_float(row.get("speed_kmh")) or 0) > 40),
        "high_steering_share": share(lambda row: abs(to_float(row.get("steer")) or 0) >= 0.5),
        "throttle_while_high_steering_share": share(
            lambda row: abs(to_float(row.get("steer")) or 0) >= 0.5 and (to_float(row.get("throttle")) or 0) >= 0.2
        ),
    }


def sector_windows(segment):
    windows = []
    current_sector = None
    current_start = None

    for index, row in enumerate(segment):
        sector = to_int(row.get("current_sector_index"))
        if sector is None:
            continue
        if current_sector is None:
            current_sector = sector
            current_start = index
            continue
        if sector != current_sector:
            windows.append((current_sector, current_start, index - 1))
            current_sector = sector
            current_start = index

    if current_sector is not None and current_start is not None:
        windows.append((current_sector, current_start, len(segment) - 1))

    return windows


def summarize_sector(segment, sector, start, end):
    rows = segment[start : end + 1]
    speeds = [to_float(row.get("speed_kmh")) for row in rows]
    throttle = [to_float(row.get("throttle")) for row in rows]
    brake = [to_float(row.get("brake")) for row in rows]
    received = [to_float(row.get("received_at")) for row in rows if to_float(row.get("received_at")) is not None]
    lap_times = [to_valid_time_ms(row.get("lap_time_ms")) for row in rows if to_valid_time_ms(row.get("lap_time_ms")) is not None]
    distances = [to_float(row.get("distance_traveled")) for row in rows if to_float(row.get("distance_traveled")) is not None]
    lap_time_start = min(lap_times) if lap_times else None
    lap_time_end = max(lap_times) if lap_times else None

    return {
        "sector_index": sector,
        "row_start_offset": start,
        "row_end_offset": end,
        "duration_seconds": round(max(received) - min(received), 3) if len(received) >= 2 else None,
        "lap_time_start_ms": lap_time_start,
        "lap_time_end_ms": lap_time_end,
        "sector_time_ms": lap_time_end - lap_time_start if lap_time_start is not None and lap_time_end is not None else None,
        "distance_delta_m": round(max(distances) - min(distances), 3) if len(distances) >= 2 else None,
        "speed_kmh": stats(speeds),
        "throttle": stats(throttle),
        "brake": stats(brake),
        "control_usage": summarize_control_usage(rows),
    }


def summarize_lap(rows, lap_count, start, end):
    segment = rows[start : end + 1]
    speeds = [to_float(row.get("speed_kmh")) for row in segment]
    throttle = [to_float(row.get("throttle")) for row in segment]
    brake = [to_float(row.get("brake")) for row in segment]
    steer = [to_float(row.get("steer")) for row in segment]
    lap_times = [to_valid_time_ms(row.get("lap_time_ms")) for row in segment if to_valid_time_ms(row.get("lap_time_ms")) is not None]
    received = [to_float(row.get("received_at")) for row in segment if to_float(row.get("received_at")) is not None]
    current_lap_display = first_valid_time_display(reversed(segment), "current_lap_display")
    last_lap_display = first_valid_time_display(reversed(segment), "last_lap_display")
    best_lap_display = first_valid_time_display(reversed(segment), "best_lap_display")

    return {
        "lap_count_value": lap_count,
        "row_start": start,
        "row_end": end,
        "duration_seconds": round(max(received) - min(received), 3) if len(received) >= 2 else None,
        "lap_time_ms_final_seen": max(lap_times) if lap_times else None,
        "current_lap_display_seen": current_lap_display,
        "last_lap_display_seen": last_lap_display,
        "best_lap_display_seen": best_lap_display,
        "speed_kmh": stats(speeds),
        "throttle": stats(throttle),
        "brake": stats(brake),
        "steer": stats(steer),
    }


def summarize_timed_lap(rows, window, lap_index):
    start = window["row_start"]
    end = window["row_end"]
    segment = rows[start : end + 1]
    speeds = [to_float(row.get("speed_kmh")) for row in segment]
    throttle = [to_float(row.get("throttle")) for row in segment]
    brake = [to_float(row.get("brake")) for row in segment]
    steer = [to_float(row.get("steer")) for row in segment]
    lap_times = [to_valid_time_ms(row.get("lap_time_ms")) for row in segment if to_valid_time_ms(row.get("lap_time_ms")) is not None]
    received = [to_float(row.get("received_at")) for row in segment if to_float(row.get("received_at")) is not None]
    distances = [to_float(row.get("distance_traveled")) for row in segment if to_float(row.get("distance_traveled")) is not None]
    distance_origin = min(distances) if distances else None
    sector_indices = sorted({to_int(row.get("current_sector_index")) for row in segment if to_int(row.get("current_sector_index")) is not None})
    session_statuses = sorted({to_int(row.get("session_status")) for row in segment if to_int(row.get("session_status")) is not None})
    lap_time_final = max(lap_times) if lap_times else None
    observed_duration = round(max(received) - min(received), 3) if len(received) >= 2 else None
    observed_vs_timer_gap = (
        round(observed_duration - (lap_time_final / 1000), 3)
        if observed_duration is not None and lap_time_final is not None
        else None
    )

    brake_events = [
        summarize_braking_event(segment, start_event, end_event)
        for start_event, end_event in contiguous_events(segment, lambda row: (to_float(row.get("brake")) or 0) >= 0.1)
    ]
    for event_index, event in enumerate(brake_events, start=1):
        event["event_index"] = event_index
        event["event_label"] = f"B{event_index}"
        if distance_origin is not None and event["distance_start_m"] is not None:
            event["lap_distance_start_m"] = round(event["distance_start_m"] - distance_origin, 3)
        else:
            event["lap_distance_start_m"] = None

    top_brake_events = sorted(
        brake_events,
        key=lambda event: (event["speed_drop_kmh"] or 0, event["duration_seconds"] or 0),
        reverse=True,
    )[:8]

    return {
        "lap_index": lap_index,
        "row_start": start,
        "row_end": end,
        "completed_by_timer_reset": window["completed_by_timer_reset"],
        "duration_seconds": observed_duration,
        "lap_time_ms_final_seen": lap_time_final,
        "lap_time_display": fmt_ms(lap_time_final) if lap_time_final is not None else "",
        "observed_vs_lap_timer_gap_seconds": observed_vs_timer_gap,
        "distance_traveled_delta": round(max(distances) - min(distances), 3) if len(distances) >= 2 else None,
        "session_statuses_seen": session_statuses,
        "sector_indices_seen": sector_indices,
        "speed_kmh": stats(speeds),
        "throttle": stats(throttle),
        "brake": stats(brake),
        "steer": stats(steer),
        "control_usage": summarize_control_usage(segment),
        "braking_event_count": len(brake_events),
        "braking_events": brake_events,
        "top_braking_events": top_brake_events,
        "sectors": [
            summarize_sector(segment, sector, sector_start, sector_end)
            for sector, sector_start, sector_end in sector_windows(segment)
        ],
    }


def classify_timed_laps(timed_laps):
    completed_distances = sorted(
        lap["distance_traveled_delta"]
        for lap in timed_laps
        if lap["completed_by_timer_reset"] and lap["distance_traveled_delta"] is not None
    )
    plausible_distances = [
        distance for distance in completed_distances if 1_000 <= distance <= 10_000
    ]
    reference_distance = statistics.median(plausible_distances) if plausible_distances else None

    for lap in timed_laps:
        distance = lap["distance_traveled_delta"]
        lap["reference_distance_m"] = round(reference_distance, 3) if reference_distance is not None else None
        if not lap["completed_by_timer_reset"]:
            lap["analysis_lap_type"] = "incomplete_current_lap"
            lap["likely_valid_for_lap_analysis"] = False
        elif (
            lap["observed_vs_lap_timer_gap_seconds"] is not None
            and abs(lap["observed_vs_lap_timer_gap_seconds"]) > 5
        ):
            lap["analysis_lap_type"] = "timer_discontinuity"
            lap["likely_valid_for_lap_analysis"] = False
        elif reference_distance is not None and distance is not None and distance > reference_distance * 1.15:
            lap["analysis_lap_type"] = "invalid_distance_jump"
            lap["likely_valid_for_lap_analysis"] = False
        elif reference_distance is not None and distance is not None and distance < reference_distance * 0.9:
            lap["analysis_lap_type"] = "partial_or_out_lap"
            lap["likely_valid_for_lap_analysis"] = False
        else:
            lap["analysis_lap_type"] = "valid_timed_lap"
            lap["likely_valid_for_lap_analysis"] = True

    return timed_laps


def annotate_timed_laps_with_track_zones(timed_laps, track):
    track_map = load_track_map(track)

    for lap in timed_laps:
        for event in lap.get("braking_events", []):
            zone = braking_zone_for_distance(track_map, event.get("lap_distance_start_m"))
            event["track_zone"] = zone["name"] if zone else ""
            event["track_zone_notes"] = zone.get("notes", "") if zone else ""

    return timed_laps


def compare_valid_laps(valid_laps):
    if len(valid_laps) < 2:
        return []

    ordered = sorted(valid_laps, key=lambda lap: lap["lap_time_ms_final_seen"] or 10**12)
    best = ordered[0]
    comparisons = []

    for lap in ordered[1:]:
        if best["lap_time_ms_final_seen"] is None or lap["lap_time_ms_final_seen"] is None:
            continue
        comparisons.append(
            {
                "lap_index": lap["lap_index"],
                "compared_to_best_lap_index": best["lap_index"],
                "time_delta_ms": lap["lap_time_ms_final_seen"] - best["lap_time_ms_final_seen"],
                "max_speed_delta_kmh": (
                    round_optional(lap["speed_kmh"]["max"] - best["speed_kmh"]["max"])
                    if lap["speed_kmh"]["max"] is not None and best["speed_kmh"]["max"] is not None
                    else None
                ),
                "full_throttle_share_delta": round_optional(lap["control_usage"]["full_throttle_share"] - best["control_usage"]["full_throttle_share"], 4),
                "braking_share_delta": round_optional(lap["control_usage"]["braking_share"] - best["control_usage"]["braking_share"], 4),
                "coasting_share_delta": round_optional(lap["control_usage"]["coasting_share"] - best["control_usage"]["coasting_share"], 4),
            }
        )

    return comparisons


def sector_lookup(lap):
    return {sector["sector_index"]: sector for sector in lap.get("sectors", [])}


def where_time_went(valid_laps):
    if len(valid_laps) < 2:
        return []

    best = min(valid_laps, key=lambda lap: lap["lap_time_ms_final_seen"] or 10**12)
    best_sectors = sector_lookup(best)
    output = []

    for lap in sorted(valid_laps, key=lambda item: item["lap_time_ms_final_seen"] or 10**12):
        if lap["lap_index"] == best["lap_index"]:
            continue
        if lap["lap_time_ms_final_seen"] is None or best["lap_time_ms_final_seen"] is None:
            continue

        sector_deltas = []
        for sector in lap.get("sectors", []):
            reference = best_sectors.get(sector["sector_index"])
            if not reference:
                continue
            sector_time = sector.get("sector_time_ms")
            reference_time = reference.get("sector_time_ms")
            if sector_time is None or reference_time is None:
                continue
            sector_deltas.append(
                {
                    "sector_index": sector["sector_index"],
                    "delta_ms": sector_time - reference_time,
                    "lap_sector_time_ms": sector_time,
                    "best_sector_time_ms": reference_time,
                    "lap_full_throttle_share": sector["control_usage"]["full_throttle_share"],
                    "best_full_throttle_share": reference["control_usage"]["full_throttle_share"],
                    "lap_braking_share": sector["control_usage"]["braking_share"],
                    "best_braking_share": reference["control_usage"]["braking_share"],
                    "lap_max_speed_kmh": sector["speed_kmh"]["max"],
                    "best_max_speed_kmh": reference["speed_kmh"]["max"],
                }
            )

        sector_deltas.sort(key=lambda item: item["delta_ms"], reverse=True)
        output.append(
            {
                "lap_index": lap["lap_index"],
                "compared_to_best_lap_index": best["lap_index"],
                "total_delta_ms": lap["lap_time_ms_final_seen"] - best["lap_time_ms_final_seen"],
                "largest_sector_losses": sector_deltas,
            }
        )

    return output


def major_braking_events(lap, min_speed_drop=40):
    return [
        event
        for event in lap.get("braking_events", [])
        if (event.get("speed_drop_kmh") or 0) >= min_speed_drop
        and event.get("lap_time_start_ms") is not None
    ]


def match_reference_event(event, reference_events):
    candidates = [
        reference
        for reference in reference_events
        if reference.get("sector_index") == event.get("sector_index")
        and reference.get("lap_time_start_ms") is not None
    ]
    if not candidates:
        return None

    return min(
        candidates,
        key=lambda reference: abs(event["lap_time_start_ms"] - reference["lap_time_start_ms"]),
    )


def compare_braking_events(valid_laps):
    if len(valid_laps) < 2:
        return []

    best = min(valid_laps, key=lambda lap: lap["lap_time_ms_final_seen"] or 10**12)
    best_events = major_braking_events(best)
    output = []

    for lap in sorted(valid_laps, key=lambda item: item["lap_time_ms_final_seen"] or 10**12):
        if lap["lap_index"] == best["lap_index"]:
            continue

        comparisons = []
        for event in major_braking_events(lap):
            reference = match_reference_event(event, best_events)
            if reference is None:
                continue
            time_gap = event["lap_time_start_ms"] - reference["lap_time_start_ms"]
            if abs(time_gap) > 8_000:
                continue
            comparisons.append(
                {
                    "event_label": event["event_label"],
                    "matched_best_event_label": reference["event_label"],
                    "track_zone": event.get("track_zone", ""),
                    "matched_best_track_zone": reference.get("track_zone", ""),
                    "event_index": event["event_index"],
                    "sector_index": event["sector_index"],
                    "lap_event_time": event["lap_time_start_display"],
                    "best_event_time": reference["lap_time_start_display"],
                    "brake_start_delta_ms": time_gap,
                    "start_speed_delta_kmh": (
                        round_optional(event["start_speed_kmh"] - reference["start_speed_kmh"])
                        if event["start_speed_kmh"] is not None and reference["start_speed_kmh"] is not None
                        else None
                    ),
                    "min_speed_delta_kmh": (
                        round_optional(event["min_speed_kmh"] - reference["min_speed_kmh"])
                        if event["min_speed_kmh"] is not None and reference["min_speed_kmh"] is not None
                        else None
                    ),
                    "duration_delta_seconds": (
                        round_optional(event["duration_seconds"] - reference["duration_seconds"])
                        if event["duration_seconds"] is not None and reference["duration_seconds"] is not None
                        else None
                    ),
                    "throttle_pickup_delta_seconds": (
                        round_optional(
                            event["throttle_pickup_after_brake_seconds"]
                            - reference["throttle_pickup_after_brake_seconds"]
                        )
                        if event["throttle_pickup_after_brake_seconds"] is not None
                        and reference["throttle_pickup_after_brake_seconds"] is not None
                        else None
                    ),
                }
            )

        comparisons.sort(
            key=lambda item: abs((item["brake_start_delta_ms"] or 0) / 1000)
            + abs(item["duration_delta_seconds"] or 0)
            + abs((item["min_speed_delta_kmh"] or 0) / 20)
            + abs((item["throttle_pickup_delta_seconds"] or 0) * 2),
            reverse=True,
        )
        output.append(
            {
                "lap_index": lap["lap_index"],
                "compared_to_best_lap_index": best["lap_index"],
                "event_differences": comparisons,
            }
        )

    return output


def generate_first_observations(timed_laps):
    observations = []
    valid_laps = [lap for lap in timed_laps if lap.get("likely_valid_for_lap_analysis")]
    if not valid_laps:
        observations.append("No full valid timed lap was detected yet; capture at least one complete timed lap for useful driving observations.")
        return observations

    best = min(valid_laps, key=lambda lap: lap["lap_time_ms_final_seen"] or 10**12)
    usage = best["control_usage"]
    observations.append(
        f"Best detected lap is timed lap {best['lap_index']} at {best['lap_time_display']}."
    )
    observations.append(
        f"Full-throttle share on that lap was {usage['full_throttle_share']}; braking share was {usage['braking_share']}."
    )
    if usage["coasting_share"] > 0.08:
        observations.append("Coasting share is relatively high; later analysis should check whether throttle pickup after braking can be improved.")
    if usage["throttle_while_high_steering_share"] > 0.04:
        observations.append("There is notable throttle use while steering angle is high; later analysis should check exit stability and understeer risk.")
    if best["top_braking_events"]:
        event = best["top_braking_events"][0]
        observations.append(
            "Largest braking event dropped "
            f"{event['speed_drop_kmh']} km/h over {event['duration_seconds']} seconds."
        )
    return observations


def generate_data_quality_notes(rows, timed_laps, valid_timed_laps, lap_counts):
    notes = []
    session_statuses = sorted({to_int(row.get("session_status")) for row in rows if to_int(row.get("session_status")) is not None})
    cars = sorted({str(row.get("car_model")) for row in rows if row.get("car_model")})
    tracks = sorted({str(row.get("track")) for row in rows if row.get("track")})
    world_x = [to_float(row.get("car_world_x")) for row in rows if to_float(row.get("car_world_x")) is not None]
    world_z = [to_float(row.get("car_world_z")) for row in rows if to_float(row.get("car_world_z")) is not None]
    invalid_lap_types = {}
    for lap in timed_laps:
        if lap.get("likely_valid_for_lap_analysis"):
            continue
        lap_type = lap.get("analysis_lap_type", "unknown")
        invalid_lap_types[lap_type] = invalid_lap_types.get(lap_type, 0) + 1

    if valid_timed_laps:
        notes.append(f"Usable for coaching: {len(valid_timed_laps)} valid timed laps detected.")
    else:
        notes.append("Not usable for lap-to-lap coaching yet: no valid full timed laps were detected.")

    if len(lap_counts) <= 1:
        notes.append(
            "ACC lap_count did not advance during this capture; the analyzer relied only on current-lap timer resets."
        )

    inactive_statuses = [status for status in session_statuses if status in (0, 1, 3)]
    if inactive_statuses:
        if valid_timed_laps and inactive_statuses == [3]:
            notes.append(
                "Session status 3 appeared briefly, but valid timed laps were captured; monitor this only if future laps are rejected."
            )
        else:
            notes.append(
                f"Session status included inactive/menu/non-driving states {inactive_statuses}; avoid pausing, returning to menu, or sitting stopped during the capture."
            )

    if len(cars) > 1:
        notes.append(f"Multiple car models were seen in one capture: {cars}; use one car/session per run for clean comparisons.")

    if len(tracks) > 1:
        notes.append(f"Multiple tracks were seen in one capture: {tracks}; use one track per run for clean comparisons.")

    if has_world_position(rows):
        notes.append("World-position telemetry is available; true path plotting can be generated for this run.")
    else:
        notes.append("World-position telemetry is not available in this run; visual output falls back to lap-distance mapping.")

    if invalid_lap_types:
        details = ", ".join(f"{lap_type}={count}" for lap_type, count in sorted(invalid_lap_types.items()))
        notes.append(f"Rejected lap windows: {details}.")

    return notes


def generate_coaching_candidate_hints(where_time_items, braking_items):
    hints = []
    braking_by_lap = {item["lap_index"]: item for item in braking_items}

    for item in where_time_items[:4]:
        if not item["largest_sector_losses"]:
            continue
        top_sector = item["largest_sector_losses"][0]
        lap_index = item["lap_index"]
        sector_index = top_sector["sector_index"]
        delta_ms = top_sector["delta_ms"]

        parts = [
            f"Timed lap {lap_index} lost most time in sector {sector_index}: {delta_ms} ms versus the best lap."
        ]
        throttle_delta = top_sector["lap_full_throttle_share"] - top_sector["best_full_throttle_share"]
        braking_delta = top_sector["lap_braking_share"] - top_sector["best_braking_share"]
        speed_delta = (
            top_sector["lap_max_speed_kmh"] - top_sector["best_max_speed_kmh"]
            if top_sector["lap_max_speed_kmh"] is not None and top_sector["best_max_speed_kmh"] is not None
            else None
        )

        if throttle_delta < -0.02:
            parts.append("Full-throttle share was lower, so the first hypothesis is later throttle commitment or a compromised exit.")
        if braking_delta > 0.02:
            parts.append("Braking share was higher, so the first hypothesis is over-slowing or braking too long in that sector.")
        if speed_delta is not None and speed_delta < -2:
            parts.append("Max speed was meaningfully lower, so check the preceding exit and straight-line acceleration.")

        braking = braking_by_lap.get(lap_index)
        if braking:
            same_sector = [
                event
                for event in braking["event_differences"]
                if event["sector_index"] == sector_index
            ]
            if same_sector:
                event = same_sector[0]
                event_parts = []
                if event["brake_start_delta_ms"] is not None and abs(event["brake_start_delta_ms"]) >= 300:
                    direction = "later" if event["brake_start_delta_ms"] > 0 else "earlier"
                    event_parts.append(f"brake start was {abs(event['brake_start_delta_ms'])} ms {direction}")
                if event["min_speed_delta_kmh"] is not None and abs(event["min_speed_delta_kmh"]) >= 5:
                    direction = "higher" if event["min_speed_delta_kmh"] > 0 else "lower"
                    event_parts.append(f"minimum speed was {abs(event['min_speed_delta_kmh'])} km/h {direction}")
                if event["throttle_pickup_delta_seconds"] is not None and abs(event["throttle_pickup_delta_seconds"]) >= 0.3:
                    direction = "later" if event["throttle_pickup_delta_seconds"] > 0 else "earlier"
                    event_parts.append(f"throttle pickup was {abs(event['throttle_pickup_delta_seconds'])} s {direction}")
                if event_parts:
                    zone = f" ({event['track_zone']})" if event.get("track_zone") else ""
                    parts.append(f"Nearest major braking event {event['event_label']}{zone} differs: " + ", ".join(event_parts) + ".")

        hints.append(" ".join(parts))

    return hints


def generate_prioritized_recommendation(where_time_items, braking_items):
    braking_by_lap = {item["lap_index"]: item for item in braking_items}
    candidates = {}

    for item in where_time_items:
        total_delta = item.get("total_delta_ms")
        if total_delta is None or total_delta <= 0:
            continue
        if total_delta > 5_000:
            continue

        lap_index = item["lap_index"]
        braking = braking_by_lap.get(lap_index, {})
        event_differences = braking.get("event_differences", [])

        for sector in item.get("largest_sector_losses", []):
            delta_ms = sector.get("delta_ms")
            if delta_ms is None or delta_ms < 100:
                continue

            matching_events = [
                event
                for event in event_differences
                if event.get("sector_index") == sector.get("sector_index")
            ]
            primary_event = matching_events[0] if matching_events else {}
            zone = primary_event.get("track_zone") or f"Sector {sector['sector_index']}"
            key = (sector["sector_index"], zone)

            candidate = candidates.setdefault(
                key,
                {
                    "sector_index": sector["sector_index"],
                    "focus_zone": zone,
                    "total_loss_ms": 0,
                    "occurrences": 0,
                    "laps": [],
                    "signals": {
                        "lower_full_throttle": 0,
                        "more_braking": 0,
                        "lower_max_speed": 0,
                        "lower_min_speed": 0,
                        "later_brake": 0,
                        "earlier_brake": 0,
                        "later_throttle": 0,
                    },
                    "evidence": [],
                },
            )

            candidate["total_loss_ms"] += delta_ms
            candidate["occurrences"] += 1
            candidate["laps"].append(lap_index)

            throttle_delta = sector["lap_full_throttle_share"] - sector["best_full_throttle_share"]
            braking_delta = sector["lap_braking_share"] - sector["best_braking_share"]
            speed_delta = (
                sector["lap_max_speed_kmh"] - sector["best_max_speed_kmh"]
                if sector["lap_max_speed_kmh"] is not None and sector["best_max_speed_kmh"] is not None
                else None
            )

            if throttle_delta < -0.02:
                candidate["signals"]["lower_full_throttle"] += 1
            if braking_delta > 0.02:
                candidate["signals"]["more_braking"] += 1
            if speed_delta is not None and speed_delta < -2:
                candidate["signals"]["lower_max_speed"] += 1

            evidence_parts = [f"timed lap {lap_index} lost {delta_ms} ms in sector {sector['sector_index']}"]

            if primary_event:
                if primary_event.get("min_speed_delta_kmh") is not None and primary_event["min_speed_delta_kmh"] < -5:
                    candidate["signals"]["lower_min_speed"] += 1
                    evidence_parts.append(f"minimum speed was {abs(primary_event['min_speed_delta_kmh'])} km/h lower")
                if primary_event.get("brake_start_delta_ms") is not None:
                    if primary_event["brake_start_delta_ms"] > 300:
                        candidate["signals"]["later_brake"] += 1
                        evidence_parts.append(f"brake start was {primary_event['brake_start_delta_ms']} ms later")
                    elif primary_event["brake_start_delta_ms"] < -300:
                        candidate["signals"]["earlier_brake"] += 1
                        evidence_parts.append(f"brake start was {abs(primary_event['brake_start_delta_ms'])} ms earlier")
                if primary_event.get("throttle_pickup_delta_seconds") is not None and primary_event["throttle_pickup_delta_seconds"] > 0.3:
                    candidate["signals"]["later_throttle"] += 1
                    evidence_parts.append(f"throttle pickup was {primary_event['throttle_pickup_delta_seconds']} s later")

            candidate["evidence"].append("; ".join(evidence_parts))

    if not candidates:
        return {
            "available": False,
            "reason": "Need at least two clean, comparable valid laps with repeated positive time loss.",
        }

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate["occurrences"],
            candidate["total_loss_ms"],
        ),
        reverse=True,
    )
    best = ranked[0]
    signals = best["signals"]

    instruction_parts = [f"At {best['focus_zone']}, keep this as the only next-lap focus."]
    if signals["lower_min_speed"] or signals["more_braking"]:
        instruction_parts.append("Aim to avoid over-slowing the car.")
    if signals["lower_full_throttle"] or signals["later_throttle"]:
        instruction_parts.append("Prioritize a clean exit and commit to throttle earlier once the car is pointed.")
    if signals["later_brake"] and not signals["lower_min_speed"]:
        instruction_parts.append("Try moving the brake point slightly earlier for stability.")
    elif signals["earlier_brake"] and not signals["lower_min_speed"]:
        instruction_parts.append("You may be able to brake slightly later if the entry remains stable.")

    if len(instruction_parts) == 1:
        instruction_parts.append("Repeat the best-lap approach there and do not experiment elsewhere yet.")

    return {
        "available": True,
        "focus_zone": best["focus_zone"],
        "sector_index": best["sector_index"],
        "occurrences": best["occurrences"],
        "total_loss_ms": best["total_loss_ms"],
        "laps": best["laps"],
        "instruction": " ".join(instruction_parts),
        "evidence": best["evidence"][:4],
        "ranked_candidates": ranked[:5],
    }


def sample_world_path(rows, lap, max_points=450):
    segment = rows[lap["row_start"] : lap["row_end"] + 1]
    points = []
    for row in segment:
        x = to_float(row.get("car_world_x"))
        z = to_float(row.get("car_world_z"))
        speed = to_float(row.get("speed_kmh"))
        if x is None or z is None or speed is None or speed < 5:
            continue
        points.append((x, z))

    if len(points) <= max_points:
        return points

    step = max(1, len(points) // max_points)
    return points[::step]


def sample_world_path_from_rows(rows, max_points=700):
    points = []
    for row in rows:
        x = to_float(row.get("car_world_x"))
        z = to_float(row.get("car_world_z"))
        speed = to_float(row.get("speed_kmh"))
        if x is None or z is None or speed is None or speed < 5:
            continue
        points.append((x, z))

    if len(points) <= max_points:
        return points

    step = max(1, len(points) // max_points)
    return points[::step]


def scale_world_paths(paths, width=760, height=270, padding=28):
    all_points = [point for path in paths for point in path]
    if not all_points:
        return []

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_z = min(point[1] for point in all_points)
    max_z = max(point[1] for point in all_points)
    span_x = max(max_x - min_x, 1)
    span_z = max(max_z - min_z, 1)
    scale = min((width - padding * 2) / span_x, (height - padding * 2) / span_z)

    scaled = []
    for path in paths:
        scaled_path = []
        for x, z in path:
            sx = padding + (x - min_x) * scale
            sy = padding + (max_z - z) * scale
            scaled_path.append((sx, sy))
        scaled.append(scaled_path)

    return scaled


def svg_path_from_points(points):
    if not points:
        return ""
    return " ".join(
        ("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}"
        for index, (x, y) in enumerate(points)
    )


def generate_world_position_visual(run_dir, analysis, rows):
    valid_laps = [lap for lap in analysis.get("timed_laps", []) if lap.get("likely_valid_for_lap_analysis")]
    has_comparable_laps = len(valid_laps) >= 2

    if has_comparable_laps:
        best_lap = min(valid_laps, key=lambda lap: lap["lap_time_ms_final_seen"] or 10**12)
        comparison_laps = [
            lap for lap in valid_laps
            if lap["lap_index"] != best_lap["lap_index"]
            and lap.get("lap_time_ms_final_seen") is not None
            and lap["lap_time_ms_final_seen"] - best_lap["lap_time_ms_final_seen"] <= 5_000
        ][:4]
        raw_paths = [sample_world_path(rows, best_lap)] + [sample_world_path(rows, lap) for lap in comparison_laps]
        subtitle = "Telemetry confirms the session track is Spa. Reference map is for readability; the inset shows raw captured position data."
        primary_class = "line-best"
        panel_note = "Blue is the best valid lap; red lines are comparable slower laps. Full map calibration is the next step."
    else:
        raw_paths = [sample_world_path_from_rows(rows)]
        subtitle = "Telemetry confirms the session track is Spa. Reference map is for readability; the inset shows raw captured position data."
        primary_class = "line-best"
        panel_note = "Position capture is working. Drive at least two clean full laps to overlay best and slower lap lines."

    if not raw_paths[0]:
        return None

    scaled_paths = scale_world_paths(raw_paths)
    if not scaled_paths:
        return None

    reference_href = None
    if SPA_REFERENCE_IMAGE.exists():
        reference_copy = run_dir / SPA_REFERENCE_IMAGE.name
        if not reference_copy.exists() or reference_copy.stat().st_size != SPA_REFERENCE_IMAGE.stat().st_size:
            shutil.copyfile(SPA_REFERENCE_IMAGE, reference_copy)
        reference_href = reference_copy.name

    best_path = svg_path_from_points(scaled_paths[0])
    comparison_paths = "\n  ".join(
        f'<path d="{svg_path_from_points(path)}" class="line-slower"/>'
        for path in scaled_paths[1:]
        if path
    )
    recommendation = analysis.get("prioritized_recommendation", {})
    focus = svg_escape(recommendation.get("focus_zone", "not available"))
    instruction_lines = textwrap.wrap(
        recommendation.get("instruction", "Need more valid laps before a map focus is available."),
        width=118,
    )
    instruction_svg = "".join(
        f'<text x="66" y="{696 + (index * 20)}" class="note">{svg_escape(line)}</text>'
        for index, line in enumerate(instruction_lines[:2])
    )

    if reference_href:
        reference_map_svg = f'''
  <rect x="42" y="110" width="896" height="505" class="track-panel"/>
  <image x="42" y="110" width="896" height="505" href="{svg_escape(reference_href)}" preserveAspectRatio="xMidYMid meet" opacity="0.98"/>
'''
    else:
        reference_map_svg = '''
  <rect x="42" y="110" width="896" height="505" class="track-panel"/>
  <text x="66" y="170" class="note">Spa reference image is missing. Add data/track_maps/spa_reference_map.png to enable the readable track map.</text>
'''

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="980" height="780" viewBox="0 0 980 780">
  <style>
    .bg {{ fill: #f8fafc; }}
    .track-panel {{ fill: #ffffff; stroke: #d0d7de; stroke-width: 1; rx: 8; }}
    .trace-panel {{ fill: #ffffff; fill-opacity: 0.92; stroke: #cbd5e1; stroke-width: 1; rx: 8; }}
    .line-best {{ fill: none; stroke: #2563eb; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; opacity: 0.95; }}
    .line-slower {{ fill: none; stroke: #ef4444; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; opacity: 0.42; }}
    .title {{ font: 700 28px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #111827; }}
    .subtitle {{ font: 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #475569; }}
    .focus {{ font: 700 18px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #b42318; }}
    .note {{ font: 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #334155; }}
    .small {{ font: 13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #475569; }}
  </style>
  <rect width="980" height="780" class="bg"/>
  <text x="42" y="54" class="title">Spa Confirmed - Position Trace</text>
  <text x="42" y="80" class="subtitle">{svg_escape(subtitle)}</text>
  {reference_map_svg}
  <rect x="118" y="392" width="820" height="188" class="trace-panel"/>
  <text x="142" y="420" class="small">Raw telemetry trace, not calibrated to the reference map yet</text>
  <g transform="translate(146,450)">
  {comparison_paths}
  <path d="{best_path}" class="{primary_class}"/>
  </g>
  <rect x="42" y="636" width="896" height="112" class="track-panel"/>
  <text x="66" y="668" class="focus">Recommended focus: {focus}</text>
  {instruction_svg}
  <text x="66" y="738" class="small">{svg_escape(panel_note)}</text>
</svg>
'''

    path = run_dir / "spa_world_path_map.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def generate_track_visual(run_dir, analysis, rows):
    real_visual = generate_world_position_visual(run_dir, analysis, rows)
    if real_visual is not None:
        return real_visual

    if normalize_track_key(analysis.get("track")) != "spa":
        return None

    track_map = load_track_map(analysis.get("track"))
    recommendation = analysis.get("prioritized_recommendation", {})
    ranked = recommendation.get("ranked_candidates", []) if recommendation.get("available") else []
    zone_ranges = {zone["name"]: zone for zone in (track_map or {}).get("braking_zones", [])}
    lap_length = (track_map or {}).get("lap_length_m", 7004)
    path_data = " ".join(
        ("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}"
        for index, (x, y) in enumerate(SPA_SCHEMATIC_POINTS)
    )

    markers = []
    for index, candidate in enumerate(ranked[:5]):
        zone = zone_ranges.get(candidate["focus_zone"])
        if not zone:
            continue
        midpoint = (zone["start_m"] + zone["end_m"]) / 2
        x, y = point_at_fraction(SPA_SCHEMATIC_POINTS, midpoint / lap_length)
        radius = 18 if index == 0 else 12
        color = "#d9382e" if index == 0 else "#f59f00"
        label = svg_escape(candidate["focus_zone"])
        loss = svg_escape(f"{candidate['total_loss_ms']} ms")
        markers.append(
            f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}" opacity="0.9"/>'
            f'<text x="{x + 22:.1f}" y="{y - 4:.1f}" class="label">{label}</text>'
            f'<text x="{x + 22:.1f}" y="{y + 14:.1f}" class="small">{loss}, {candidate["occurrences"]} laps</text></g>'
        )

    best_focus = svg_escape(recommendation.get("focus_zone", "not available"))
    instruction_lines = textwrap.wrap(
        recommendation.get("instruction", "Need more valid laps before a map focus is available."),
        width=118,
    )
    instruction_svg = "".join(
        f'<text x="66" y="{608 + (index * 20)}" class="note">{svg_escape(line)}</text>'
        for index, line in enumerate(instruction_lines[:2])
    )
    legend_rows = []
    for candidate in ranked[:4]:
        legend_rows.append(
            f'<li><span>{svg_escape(candidate["focus_zone"])}</span>: '
            f'{candidate["total_loss_ms"]} ms repeated loss on {candidate["occurrences"]} comparable laps</li>'
        )
    if not legend_rows:
        legend_rows.append("<li>No ranked slow zones available.</li>")

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="980" height="680" viewBox="0 0 980 680">
  <style>
    .bg {{ fill: #f8fafc; }}
    .track-shadow {{ fill: none; stroke: #cbd5e1; stroke-width: 34; stroke-linecap: round; stroke-linejoin: round; }}
    .track {{ fill: none; stroke: #1f2937; stroke-width: 18; stroke-linecap: round; stroke-linejoin: round; }}
    .line-best {{ fill: none; stroke: #2563eb; stroke-width: 5; stroke-linecap: round; stroke-linejoin: round; opacity: 0.65; }}
    .line-slower {{ fill: none; stroke: #ef4444; stroke-width: 5; stroke-linecap: round; stroke-linejoin: round; opacity: 0.45; stroke-dasharray: 14 10; }}
    .title {{ font: 700 28px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #111827; }}
    .subtitle {{ font: 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #475569; }}
    .label {{ font: 700 15px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #111827; }}
    .small {{ font: 13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #475569; }}
    .panel {{ fill: #ffffff; stroke: #d0d7de; stroke-width: 1; rx: 8; }}
    .focus {{ font: 700 18px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #b42318; }}
    .note {{ font: 14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #334155; }}
  </style>
  <rect width="980" height="680" class="bg"/>
  <text x="42" y="54" class="title">Spa Time-Loss Map</text>
  <text x="42" y="80" class="subtitle">Schematic lap-distance map from telemetry. It shows slow zones, not true GPS racing lines yet.</text>

  <path d="{path_data}" class="track-shadow"/>
  <path d="{path_data}" class="track"/>
  <path d="{path_data}" class="line-best" transform="translate(-4,-4)"/>
  <path d="{path_data}" class="line-slower" transform="translate(6,6)"/>

  {''.join(markers)}

  <rect x="42" y="548" width="896" height="112" class="panel"/>
  <text x="66" y="580" class="focus">Recommended focus: {best_focus}</text>
  {instruction_svg}
  <text x="66" y="650" class="small">Blue line = best-lap reference path; red dashed line = slower comparable laps, aligned by lap distance.</text>
</svg>
'''

    path = run_dir / "spa_time_loss_map.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def analyze(run_dir):
    rows = load_rows(run_dir)
    received = [to_float(row.get("received_at")) for row in rows if to_float(row.get("received_at")) is not None]
    speeds = [to_float(row.get("speed_kmh")) for row in rows]
    throttle = [to_float(row.get("throttle")) for row in rows]
    brake = [to_float(row.get("brake")) for row in rows]
    steer = [to_float(row.get("steer")) for row in rows]
    lap_counts = sorted({to_int(row.get("lap_count")) for row in rows if to_int(row.get("lap_count")) is not None})
    sectors = sorted({to_int(row.get("current_sector_index")) for row in rows if to_int(row.get("current_sector_index")) is not None})
    track = first_non_empty(rows, "track")
    last_lap_times = [to_completed_lap_time_ms(row.get("last_lap_ms")) for row in rows if to_completed_lap_time_ms(row.get("last_lap_ms")) is not None]
    best_lap_times = [to_completed_lap_time_ms(row.get("best_lap_ms")) for row in rows if to_completed_lap_time_ms(row.get("best_lap_ms")) is not None]
    windows = lap_windows(rows)
    timed_windows = timed_lap_windows(rows)

    laps = [summarize_lap(rows, lap_count, start, end) for lap_count, start, end in windows]
    timed_laps = classify_timed_laps(
        [summarize_timed_lap(rows, window, index + 1) for index, window in enumerate(timed_windows)]
    )
    timed_laps = annotate_timed_laps_with_track_zones(timed_laps, track)
    valid_timed_laps = [lap for lap in timed_laps if lap.get("likely_valid_for_lap_analysis")]
    time_loss = where_time_went(valid_timed_laps)
    braking_differences = compare_braking_events(valid_timed_laps)

    return {
        "run": str(run_dir),
        "rows": len(rows),
        "duration_seconds": round(max(received) - min(received), 3) if len(received) >= 2 else None,
        "track": track,
        "car_model": first_non_empty(rows, "car_model"),
        "player_name": first_non_empty(rows, "player_name"),
        "speed_kmh": stats(speeds),
        "throttle": stats(throttle),
        "brake": stats(brake),
        "steer": stats(steer),
        "lap_counts_seen": lap_counts,
        "sector_indices_seen": sectors,
        "last_lap_ms": stats(last_lap_times),
        "best_lap_ms": stats(best_lap_times),
        "last_lap_display": first_valid_time_display(reversed(rows), "last_lap_display"),
        "best_lap_display": first_valid_time_display(reversed(rows), "best_lap_display"),
        "lap_windows_detected": len(windows),
        "laps": laps,
        "timed_lap_windows_detected": len(timed_windows),
        "valid_timed_laps_detected": len(valid_timed_laps),
        "data_quality_notes": generate_data_quality_notes(rows, timed_laps, valid_timed_laps, lap_counts),
        "timed_laps": timed_laps,
        "first_observations": generate_first_observations(timed_laps),
        "lap_comparisons": compare_valid_laps(valid_timed_laps),
        "where_time_went": time_loss,
        "braking_event_comparisons": braking_differences,
        "coaching_candidate_hints": generate_coaching_candidate_hints(time_loss, braking_differences),
        "prioritized_recommendation": generate_prioritized_recommendation(time_loss, braking_differences),
    }


def write_markdown(run_dir, analysis):
    rows = load_rows(run_dir)
    visual_path = generate_track_visual(run_dir, analysis, rows)
    lines = [
        "# ACC Session Analysis",
        "",
        f"Run: `{analysis['run']}`",
        f"Rows: {analysis['rows']}",
        f"Duration: {analysis['duration_seconds']} seconds",
        f"Track: {analysis['track'] or 'not captured'}",
        f"Car: {analysis['car_model'] or 'not captured'}",
        "",
        "## Session Metrics",
        "",
        f"- Max speed: {analysis['speed_kmh']['max']} km/h",
        f"- Throttle range: {analysis['throttle']['min']} to {analysis['throttle']['max']}",
        f"- Brake range: {analysis['brake']['min']} to {analysis['brake']['max']}",
        f"- Steering range: {analysis['steer']['min']} to {analysis['steer']['max']}",
        f"- Lap counts seen: {analysis['lap_counts_seen']}",
        f"- Sector indices seen: {analysis['sector_indices_seen']}",
        f"- Last lap: {analysis['last_lap_display'] or 'not captured'}",
        f"- Best lap: {analysis['best_lap_display'] or 'not captured'}",
        f"- Timed lap windows detected: {analysis['timed_lap_windows_detected']}",
        f"- Valid timed laps detected: {analysis['valid_timed_laps_detected']}",
        "",
        "## Data Quality",
        "",
    ]

    for note in analysis["data_quality_notes"]:
        lines.append(f"- {note}")

    lines.extend([
        "",
        "## First Observations",
        "",
    ])

    for observation in analysis["first_observations"]:
        lines.append(f"- {observation}")

    recommendation = analysis["prioritized_recommendation"]
    lines.extend(["", "## Recommended Next-Lap Focus", ""])
    if not recommendation["available"]:
        lines.append(recommendation["reason"])
    else:
        lines.extend(
            [
                f"- Focus zone: {recommendation['focus_zone']}",
                f"- Sector: {recommendation['sector_index']}",
                f"- Evidence strength: seen on {recommendation['occurrences']} comparable laps, total repeated loss {recommendation['total_loss_ms']} ms",
                f"- Instruction: {recommendation['instruction']}",
                "",
                "Evidence:",
            ]
        )
        for evidence in recommendation["evidence"]:
            lines.append(f"- {evidence}")

    if visual_path is not None:
        lines.extend(
            [
                "",
                "## Visual Map",
                "",
                f"![Spa reference map with raw telemetry trace]({visual_path.name})",
                "",
                "Note: Spa reports use the reference map for readability. The blue/red inset is raw `car_world_x`/`car_world_z` telemetry and is not calibrated to the map background yet.",
            ]
        )

    lines.extend(["", "## Coaching Candidate Hints", ""])
    if not analysis["coaching_candidate_hints"]:
        lines.append("Need at least two valid timed laps before coaching candidate hints are useful.")
    else:
        for hint in analysis["coaching_candidate_hints"]:
            lines.append(f"- {hint}")

    lines.extend(["", "## Lap Comparisons", ""])
    if not analysis["lap_comparisons"]:
        lines.append("Need at least two valid timed laps before lap-to-lap comparison is useful.")
    else:
        for comparison in analysis["lap_comparisons"]:
            lines.append(
                f"- Timed lap {comparison['lap_index']} vs best lap {comparison['compared_to_best_lap_index']}: "
                f"+{comparison['time_delta_ms']} ms, "
                f"max_speed_delta={comparison['max_speed_delta_kmh']} km/h, "
                f"full_throttle_share_delta={comparison['full_throttle_share_delta']}, "
                f"braking_share_delta={comparison['braking_share_delta']}, "
                f"coasting_share_delta={comparison['coasting_share_delta']}"
            )

    lines.extend(["", "## Where Time Went", ""])
    if not analysis["where_time_went"]:
        lines.append("Need at least two valid timed laps before sector loss analysis is useful.")
    else:
        for item in analysis["where_time_went"]:
            lines.append(
                f"### Timed lap {item['lap_index']} vs best lap {item['compared_to_best_lap_index']} "
                f"(+{item['total_delta_ms']} ms)"
            )
            lines.append("")
            for sector in item["largest_sector_losses"]:
                lines.append(
                    f"- Sector {sector['sector_index']}: {sector['delta_ms']} ms "
                    f"({fmt_ms(sector['lap_sector_time_ms'])} vs {fmt_ms(sector['best_sector_time_ms'])}); "
                    f"full_throttle_share {sector['lap_full_throttle_share']} vs {sector['best_full_throttle_share']}, "
                    f"braking_share {sector['lap_braking_share']} vs {sector['best_braking_share']}, "
                    f"max_speed {sector['lap_max_speed_kmh']} vs {sector['best_max_speed_kmh']} km/h"
                )
            lines.append("")

    lines.extend(["", "## Braking Event Differences", ""])
    if not analysis["braking_event_comparisons"]:
        lines.append("Need at least two valid timed laps before braking-event comparison is useful.")
    else:
        for item in analysis["braking_event_comparisons"]:
            lines.append(
                f"### Timed lap {item['lap_index']} vs best lap {item['compared_to_best_lap_index']}"
            )
            lines.append("")
            if not item["event_differences"]:
                lines.append("- No matching braking events found.")
            for event in item["event_differences"][:6]:
                lines.append(
                    f"- {event['event_label']} {event['track_zone'] or 'unmapped zone'} sector {event['sector_index']} "
                    f"(matched best {event['matched_best_event_label']}, "
                    f"{event['lap_event_time']} vs {event['best_event_time']}): "
                    f"brake_start_delta={event['brake_start_delta_ms']} ms, "
                    f"start_speed_delta={event['start_speed_delta_kmh']} km/h, "
                    f"min_speed_delta={event['min_speed_delta_kmh']} km/h, "
                    f"duration_delta={event['duration_delta_seconds']}s, "
                    f"throttle_pickup_delta={event['throttle_pickup_delta_seconds']}s"
                )
            lines.append("")

    lines.extend(
        [
        "",
        "## Timed Laps",
        "",
        ]
    )

    if not analysis["timed_laps"]:
        lines.append("No timed lap windows detected yet. This usually means lap timer metadata was not captured in this run.")
    else:
        for lap in analysis["timed_laps"]:
            status = lap["analysis_lap_type"]
            usage = lap["control_usage"]
            lines.extend(
                [
                    f"### Timed lap {lap['lap_index']} ({status})",
                    "",
                    f"- Valid for lap analysis: {lap['likely_valid_for_lap_analysis']}",
                    f"- Duration observed: {lap['duration_seconds']} seconds",
                    f"- Final lap timer seen: {lap['lap_time_display'] or 'not captured'}",
                    f"- Observed vs lap timer gap: {lap['observed_vs_lap_timer_gap_seconds']} seconds",
                    f"- Distance delta: {lap['distance_traveled_delta']} m",
                    f"- Sectors seen: {lap['sector_indices_seen']}",
                    f"- Max speed: {lap['speed_kmh']['max']} km/h",
                    f"- Full throttle share: {usage['full_throttle_share']}",
                    f"- Braking share: {usage['braking_share']}",
                    f"- Heavy braking share: {usage['heavy_braking_share']}",
                    f"- Coasting share: {usage['coasting_share']}",
                    f"- High steering + throttle share: {usage['throttle_while_high_steering_share']}",
                    f"- Braking events detected: {lap['braking_event_count']}",
                    "",
                ]
            )
            if lap["top_braking_events"]:
                lines.extend(["Top braking events:", ""])
                for idx, event in enumerate(lap["top_braking_events"][:5], start=1):
                    pickup = event["throttle_pickup_after_brake_seconds"]
                    pickup_text = f"{pickup}s" if pickup is not None else "not detected"
                    lines.append(
                        f"{idx}. {event['event_label']} zone={event['track_zone'] or 'unmapped'}, "
                        f"sector={event['sector_index']}, "
                        f"lap_time={event['lap_time_start_display']}, "
                        f"lap_distance={event['lap_distance_start_m']}m, "
                        f"duration={event['duration_seconds']}s, "
                        f"speed_drop={event['speed_drop_kmh']} km/h, "
                        f"start_speed={event['start_speed_kmh']} km/h, "
                        f"min_speed={event['min_speed_kmh']} km/h, "
                        f"max_brake={event['max_brake']}, "
                        f"throttle_pickup_after={pickup_text}"
                    )
                lines.append("")
            if lap["sectors"]:
                lines.extend(["Sector summaries:", ""])
                for sector in lap["sectors"]:
                    lines.append(
                        f"- Sector {sector['sector_index']}: "
                        f"duration={sector['duration_seconds']}s, "
                        f"lap_time={fmt_ms(sector['lap_time_start_ms'])} to {fmt_ms(sector['lap_time_end_ms'])}, "
                        f"distance={sector['distance_delta_m']}m, "
                        f"max_speed={sector['speed_kmh']['max']} km/h, "
                        f"braking_share={sector['control_usage']['braking_share']}, "
                        f"full_throttle_share={sector['control_usage']['full_throttle_share']}"
                    )
                lines.append("")

    lines.extend(["## Lap Counter Windows", ""])
    if not analysis["laps"]:
        lines.append("No lap counter windows detected.")
    else:
        for lap in analysis["laps"]:
            lines.extend(
                [
                    f"### Lap counter value {lap['lap_count_value']}",
                    "",
                    f"- Duration observed: {lap['duration_seconds']} seconds",
                    f"- Final lap timer seen: {lap['lap_time_ms_final_seen']} ms",
                    f"- Current lap display seen: {lap['current_lap_display_seen'] or 'not captured'}",
                    f"- Last lap display seen: {lap['last_lap_display_seen'] or 'not captured'}",
                    f"- Best lap display seen: {lap['best_lap_display_seen'] or 'not captured'}",
                    "",
                ]
            )

    path = run_dir / "analysis.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Analyze an ACC telemetry run")
    parser.add_argument("run_dir", help="Path to a run folder, for example runs/acc-stability-1")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    analysis = analyze(run_dir)

    json_path = run_dir / "analysis.json"
    json_path.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    md_path = write_markdown(run_dir, analysis)

    print(json_path)
    print(md_path)


if __name__ == "__main__":
    main()
