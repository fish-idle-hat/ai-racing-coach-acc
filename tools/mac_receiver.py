#!/usr/bin/env python3
import argparse
import csv
import json
import socket
import statistics
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
INVALID_TIME_MS = 2_000_000_000


CSV_FIELDS = [
    "received_at",
    "source",
    "packet_id",
    "speed_kmh",
    "throttle",
    "brake",
    "steer",
    "gear",
    "gear_raw",
    "rpm",
    "fuel",
    "tyre_core_temp_fl",
    "tyre_core_temp_fr",
    "tyre_core_temp_rl",
    "tyre_core_temp_rr",
    "air_temp_c",
    "road_temp_c",
    "wheel_slip_fl",
    "wheel_slip_fr",
    "wheel_slip_rl",
    "wheel_slip_rr",
    "acc_g_x",
    "acc_g_y",
    "acc_g_z",
    "heading",
    "pitch",
    "roll",
    "car_damage_front",
    "car_damage_rear",
    "car_damage_left",
    "car_damage_right",
    "car_damage_center",
    "car_damage_total",
    "number_of_tyres_out",
    "pit_limiter_on",
    "abs_active",
    "local_angular_vel_x",
    "local_angular_vel_y",
    "local_angular_vel_z",
    "graphics_packet_id",
    "session_status",
    "session_type",
    "current_lap_display",
    "last_lap_display",
    "best_lap_display",
    "split_display",
    "lap_time_ms",
    "last_lap_ms",
    "best_lap_ms",
    "lap_count",
    "position",
    "session_time_left",
    "distance_traveled",
    "is_in_pit",
    "current_sector_index",
    "last_sector_time_ms",
    "number_of_laps",
    "is_valid_lap",
    "is_valid_lap_candidate_wide",
    "is_valid_lap_candidate_ansi",
    "current_lap_invalid",
    "current_lap_valid",
    "normalized_car_position",
    "player_car_id",
    "player_car_index",
    "car_world_x",
    "car_world_y",
    "car_world_z",
    "acc_raw_car_world_x",
    "acc_raw_car_world_y",
    "acc_raw_car_world_z",
    "legacy_player_car_id",
    "legacy_player_car_index",
    "legacy_car_world_x",
    "legacy_car_world_y",
    "legacy_car_world_z",
    "track",
    "car_model",
    "player_name",
    "shared_memory_version",
    "acc_version",
]


def now_stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_valid_time_ms(value):
    parsed = as_int(value)
    if parsed is None or parsed < 0 or parsed >= INVALID_TIME_MS:
        return None
    return parsed


def summarize(rows, started_at, ended_at):
    duration = max(0.0, ended_at - started_at)
    packet_count = len(rows)
    rates = []
    last_t = None
    speeds = []
    throttle_values = []
    brake_values = []
    steer_values = []
    intervals = []
    lap_counts = set()
    tracks = set()
    cars = set()
    session_statuses = set()
    session_types = set()
    lap_times = []
    last_lap_times = []
    best_lap_times = []
    sector_indices = set()
    valid_lap_values = set()
    current_lap_invalid_values = set()
    current_lap_valid_values = set()
    normalized_positions = []
    car_world_x = []
    car_world_y = []
    car_world_z = []
    tyre_core_temps = []
    air_temps = []
    road_temps = []
    car_damage_totals = []
    tyres_out_values = []
    abs_local_angular_velocities = []

    for row in rows:
        received_at = as_float(row.get("received_at"))
        if received_at is not None and last_t is not None:
            dt = received_at - last_t
            if dt > 0:
                intervals.append(dt)
                rates.append(1.0 / dt)
        if received_at is not None:
            last_t = received_at

        for key, bucket in (
            ("speed_kmh", speeds),
            ("throttle", throttle_values),
            ("brake", brake_values),
            ("steer", steer_values),
        ):
            value = as_float(row.get(key))
            if value is not None:
                bucket.append(value)

        lap_count = as_int(row.get("lap_count"))
        if lap_count is not None:
            lap_counts.add(lap_count)
        for key, bucket in (
            ("session_status", session_statuses),
            ("session_type", session_types),
            ("current_sector_index", sector_indices),
        ):
            value = as_int(row.get(key))
            if value is not None:
                bucket.add(value)
        for key, bucket in (
            ("is_valid_lap", valid_lap_values),
            ("current_lap_invalid", current_lap_invalid_values),
            ("current_lap_valid", current_lap_valid_values),
        ):
            value = as_int(row.get(key))
            if value is not None:
                bucket.add(value)
        for key, bucket in (
            ("lap_time_ms", lap_times),
            ("last_lap_ms", last_lap_times),
            ("best_lap_ms", best_lap_times),
        ):
            value = as_valid_time_ms(row.get(key))
            if value is not None:
                bucket.append(value)
        for key, bucket in (
            ("normalized_car_position", normalized_positions),
            ("car_world_x", car_world_x),
            ("car_world_y", car_world_y),
            ("car_world_z", car_world_z),
            ("air_temp_c", air_temps),
            ("road_temp_c", road_temps),
        ):
            value = as_float(row.get(key))
            if value is not None:
                bucket.append(value)
        for key in ("tyre_core_temp_fl", "tyre_core_temp_fr", "tyre_core_temp_rl", "tyre_core_temp_rr"):
            value = as_float(row.get(key))
            if value is not None and value > 0:
                tyre_core_temps.append(value)
        damage_total = as_float(row.get("car_damage_total"))
        if damage_total is not None:
            car_damage_totals.append(damage_total)
        tyres_out = as_int(row.get("number_of_tyres_out"))
        if tyres_out is not None:
            tyres_out_values.append(tyres_out)
        angular_components = [
            as_float(row.get("local_angular_vel_x")),
            as_float(row.get("local_angular_vel_y")),
            as_float(row.get("local_angular_vel_z")),
        ]
        angular_components = [abs(value) for value in angular_components if value is not None]
        if angular_components:
            abs_local_angular_velocities.append(max(angular_components))
        if row.get("track"):
            tracks.add(str(row["track"]))
        if row.get("car_model"):
            cars.add(str(row["car_model"]))

    def stats(values):
        if not values:
            return {"min": None, "max": None, "mean": None}
        return {
            "min": min(values),
            "max": max(values),
            "mean": statistics.fmean(values),
        }

    timing_health = {
        "max_interval_seconds": max(intervals) if intervals else None,
        "intervals_over_0_1s": sum(1 for value in intervals if value > 0.1),
        "intervals_over_0_5s": sum(1 for value in intervals if value > 0.5),
        "intervals_over_1_0s": sum(1 for value in intervals if value > 1.0),
    }

    return {
        "duration_seconds": round(duration, 3),
        "packet_count": packet_count,
        "average_packets_per_second": round(packet_count / duration, 3) if duration else 0,
        "instant_packet_rate_hz": stats(rates),
        "receiver_timing_health": timing_health,
        "speed_kmh": stats(speeds),
        "throttle": stats(throttle_values),
        "brake": stats(brake_values),
        "steer": stats(steer_values),
        "lap_time_ms": stats(lap_times),
        "last_lap_ms": stats(last_lap_times),
        "best_lap_ms": stats(best_lap_times),
        "normalized_car_position": stats(normalized_positions),
        "car_world_x": stats(car_world_x),
        "car_world_y": stats(car_world_y),
        "car_world_z": stats(car_world_z),
        "tyre_core_temp_c": stats(tyre_core_temps),
        "air_temp_c": stats(air_temps),
        "road_temp_c": stats(road_temps),
        "car_damage_total": stats(car_damage_totals),
        "number_of_tyres_out": stats(tyres_out_values),
        "max_abs_local_angular_vel": stats(abs_local_angular_velocities),
        "lap_counts_seen": sorted(lap_counts),
        "session_statuses_seen": sorted(session_statuses),
        "session_types_seen": sorted(session_types),
        "sector_indices_seen": sorted(sector_indices),
        "is_valid_lap_values_seen": sorted(valid_lap_values),
        "current_lap_invalid_values_seen": sorted(current_lap_invalid_values),
        "current_lap_valid_values_seen": sorted(current_lap_valid_values),
        "tracks_seen": sorted(tracks),
        "cars_seen": sorted(cars),
        "pass_hints": {
            "has_packets": packet_count > 0,
            "speed_changes": bool(speeds and max(speeds) - min(speeds) > 5),
            "throttle_changes": bool(throttle_values and max(throttle_values) - min(throttle_values) > 0.1),
            "brake_changes": bool(brake_values and max(brake_values) - min(brake_values) > 0.1),
            "steer_changes": bool(steer_values and max(steer_values) - min(steer_values) > 0.05),
            "has_world_position": bool(
                len(car_world_x) > 10
                and len(car_world_z) > 10
                and max(car_world_x) - min(car_world_x) > 1
                and max(car_world_z) - min(car_world_z) > 1
            ),
            "has_incident_evidence_fields": bool(
                car_damage_totals or tyres_out_values or abs_local_angular_velocities
            ),
        },
    }


def normalize_packet(packet):
    normalized = {field: "" for field in CSV_FIELDS}
    normalized["received_at"] = time.time()
    normalized["source"] = packet.get("source", "unknown")

    aliases = {
        "packet_id": ["packet_id", "packetId"],
        "speed_kmh": ["speed_kmh", "speedKmh"],
        "throttle": ["throttle", "gas"],
        "brake": ["brake"],
        "steer": ["steer", "steerAngle"],
        "gear": ["gear"],
        "gear_raw": ["gear_raw", "raw_gear"],
        "rpm": ["rpm", "rpms"],
        "fuel": ["fuel"],
        "tyre_core_temp_fl": ["tyre_core_temp_fl", "tyreCoreTempFL"],
        "tyre_core_temp_fr": ["tyre_core_temp_fr", "tyreCoreTempFR"],
        "tyre_core_temp_rl": ["tyre_core_temp_rl", "tyreCoreTempRL"],
        "tyre_core_temp_rr": ["tyre_core_temp_rr", "tyreCoreTempRR"],
        "air_temp_c": ["air_temp_c", "airTemp"],
        "road_temp_c": ["road_temp_c", "roadTemp"],
        "wheel_slip_fl": ["wheel_slip_fl"],
        "wheel_slip_fr": ["wheel_slip_fr"],
        "wheel_slip_rl": ["wheel_slip_rl"],
        "wheel_slip_rr": ["wheel_slip_rr"],
        "acc_g_x": ["acc_g_x"],
        "acc_g_y": ["acc_g_y"],
        "acc_g_z": ["acc_g_z"],
        "heading": ["heading"],
        "pitch": ["pitch"],
        "roll": ["roll"],
        "car_damage_front": ["car_damage_front"],
        "car_damage_rear": ["car_damage_rear"],
        "car_damage_left": ["car_damage_left"],
        "car_damage_right": ["car_damage_right"],
        "car_damage_center": ["car_damage_center"],
        "car_damage_total": ["car_damage_total"],
        "number_of_tyres_out": ["number_of_tyres_out"],
        "pit_limiter_on": ["pit_limiter_on"],
        "abs_active": ["abs_active"],
        "local_angular_vel_x": ["local_angular_vel_x"],
        "local_angular_vel_y": ["local_angular_vel_y"],
        "local_angular_vel_z": ["local_angular_vel_z"],
        "graphics_packet_id": ["graphics_packet_id", "graphicsPacketId"],
        "session_status": ["session_status", "status"],
        "session_type": ["session_type", "session"],
        "current_lap_display": ["current_lap_display", "currentTime"],
        "last_lap_display": ["last_lap_display", "lastTime"],
        "best_lap_display": ["best_lap_display", "bestTime"],
        "split_display": ["split_display", "split"],
        "lap_time_ms": ["lap_time_ms", "currentTimeMs", "current_lap_ms"],
        "last_lap_ms": ["last_lap_ms", "lastTimeMs", "last_lap_ms"],
        "best_lap_ms": ["best_lap_ms", "bestTimeMs", "best_lap_ms"],
        "lap_count": ["lap_count", "completedLaps"],
        "position": ["position"],
        "session_time_left": ["session_time_left"],
        "distance_traveled": ["distance_traveled"],
        "is_in_pit": ["is_in_pit"],
        "current_sector_index": ["current_sector_index"],
        "last_sector_time_ms": ["last_sector_time_ms"],
        "number_of_laps": ["number_of_laps"],
        "is_valid_lap": ["is_valid_lap", "isValidLap"],
        "is_valid_lap_candidate_wide": ["is_valid_lap_candidate_wide"],
        "is_valid_lap_candidate_ansi": ["is_valid_lap_candidate_ansi"],
        "current_lap_invalid": ["current_lap_invalid", "currentLapInvalid"],
        "current_lap_valid": ["current_lap_valid", "currentLapValid"],
        "normalized_car_position": ["normalized_car_position", "normalizedCarPosition"],
        "player_car_id": ["player_car_id", "playerCarId", "playerCarID"],
        "player_car_index": ["player_car_index", "playerCarIndex"],
        "car_world_x": ["car_world_x", "world_x", "carWorldX"],
        "car_world_y": ["car_world_y", "world_y", "carWorldY"],
        "car_world_z": ["car_world_z", "world_z", "carWorldZ"],
        "acc_raw_car_world_x": ["acc_raw_car_world_x"],
        "acc_raw_car_world_y": ["acc_raw_car_world_y"],
        "acc_raw_car_world_z": ["acc_raw_car_world_z"],
        "legacy_player_car_id": ["legacy_player_car_id"],
        "legacy_player_car_index": ["legacy_player_car_index"],
        "legacy_car_world_x": ["legacy_car_world_x"],
        "legacy_car_world_y": ["legacy_car_world_y"],
        "legacy_car_world_z": ["legacy_car_world_z"],
        "track": ["track"],
        "car_model": ["car_model", "carModel"],
        "player_name": ["player_name"],
        "shared_memory_version": ["shared_memory_version"],
        "acc_version": ["acc_version"],
    }

    for target, keys in aliases.items():
        for key in keys:
            if key in packet and packet[key] is not None:
                normalized[target] = packet[key]
                break
    return normalized


def main():
    parser = argparse.ArgumentParser(description="AI Racing Coach - ACC Milestone 0 UDP telemetry receiver")
    parser.add_argument("--host", default="127.0.0.1", help="UDP host to bind")
    parser.add_argument("--port", type=int, default=47777, help="UDP port to bind")
    parser.add_argument("--run-name", default=None, help="Optional run folder name")
    parser.add_argument("--quiet", action="store_true", help="Reduce live console output")
    args = parser.parse_args()

    run_name = args.run_name or now_stamp()
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=False)

    ndjson_path = run_dir / "telemetry.ndjson"
    csv_path = run_dir / "telemetry.csv"
    summary_path = run_dir / "summary.json"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)

    print(f"Listening for ACC telemetry on udp://{args.host}:{args.port}")
    print(f"Saving run to {run_dir}")
    print("Stop with Ctrl+C.")

    rows = []
    started_at = time.time()
    last_print = 0.0

    with ndjson_path.open("w", encoding="utf-8") as ndjson_file, csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

        try:
            while True:
                try:
                    data, _addr = sock.recvfrom(65535)
                except socket.timeout:
                    if not args.quiet and time.time() - last_print > 5:
                        print("Waiting for packets...")
                        last_print = time.time()
                    continue

                try:
                    packet = json.loads(data.decode("utf-8"))
                except Exception as exc:
                    print(f"Skipped unreadable packet: {exc}")
                    continue

                row = normalize_packet(packet)
                rows.append(row)
                ndjson_file.write(json.dumps(packet, separators=(",", ":")) + "\n")
                ndjson_file.flush()
                writer.writerow(row)
                csv_file.flush()

                if not args.quiet and time.time() - last_print > 2:
                    print(
                        "packets={count} speed={speed} throttle={throttle} brake={brake} steer={steer} gear={gear} rpm={rpm} pos=({x},{y},{z})".format(
                            count=len(rows),
                            speed=row.get("speed_kmh"),
                            throttle=row.get("throttle"),
                            brake=row.get("brake"),
                            steer=row.get("steer"),
                            gear=row.get("gear"),
                            rpm=row.get("rpm"),
                            x=row.get("car_world_x"),
                            y=row.get("car_world_y"),
                            z=row.get("car_world_z"),
                        )
                    )
                    last_print = time.time()
        except KeyboardInterrupt:
            pass
        finally:
            ended_at = time.time()
            summary = summarize(rows, started_at, ended_at)
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print("")
            print("Receiver stopped.")
            print(f"Packets: {summary['packet_count']}")
            print(f"Average rate: {summary['average_packets_per_second']} packets/sec")
            print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
