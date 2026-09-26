#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import queue
import re
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mac_receiver import CSV_FIELDS, normalize_packet, summarize
from coach_decision import build_lap_decision, issue_key, write_decision_artifacts
from driver_model import live_issue_counts, live_issue_stats, load_profile, update_profile
from reference_compare import REFERENCES_DIR, compare_run, slug
from turn_timing import build_report as build_turn_timing_report, write_report as write_turn_timing_report


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
TRACK_MAPS_DIR = ROOT / "data" / "track_maps"
SPA_MAP_PATH = TRACK_MAPS_DIR / "spa.json"
SPA_WORLD_GATES_PATH = TRACK_MAPS_DIR / "spa_world_gates.json"
SPA_WORLD_PATH_PATH = TRACK_MAPS_DIR / "spa_world_path.json"
PRO_VIDEO_LATEST_PATH = ROOT / "data" / "pro_video_references" / "latest_pro_video_reference.json"
INTRO_COLLECTION_MESSAGE = (
    "I will be observing and collecting data from now, and for the best results, "
    "it would take approximately 2 laps. Go ahead!"
)


def now_stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


NEGATIVE_DISTANCE_DIRECTION_RE = re.compile(
    r"(?<![\w.])(?P<prefix>about\s+|approximately\s+)?(?P<sign>negative\s+|-)"
    r"(?P<amount>\d+(?:\.\d+)?(?:\s*(?:-|to)\s*\d+(?:\.\d+)?)?)\s*"
    r"(?P<unit>m|meters?)\s+(?P<too>too\s+)?(?P<direction>earlier|later|early|late)\b",
    re.IGNORECASE,
)
NEGATIVE_DISTANCE_AFTER_DIRECTION_RE = re.compile(
    r"\b(?P<direction>earlier|later)\s+(?:by\s+)?(?P<sign>negative\s+|-)"
    r"(?P<amount>\d+(?:\.\d+)?(?:\s*(?:-|to)\s*\d+(?:\.\d+)?)?)\s*(?P<unit>m|meters?)\b",
    re.IGNORECASE,
)


def flipped_direction(direction):
    lower = direction.lower()
    if lower in {"earlier", "early"}:
        flipped = "later" if lower == "earlier" else "late"
    else:
        flipped = "earlier" if lower == "later" else "early"
    return flipped


def sanitize_directional_distance_text(message):
    def replace_before_direction(match):
        prefix = match.group("prefix") or ""
        amount = match.group("amount")
        unit = match.group("unit")
        too = match.group("too") or ""
        direction = flipped_direction(match.group("direction"))
        return f"{prefix}{amount} {unit} {too}{direction}"

    def replace_after_direction(match):
        direction = flipped_direction(match.group("direction"))
        amount = match.group("amount")
        unit = match.group("unit")
        return f"{direction} by {amount} {unit}"

    text = NEGATIVE_DISTANCE_DIRECTION_RE.sub(replace_before_direction, message)
    return NEGATIVE_DISTANCE_AFTER_DIRECTION_RE.sub(replace_after_direction, text)


def sanitize_coach_messages(messages):
    return [sanitize_directional_distance_text(message) for message in messages]


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


def fmt_lap_time(ms):
    if ms is None:
        return "--:--.---"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def lap_validity_state(row):
    crew_invalid = to_int(row.get("current_lap_invalid"))
    if crew_invalid in (0, 1):
        return "invalid" if crew_invalid == 1 else "valid"

    crew_valid = to_int(row.get("current_lap_valid"))
    if crew_valid in (0, 1):
        return "valid" if crew_valid == 1 else "invalid"

    acc_valid = to_int(row.get("is_valid_lap"))
    if acc_valid in (0, 1):
        return "valid" if acc_valid == 1 else "invalid"

    return "unknown"


def load_spa_map():
    if not SPA_MAP_PATH.exists():
        raise SystemExit(f"Missing Spa map: {SPA_MAP_PATH}")
    return json.loads(SPA_MAP_PATH.read_text(encoding="utf-8"))


def load_spa_world_gates():
    if not SPA_WORLD_GATES_PATH.exists():
        return []
    return json.loads(SPA_WORLD_GATES_PATH.read_text(encoding="utf-8")).get("gates", [])


def load_spa_world_path():
    if not SPA_WORLD_PATH_PATH.exists():
        return None
    return json.loads(SPA_WORLD_PATH_PATH.read_text(encoding="utf-8"))


def load_pro_video_reference(path=None):
    if path:
        reference_path = Path(path)
    else:
        if not PRO_VIDEO_LATEST_PATH.exists():
            return None
        latest = json.loads(PRO_VIDEO_LATEST_PATH.read_text(encoding="utf-8"))
        reference_path = Path(latest.get("pro_reference") or "")
    if not reference_path.exists():
        return None
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    reference["_path"] = str(reference_path)
    return reference


def world_position(row):
    x = to_float(row.get("car_world_x"))
    z = to_float(row.get("car_world_z"))
    if x is None or z is None:
        return None
    if abs(x) < 1e-20 and abs(z) < 1e-20:
        return None
    return x, z


def track_distance(row, lap_length_m, position_offset_lap=0.0):
    normalized = normalized_lap_position(row, position_offset_lap)
    if normalized is not None:
        return normalized * lap_length_m

    distance = to_float(row.get("distance_traveled"))
    if distance is not None and 0 <= distance <= lap_length_m * 1.2:
        return distance % lap_length_m

    return None


def normalized_lap_position(row, position_offset_lap=0.0):
    normalized = to_float(row.get("normalized_car_position"))
    if normalized is None or not 0 <= normalized <= 1:
        return None
    return (normalized + position_offset_lap) % 1.0


def zone_for_distance(track_map, distance_m):
    if distance_m is None:
        return None
    lap_length = track_map.get("lap_length_m", 7004)
    wrapped_distance = distance_m % lap_length
    for zone in track_map.get("braking_zones", []):
        start = zone["start_m"]
        end = zone["end_m"]
        if start <= wrapped_distance < min(end, lap_length):
            return zone
        if end > lap_length and (wrapped_distance >= start or wrapped_distance < end - lap_length):
            return zone
    return None


def zone_progress(zone, distance_m, lap_length_m):
    if zone is None or distance_m is None:
        return None
    wrapped = distance_m % lap_length_m
    start = zone["start_m"]
    end = zone["end_m"]
    if end > lap_length_m and wrapped < end - lap_length_m:
        wrapped += lap_length_m
    span = max(1, end - start)
    return max(0.0, min(1.0, (wrapped - start) / span))


@dataclass
class ZoneState:
    name: str
    lap_count: int | None
    entered_at: float
    entry_lap_time_ms: int | None = None
    last_lap_time_ms: int | None = None
    entry_speed: float | None = None
    exit_speed: float | None = None
    min_speed: float | None = None
    max_speed: float | None = None
    max_brake: float = 0.0
    first_brake_lap_time_ms: int | None = None
    first_brake_distance_m: float | None = None
    brake_release_lap_time_ms: int | None = None
    brake_release_distance_m: float | None = None
    throttle_pickup_lap_time_ms: int | None = None
    throttle_pickup_distance_m: float | None = None
    max_abs_steer: float = 0.0
    high_steer_throttle_samples: int = 0
    brake_throttle_overlap_samples: int = 0
    entry_damage_total: float | None = None
    max_damage_total: float | None = None
    max_number_of_tyres_out: int = 0
    max_abs_local_angular_vel: float = 0.0
    max_wheel_slip: float = 0.0
    official_invalid_triggered: bool = False
    official_invalid_lap_time_ms: int | None = None
    official_invalid_distance_m: float | None = None
    official_invalid_speed_kmh: float | None = None
    sample_count: int = 0
    seen_brake: bool = False
    seen_throttle_after_brake: bool = False
    messages: list[str] = field(default_factory=list)


@dataclass
class ZoneSummary:
    name: str
    lap_count: int | None
    entry_lap_time_ms: int | None
    exit_lap_time_ms: int | None
    duration_ms: int | None
    entry_speed: float | None
    exit_speed: float | None
    min_speed: float | None
    max_speed: float | None
    max_brake: float
    first_brake_lap_time_ms: int | None
    first_brake_distance_m: float | None
    brake_release_lap_time_ms: int | None
    brake_release_distance_m: float | None
    trail_brake_duration_ms: int | None
    throttle_pickup_lap_time_ms: int | None
    throttle_pickup_distance_m: float | None
    max_abs_steer: float
    high_steer_throttle_samples: int
    brake_throttle_overlap_samples: int
    entry_damage_total: float | None
    max_damage_total: float | None
    max_damage_delta: float
    max_number_of_tyres_out: int
    max_abs_local_angular_vel: float
    max_wheel_slip: float
    official_invalid_triggered: bool
    official_invalid_lap_time_ms: int | None
    official_invalid_distance_m: float | None
    official_invalid_speed_kmh: float | None
    sample_count: int
    seen_brake: bool
    seen_throttle_after_brake: bool


@dataclass
class FocusIssue:
    zone_name: str
    reason_key: str
    severity: float
    lap_summary: str
    upcoming_hint: str
    detail: dict = field(default_factory=dict)


def first_not_none(values):
    return next((value for value in values if value is not None), None)


def min_not_none(values):
    values = [value for value in values if value is not None]
    return min(values) if values else None


def max_not_none(values):
    values = [value for value in values if value is not None]
    return max(values) if values else None


def consolidate_zone_records(records):
    by_name = {}
    order = []
    for record in records:
        if record.name not in by_name:
            by_name[record.name] = []
            order.append(record.name)
        by_name[record.name].append(record)

    consolidated = []
    for name in order:
        items = by_name[name]
        first = items[0]
        first_brake_lap_time_ms = min_not_none(item.first_brake_lap_time_ms for item in items)
        brake_release_lap_time_ms = max_not_none(item.brake_release_lap_time_ms for item in items)
        entry_damage_total = first_not_none(item.entry_damage_total for item in items)
        max_damage_total = max_not_none(item.max_damage_total for item in items)
        official_invalid_record = next((item for item in items if item.official_invalid_triggered), None)
        consolidated.append(
            ZoneSummary(
                name=name,
                lap_count=first.lap_count,
                entry_lap_time_ms=first_not_none(item.entry_lap_time_ms for item in items),
                exit_lap_time_ms=max_not_none(item.exit_lap_time_ms for item in items),
                duration_ms=sum(item.duration_ms or 0 for item in items) or None,
                entry_speed=first_not_none(item.entry_speed for item in items),
                exit_speed=first_not_none(reversed([item.exit_speed for item in items])),
                min_speed=min_not_none(item.min_speed for item in items),
                max_speed=max_not_none(item.max_speed for item in items),
                max_brake=max(item.max_brake for item in items),
                first_brake_lap_time_ms=first_brake_lap_time_ms,
                first_brake_distance_m=first_not_none(item.first_brake_distance_m for item in items),
                brake_release_lap_time_ms=brake_release_lap_time_ms,
                brake_release_distance_m=max_not_none(item.brake_release_distance_m for item in items),
                trail_brake_duration_ms=(
                    brake_release_lap_time_ms - first_brake_lap_time_ms
                    if first_brake_lap_time_ms is not None
                    and brake_release_lap_time_ms is not None
                    and brake_release_lap_time_ms >= first_brake_lap_time_ms
                    else None
                ),
                throttle_pickup_lap_time_ms=first_not_none(item.throttle_pickup_lap_time_ms for item in items),
                throttle_pickup_distance_m=first_not_none(item.throttle_pickup_distance_m for item in items),
                max_abs_steer=max(item.max_abs_steer for item in items),
                high_steer_throttle_samples=sum(item.high_steer_throttle_samples for item in items),
                brake_throttle_overlap_samples=sum(item.brake_throttle_overlap_samples for item in items),
                entry_damage_total=entry_damage_total,
                max_damage_total=max_damage_total,
                max_damage_delta=max(
                    max(item.max_damage_delta for item in items),
                    (max_damage_total or 0.0) - (entry_damage_total or 0.0)
                    if entry_damage_total is not None and max_damage_total is not None
                    else 0.0,
                ),
                max_number_of_tyres_out=max(item.max_number_of_tyres_out for item in items),
                max_abs_local_angular_vel=max(item.max_abs_local_angular_vel for item in items),
                max_wheel_slip=max(item.max_wheel_slip for item in items),
                official_invalid_triggered=official_invalid_record is not None,
                official_invalid_lap_time_ms=official_invalid_record.official_invalid_lap_time_ms if official_invalid_record else None,
                official_invalid_distance_m=official_invalid_record.official_invalid_distance_m if official_invalid_record else None,
                official_invalid_speed_kmh=official_invalid_record.official_invalid_speed_kmh if official_invalid_record else None,
                sample_count=sum(item.sample_count for item in items),
                seen_brake=any(item.seen_brake for item in items),
                seen_throttle_after_brake=any(item.seen_throttle_after_brake for item in items),
            )
        )
    return consolidated


class RealtimeCoach:
    def __init__(
        self,
        track_map,
        world_gates=None,
        world_path=None,
        min_message_gap=2.5,
        verbose_events=False,
        position_offset_lap=0.0,
        min_prompt_speed_kmh=35.0,
        pro_video_reference=None,
    ):
        self.track_map = track_map
        self.world_gates = world_gates or []
        self.world_path = world_path
        self.lap_length_m = track_map.get("lap_length_m", 7004)
        self.current_zone = None
        self.zone_state = None
        self.last_message_at = 0.0
        self.min_message_gap = min_message_gap
        self.verbose_events = verbose_events
        self.position_offset_lap = position_offset_lap
        self.min_prompt_speed_kmh = min_prompt_speed_kmh
        self.pro_video_reference = pro_video_reference
        self.last_packet_lap_count = None
        self.last_normalized_position = None
        self.session_lap_number = 1
        self.has_seen_start_finish = False
        self.completed_zone_summaries = []
        self.completed_zone_records = []
        self.current_lap_zone_records = []
        self.zone_history = {}
        self.active_focus_issue = None
        self.current_lap_official_invalid = False
        self.current_lap_validity_seen = False
        self.previous_lap_validity_state = None
        self.generic_gate_counts = {}
        self.issue_phrase_counts = {}
        self.session_issue_counts = {}
        self.historical_issue_counts = {}
        self.historical_issue_stats = {}
        self.profile_loaded_for = None
        self.decision_history = []
        self.intro_spoken = False
        self.last_entry_message_by_zone = {}
        self.zone_entry_cooldown_seconds = 20.0
        self.last_trigger_position = None
        self.triggered_locations_this_lap = set()
        self.last_gate_distances = {}
        self.last_world_progress = None
        self.last_world_point_index = None
        self.next_world_gate_index = None
        self.zero_physics_warning_sent = False
        self.zero_physics_samples = 0
        self.last_physics_packet_id = None
        self.last_graphics_packet_id = None
        self.corner_triggers = [
            {
                "key": "t1",
                "position": 0.985,
                "message": "Approaching Turn 1 La Source: brake in one firm hit, release as you turn, delay full throttle until the car points toward exit.",
            },
            {
                "key": "t3",
                "position": 0.105,
                "message": "Approaching Turn 3 Eau Rouge: after Turn 2, settle the car, hold one steering angle, avoid a panic lift.",
            },
            {
                "key": "t5",
                "position": 0.235,
                "message": "Approaching Turn 5, then Turn 6 and Turn 7 Les Combes/Malmedy: brake once at the end of Kemmel, release before turn-in, use the first right-hand apex to set up the left.",
            },
            {
                "key": "t8",
                "position": 0.365,
                "message": "Approaching Turn 8, then Turn 9 Bruxelles: keep light brake pressure past turn-in, then wait for rotation before throttle.",
            },
            {
                "key": "t10",
                "position": 0.465,
                "message": "Approaching Turn 10, then Turn 11 No Name: finish the small lift early, then commit with one smooth steering input.",
            },
            {
                "key": "t12",
                "position": 0.545,
                "message": "Approaching Turn 12, then Turn 13 Pouhon/Fagnes: keep the first left flowing; do not add throttle if steering is still increasing.",
            },
            {
                "key": "t14",
                "position": 0.650,
                "message": "Approaching Turn 14, then Turn 15 Campus/Stavelot: sacrifice entry if needed, straighten early, then go full throttle for the long exit.",
            },
            {
                "key": "t18",
                "position": 0.870,
                "message": "Approaching Turn 18, then Turn 19 Bus Stop: brake hard while straight, release for rotation, prioritize second apex exit.",
            },
        ]
        self.gate_zone_names = {
            "t1": "La Source",
            "t3": "Eau Rouge/Raidillon/Kemmel",
            "t5": "Les Combes/Malmedy",
            "t8": "Bruxelles",
            "t10": "No Name/Pouhon Entry",
            "t12": "Pouhon/Fagnes",
            "t14": "Campus/Stavelot",
            "t18": "Bus Stop",
        }

    def zone_label(self, zone_name):
        labels = {
            "La Source": "T1 La Source",
            "Eau Rouge/Raidillon/Kemmel": "T2, T3, T4 Eau Rouge/Raidillon, Kemmel",
            "Les Combes/Malmedy": "T5, T6, T7 Les Combes/Malmedy",
            "Bruxelles": "T8, T9 Bruxelles",
            "No Name/Pouhon Entry": "T10, T11 No Name/Pouhon entry",
            "Pouhon/Fagnes": "T12, T13 Pouhon/Fagnes",
            "Campus/Stavelot": "T14, T15 Campus/Stavelot",
            "Blanchimont": "T16, T17 Blanchimont",
            "Bus Stop": "T18, T19 Bus Stop chicane",
        }
        return labels.get(zone_name, zone_name or "Unknown zone")

    def maybe_message(self, message, force=False):
        now = time.time()
        if not force and now - self.last_message_at < self.min_message_gap:
            return None
        self.last_message_at = now
        return message

    def weather_text(self, row):
        air = to_float(row.get("air_temp_c"))
        road = to_float(row.get("road_temp_c"))
        if air == 0 and road == 0:
            return None
        if air is not None and road is not None and 1 <= air <= 70 and 1 <= road <= 90:
            return f"Today's weather is air {air:.0f} degrees, track {road:.0f} degrees."
        if air is not None and 1 <= air <= 70:
            return f"Today's weather is air {air:.0f} degrees. Track temperature is not available yet."
        return None

    def intro_message(self, row, speed):
        if self.intro_spoken:
            return None
        self.intro_spoken = True
        driver = str(row.get("player_name") or "").strip() or "driver"
        weather = self.weather_text(row)
        weather_part = f" {weather}" if weather else ""
        return f"Hi, I am your private driving coach Rachel. It is nice to meet you {driver}.{weather_part} Start driving when you are ready."

    def should_print_zone_entry(self, zone_name):
        if zone_name not in {"La Source", "Les Combes/Malmedy", "Bruxelles", "Campus/Stavelot", "Bus Stop"}:
            return False
        now = time.time()
        last = self.last_entry_message_by_zone.get(zone_name, 0.0)
        if now - last < self.zone_entry_cooldown_seconds:
            return False
        self.last_entry_message_by_zone[zone_name] = now
        return True

    def crossed_trigger(self, previous, current, trigger):
        if previous is None or current is None:
            return False
        if current >= previous:
            return previous < trigger <= current
        return trigger > previous or trigger <= current

    def world_path_index_progress(self, previous_index, current_index):
        if previous_index is None or current_index is None or not self.world_path:
            return None
        point_count = len(self.world_path.get("points", []))
        if point_count <= 0:
            return None
        return (current_index - previous_index) % point_count

    def localize_world_progress(self, row):
        position = world_position(row)
        if position is None or not self.world_path:
            return None

        x, z = position
        points = sorted(self.world_path.get("points", []), key=lambda point: point["progress"])
        if not points:
            return None

        if self.last_world_point_index is None:
            nearest_index, nearest = min(
                enumerate(points),
                key=lambda item: math.hypot(x - item[1]["x"], z - item[1]["z"]),
            )
            self.last_world_point_index = nearest_index
            self.last_world_progress = nearest["progress"]
            return nearest["progress"]

        point_count = len(points)
        # Spa has physically close but unrelated track sections. Search only a
        # short ordered window around the last known path index so localization
        # follows the lap instead of snapping to a nearby future corner.
        candidate_offsets = range(-3, 18)
        candidates = [
            ((self.last_world_point_index + offset) % point_count, offset)
            for offset in candidate_offsets
        ]
        nearest_index, nearest_offset = min(
            candidates,
            key=lambda item: math.hypot(x - points[item[0]]["x"], z - points[item[0]]["z"]),
        )

        if nearest_offset < 0:
            nearest_index = self.last_world_point_index

        nearest = points[nearest_index]
        self.last_world_progress = nearest["progress"]
        self.last_world_point_index = nearest_index
        return nearest["progress"]

    def location_trigger_messages(self, position, moving_fast_enough):
        if position is None:
            return []

        if self.last_trigger_position is not None and self.last_trigger_position > 0.85 and position < 0.15:
            self.triggered_locations_this_lap.clear()

        messages = []
        if moving_fast_enough:
            for trigger in self.corner_triggers:
                if trigger["key"] in self.triggered_locations_this_lap:
                    continue
                if self.crossed_trigger(self.last_trigger_position, position, trigger["position"]):
                    self.triggered_locations_this_lap.add(trigger["key"])
                    message = self.maybe_message(trigger["message"], force=True)
                    if message:
                        messages.append(message)

        self.last_trigger_position = position
        return sanitize_coach_messages(messages)

    def world_gate_messages(self, row, moving_fast_enough, allow_prompts=True):
        progress = self.localize_world_progress(row)
        if progress is None or not moving_fast_enough:
            return []

        gates = sorted(
            (self.world_path or {}).get("gates", []) or self.world_gates,
            key=lambda gate: gate["progress"],
        )
        if not gates:
            return []

        if self.next_world_gate_index is None:
            self.next_world_gate_index = next(
                (index for index, gate in enumerate(gates) if gate["progress"] > progress),
                0,
            )
            self.last_trigger_position = progress
            return []

        messages = []
        gate = gates[self.next_world_gate_index]
        if self.crossed_trigger(self.last_trigger_position, progress, gate["progress"]):
            if allow_prompts:
                message_text = self.message_for_gate(gate)
                message = self.maybe_message(message_text, force=True) if message_text else None
                if message:
                    messages.append(message)
            self.next_world_gate_index = (self.next_world_gate_index + 1) % len(gates)

        self.last_trigger_position = progress
        return sanitize_coach_messages(messages)

    def message_for_gate(self, gate):
        key = gate.get("key")
        zone_name = self.gate_zone_names.get(key)
        if self.active_focus_issue and self.active_focus_issue.zone_name == zone_name:
            issue = self.active_focus_issue
            self.active_focus_issue = None
            return self.active_focus_issue_text(issue, gate)

        return None

    def active_focus_issue_text(self, issue, gate):
        if issue is None:
            return gate.get("message")
        return f"For the upcoming corner, {issue.upcoming_hint}"

    def short_gate_reminder(self, gate):
        labels = {
            "t1": "Turn 1: clean brake release, then exit.",
            "t3": "Turn 3: one smooth commitment through Eau Rouge.",
            "t5": "Turn 5: brake once, then flow through Turns 6 and 7.",
            "t8": "Turn 8: trail brake lightly and wait for rotation.",
            "t10": "Turn 10: finish the lift early, then commit.",
            "t12": "Turn 12: keep Pouhon smooth; no throttle against rising steering.",
            "t14": "Turn 14: protect the exit for the long run.",
            "t18": "Turn 18: brake straight, rotate, second apex exit.",
        }
        return labels.get(gate.get("key"))

    def zone_entry_message(self, zone_name):
        label = self.zone_label(zone_name)
        if zone_name == "La Source":
            return f"Approaching {label}: brake in one firm hit, release as you turn, delay full throttle until the car points toward exit."
        if zone_name == "Eau Rouge/Raidillon/Kemmel":
            return f"{label}: hold a stable steering angle; avoid lifting unless the car is unsettled."
        if zone_name == "Les Combes/Malmedy":
            return f"Approaching {label}: brake once at the end of Kemmel, release before turn-in, use the first right-hand apex to set up the left."
        if zone_name == "Bruxelles":
            return f"{label}: keep light brake pressure past turn-in, then wait for rotation before throttle."
        if zone_name == "No Name/Pouhon Entry":
            return f"{label}: finish the small lift early, then commit with one smooth steering input."
        if zone_name == "Pouhon/Fagnes":
            return f"{label}: keep the first left flowing; do not add throttle if steering is still increasing."
        if zone_name == "Campus/Stavelot":
            return f"{label}: sacrifice entry if needed, straighten early, then go full throttle for the long exit."
        if zone_name == "Blanchimont":
            return f"{label}: stay flat only if stable; move eyes to the Bus Stop braking reference early."
        if zone_name == "Bus Stop":
            return f"Approaching {label}: brake hard while straight, release for rotation, prioritize second apex exit."
        return f"Entering {label}."

    def focus_instruction(self, record, reason_key):
        label = self.zone_label(record.name)
        if reason_key == "throttle_pickup":
            instructions = {
                "La Source": "wait longer before full throttle; only commit when the wheel starts opening.",
                "Les Combes/Malmedy": "release brake earlier before turn-in, then pick up throttle after the left-right transition.",
                "Bruxelles": "hold light trail brake longer, then roll into throttle after the car rotates.",
                "No Name/Pouhon Entry": "finish the lift/brake earlier and make one clean throttle pickup before Pouhon.",
                "Pouhon/Fagnes": "avoid early throttle while steering load is high; add throttle only after the car settles.",
                "Campus/Stavelot": "straighten the car earlier before full throttle so the exit is cleaner.",
                "Bus Stop": "delay throttle until the second apex; prioritize exit over the first curb.",
            }
            return f"{label}: {instructions.get(record.name, 'make one clean throttle pickup after brake release.')}"

        if reason_key == "min_speed":
            instructions = {
                "La Source": "release brake slightly smoother and carry a little more speed to apex.",
                "Les Combes/Malmedy": "avoid over-slowing the first right; keep enough speed to flow into the left.",
                "Bruxelles": "do not dump speed at entry; use gentle trail brake to hold rotation.",
                "Pouhon/Fagnes": "commit to the first left with smoother steering and less mid-corner correction.",
                "Campus/Stavelot": "do not over-slow entry; the goal is a straighter, faster exit.",
                "Bus Stop": "brake hard, but release cleanly so the car rotates instead of stopping.",
            }
            return f"{label}: {instructions.get(record.name, 'carry slightly more minimum speed without forcing throttle.')}"

        return f"{label}: repeat the cleanest part of the previous lap."

    def zone_best_record(self, zone_name):
        records = self.zone_history.get(zone_name, [])
        if not records:
            return None
        timed = [record for record in records if record.duration_ms is not None]
        if timed:
            return min(timed, key=lambda record: record.duration_ms)
        speed_records = [record for record in records if record.min_speed is not None]
        if speed_records:
            return max(speed_records, key=lambda record: record.min_speed)
        return records[-1]

    def varied_issue_text(self, zone_name, reason_key, options):
        key = (zone_name, reason_key)
        count = self.issue_phrase_counts.get(key, 0)
        self.issue_phrase_counts[key] = count + 1
        return options[count % len(options)]

    def zone_time_loss_seconds(self, record, best):
        if record.duration_ms is None or best is None or best.duration_ms is None:
            return None
        delta_ms = record.duration_ms - best.duration_ms
        if delta_ms < 120:
            return None
        return round(delta_ms / 1000, 2)

    def detail_deltas(self, record, best):
        if best is None:
            return {}
        details = {}
        if record.first_brake_distance_m is not None and best.first_brake_distance_m is not None:
            details["brake_early_m"] = round(best.first_brake_distance_m - record.first_brake_distance_m, 1)
        if record.trail_brake_duration_ms is not None and best.trail_brake_duration_ms is not None:
            details["trail_short_ms"] = best.trail_brake_duration_ms - record.trail_brake_duration_ms
            details["trail_long_ms"] = record.trail_brake_duration_ms - best.trail_brake_duration_ms
        if record.throttle_pickup_distance_m is not None and best.throttle_pickup_distance_m is not None:
            details["throttle_late_m"] = round(record.throttle_pickup_distance_m - best.throttle_pickup_distance_m, 1)
        if record.min_speed is not None and best.min_speed is not None:
            details["min_speed_loss_kmh"] = round(best.min_speed - record.min_speed, 1)
        details["time_loss_s"] = self.zone_time_loss_seconds(record, best)
        return details

    def detailed_reference_summary(self, record, best, primary_reason):
        label = self.zone_label(record.name)
        details = self.detail_deltas(record, best)
        time_loss = details.get("time_loss_s")
        lead = (
            f"You lost approximately {time_loss:.2f} s through {label}."
            if time_loss is not None
            else f"Through {label}, the main loss was in rotation or exit quality."
        )

        causes = []
        brake_early = details.get("brake_early_m")
        trail_short = details.get("trail_short_ms")
        trail_long = details.get("trail_long_ms")
        throttle_late = details.get("throttle_late_m")
        min_speed_loss = details.get("min_speed_loss_kmh")

        if brake_early is not None and brake_early >= 25:
            causes.append(f"you began braking about {brake_early:.0f} m too early")
        if trail_short is not None and trail_short >= 250:
            causes.append(f"you released the brake about {trail_short / 1000:.1f} s sooner than your best pass")
        if trail_long is not None and trail_long >= 550:
            causes.append(f"you stayed on the brake about {trail_long / 1000:.1f} s too long")
        if throttle_late is not None and throttle_late >= 25:
            causes.append(f"you picked up throttle about {throttle_late:.0f} m late")
        elif throttle_late is not None and throttle_late <= -25:
            causes.append(f"you added throttle about {abs(throttle_late):.0f} m earlier while the car still needed rotation")
        if min_speed_loss is not None and min_speed_loss >= 5:
            causes.append(f"minimum speed was about {min_speed_loss:.0f} km/h lower")
        if record.high_steer_throttle_samples >= 8:
            causes.append("you added throttle while steering load was still high")
        if record.brake_throttle_overlap_samples >= 8:
            causes.append("you overlapped brake and throttle")

        if not causes:
            causes.append("the telemetry points to weaker rotation and exit commitment than your best pass")

        if len(causes) == 1:
            cause_text = f"You were slow because {causes[0]}."
        elif len(causes) == 2:
            cause_text = f"You were slow because {causes[0]} and {causes[1]}."
        else:
            cause_text = f"You were slow because {', '.join(causes[:-1])}, and {causes[-1]}."

        correction = self.detailed_reference_correction(record, details, primary_reason)
        return f"{lead} {cause_text} {correction}", details

    def detailed_reference_correction(self, record, details, primary_reason):
        brake_early = details.get("brake_early_m")
        trail_short = details.get("trail_short_ms")
        trail_long = details.get("trail_long_ms")
        throttle_late = details.get("throttle_late_m")

        if brake_early is not None and brake_early <= -18 and trail_long is not None and trail_long >= 550:
            earlier = max(4, min(14, round(abs(brake_early) * 0.25)))
            return f"Brake {earlier}-{earlier + 4} m earlier and straighter, then release sooner so the car can rotate before throttle."
        if brake_early is not None and brake_early <= -18:
            earlier = max(4, min(14, round(abs(brake_early) * 0.25)))
            return f"Move the brake point {earlier}-{earlier + 4} m earlier and keep the car settled before turn-in."
        if brake_early is not None and brake_early >= 25 and trail_short is not None and trail_short >= 250:
            later = max(6, min(18, round(brake_early * 0.25)))
            return f"Try moving the brake point {later}-{later + 4} m later and trail-braking into the apex instead of releasing abruptly."
        if brake_early is not None and brake_early >= 25:
            later = max(6, min(18, round(brake_early * 0.25)))
            return f"Try moving the brake point {later}-{later + 4} m later, but keep the release smooth."
        if trail_short is not None and trail_short >= 250:
            return f"Keep light brake pressure about {trail_short / 1000:.1f} s longer toward turn-in so the car rotates."
        if throttle_late is not None and throttle_late >= 25:
            earlier = max(6, min(18, round(throttle_late * 0.25)))
            return f"Once the car rotates, start the throttle build {earlier}-{earlier + 4} m earlier."
        if primary_reason in {"understeer", "early_throttle"} or (throttle_late is not None and throttle_late <= -25):
            return "Delay throttle until the steering starts to unwind, then build power progressively."
        if primary_reason == "low_min_speed":
            return "Carry a little more entry speed only if the car is rotating; do not solve it with early throttle."
        return "Repeat the corner with one clean brake release, one rotation phase, and one throttle build."

    def pro_zone_metrics(self, zone_name):
        if not self.pro_video_reference:
            return None
        zones = ((self.pro_video_reference.get("lap_profile") or {}).get("zones") or {})
        return zones.get(zone_name)

    def pro_video_issue(self, record):
        pro = self.pro_zone_metrics(record.name)
        if not pro or (pro.get("input_confidence") or 0) < 0.10:
            return None
        label = self.zone_label(record.name)
        causes = []
        details = {}
        estimated_time_loss = None
        pro_duration_available = record.duration_ms is not None and pro.get("duration_ms") is not None
        if record.duration_ms is not None and pro.get("duration_ms") is not None:
            estimated_time_loss = (record.duration_ms - pro["duration_ms"]) / 1000
            details["time_loss_s"] = round(estimated_time_loss, 2)
            details["user_duration_ms"] = record.duration_ms
            details["pro_duration_ms"] = pro["duration_ms"]
        if record.entry_speed is not None:
            details["user_entry_speed_kmh"] = round(record.entry_speed, 1)
        if record.exit_speed is not None:
            details["user_exit_speed_kmh"] = round(record.exit_speed, 1)
        if record.min_speed is not None:
            details["user_min_speed_kmh"] = round(record.min_speed, 1)
        if pro.get("entry_time_ms") is not None:
            details["pro_entry_time_ms"] = pro.get("entry_time_ms")
        if pro.get("exit_time_ms") is not None:
            details["pro_exit_time_ms"] = pro.get("exit_time_ms")
        if pro.get("estimated_entry_speed_kmh") is not None:
            details["pro_entry_speed_kmh"] = pro.get("estimated_entry_speed_kmh")
        if pro.get("estimated_exit_speed_kmh") is not None:
            details["pro_exit_speed_kmh"] = pro.get("estimated_exit_speed_kmh")
        if pro.get("estimated_min_speed_kmh") is not None:
            details["pro_min_speed_kmh"] = pro.get("estimated_min_speed_kmh")
        if pro.get("estimated_entry_gear") is not None:
            details["pro_entry_gear"] = pro.get("estimated_entry_gear")
        if pro.get("estimated_exit_gear") is not None:
            details["pro_exit_gear"] = pro.get("estimated_exit_gear")

        if record.first_brake_distance_m is not None and pro.get("brake_start_distance_m") is not None:
            brake_early = pro["brake_start_distance_m"] - record.first_brake_distance_m
            details["brake_early_m"] = round(brake_early, 1)
            if brake_early >= 12:
                causes.append(f"you began braking about {brake_early:.0f} m earlier than the pro reference")
            elif brake_early <= -18:
                causes.append(f"you began braking about {abs(brake_early):.0f} m later than the pro reference")

        if record.trail_brake_duration_ms is not None and pro.get("trail_brake_duration_ms") is not None:
            trail_short = pro["trail_brake_duration_ms"] - record.trail_brake_duration_ms
            details["trail_short_ms"] = trail_short
            if trail_short >= 250:
                causes.append(f"you released the brake about {trail_short / 1000:.1f} s sooner")
            elif trail_short <= -550:
                details["trail_long_ms"] = abs(trail_short)
                causes.append(f"you stayed on the brake about {abs(trail_short) / 1000:.1f} s longer")

        if record.throttle_pickup_distance_m is not None and pro.get("throttle_pickup_distance_m") is not None:
            throttle_late = record.throttle_pickup_distance_m - pro["throttle_pickup_distance_m"]
            details["throttle_late_m"] = round(throttle_late, 1)
            if throttle_late >= 12:
                causes.append(f"you picked up throttle about {throttle_late:.0f} m later")
            elif throttle_late <= -18 and record.max_abs_steer >= 0.35:
                causes.append(f"you added throttle about {abs(throttle_late):.0f} m earlier while the steering was still loaded")

        if record.min_speed is not None and pro.get("estimated_min_speed_kmh") is not None:
            min_speed_loss = pro["estimated_min_speed_kmh"] - record.min_speed
            details["pro_min_speed_loss_kmh"] = round(min_speed_loss, 1)
            if min_speed_loss >= 5:
                causes.append(f"minimum speed was about {min_speed_loss:.0f} km/h lower than the pro reference")

        if not causes:
            if estimated_time_loss is None or estimated_time_loss < 0.25:
                return None
            causes.append("your section time was slower even though the main input trigger is not clear yet")

        if pro_duration_available and estimated_time_loss is not None and estimated_time_loss < 0.12:
            return None

        time_loss = estimated_time_loss
        if time_loss is None or time_loss < 0.12:
            time_loss = self.zone_time_loss_seconds(record, self.zone_best_record(record.name))
        if time_loss is None or time_loss < 0.12:
            time_loss = min(0.95, max(0.18, len(causes) * 0.18))
        if len(causes) == 1:
            cause_text = causes[0]
        elif len(causes) == 2:
            cause_text = f"{causes[0]} and {causes[1]}"
        else:
            cause_text = f"{', '.join(causes[:-1])}, and {causes[-1]}"

        correction = self.detailed_reference_correction(record, details, "pro_video")
        extra_context = []
        if record.entry_speed is not None and record.exit_speed is not None:
            extra_context.append(f"Your entry-to-exit speed was about {record.entry_speed:.0f} to {record.exit_speed:.0f} km/h.")
        if pro.get("estimated_entry_speed_kmh") is None or pro.get("estimated_entry_gear") is None:
            extra_context.append("Pro speed and gear OCR are not reliable yet, so this comparison uses pro timing and input traces.")
        context_text = (" " + " ".join(extra_context)) if extra_context else ""
        return FocusIssue(
            zone_name=record.name,
            reason_key="pro_video_delta",
            severity=20.0 + min(55.0, time_loss * 18.0) + min(6.0, len(causes) * 0.8),
            lap_summary=(
                f"You lost approximately {time_loss:.2f} s through {label} compared to the pro video reference. "
                f"You were slow because {cause_text}.{context_text} {correction}"
            ),
            upcoming_hint=f"{label}: {correction}",
            detail={**details, "pro_reference_confidence": pro.get("input_confidence"), "source": "pro_video_reference"},
        )

    def evaluate_record_issue(self, record):
        label = self.zone_label(record.name)
        best = self.zone_best_record(record.name)
        issues = []

        if record.sample_count < 20:
            return None

        incident_issue = self.major_incident_issue(record)
        if incident_issue is not None:
            return incident_issue

        if self.pro_video_reference:
            return self.pro_video_issue(record)

        if record.high_steer_throttle_samples >= 8:
            if best is not None:
                lap_text, detail = self.detailed_reference_summary(record, best, "understeer")
            else:
                detail = {}
                lap_text = self.varied_issue_text(
                    record.name,
                    "understeer",
                    [
                        f"Through {label}, the main loss was exit rotation. You opened throttle while the steering angle was still high, so the front likely washed wide. Next lap, wait for the apex/rotation first, then add throttle as the wheel opens.",
                        f"Through {label}, you likely lost time because throttle came in before the car finished rotating. Hold the throttle slightly longer, let the front bite, then build power progressively.",
                        f"Through {label}, the car was still loaded in steering when you added gas. That usually creates understeer. Delay throttle until the steering starts to unwind.",
                    ],
                )
            issues.append(
                FocusIssue(
                    zone_name=record.name,
                    reason_key="understeer",
                    severity=4.0 + min(3.0, record.high_steer_throttle_samples / 12),
                    lap_summary=lap_text,
                    upcoming_hint=f"{label}: wait for rotation first. Add throttle only when the wheel is opening.",
                    detail=detail,
                )
            )

        if record.seen_brake and not record.seen_throttle_after_brake:
            lap_text = self.varied_issue_text(
                record.name,
                "no_throttle_pickup",
                [
                    f"Through {label}, you likely lost exit time because there was no clean throttle pickup after braking. Once the car rotates, commit to one smooth throttle build.",
                    f"Through {label}, throttle did not come back clearly after the brake phase, so the exit was probably too passive. Pick up throttle earlier once steering load drops.",
                    f"Through {label}, the brake phase ended without a decisive throttle build. You likely gave up exit speed; look for one clean release, then one progressive throttle application.",
                ],
            )
            issues.append(
                FocusIssue(
                    zone_name=record.name,
                    reason_key="no_throttle_pickup",
                    severity=5.5,
                    lap_summary=lap_text,
                    upcoming_hint=self.focus_instruction(record, "throttle_pickup"),
                )
            )

        if record.brake_throttle_overlap_samples >= 8:
            lap_text = self.varied_issue_text(
                record.name,
                "pedal_overlap",
                [
                    f"Through {label}, you likely lost time because brake and throttle overlapped. That keeps the car loaded and can block rotation. Finish brake release before building throttle.",
                    f"Through {label}, you carried throttle against brake too often, which usually wastes rotation and exit speed. Separate the two inputs more clearly.",
                    f"Through {label}, pedal overlap was high. Release brake smoothly first, let the car rotate, then start throttle pickup.",
                ],
            )
            issues.append(
                FocusIssue(
                    zone_name=record.name,
                    reason_key="pedal_overlap",
                    severity=3.8 + min(2.5, record.brake_throttle_overlap_samples / 12),
                    lap_summary=lap_text,
                    upcoming_hint=f"{label}: separate the pedals. Finish brake release before building throttle.",
                )
            )

        if best is not None:
            if (
                record.min_speed is not None
                and best.min_speed is not None
                and best.min_speed - record.min_speed >= 8
            ):
                delta = best.min_speed - record.min_speed
                lap_text, detail = self.detailed_reference_summary(record, best, "low_min_speed")
                issues.append(
                    FocusIssue(
                        zone_name=record.name,
                        reason_key="low_min_speed",
                        severity=3.0 + min(3.0, delta / 8),
                        lap_summary=lap_text,
                        upcoming_hint=self.focus_instruction(record, "min_speed"),
                        detail=detail,
                    )
                )

            if (
                record.first_brake_distance_m is not None
                and best.first_brake_distance_m is not None
                and best.first_brake_distance_m - record.first_brake_distance_m >= 35
            ):
                delta = best.first_brake_distance_m - record.first_brake_distance_m
                lap_text, detail = self.detailed_reference_summary(record, best, "braked_early")
                issues.append(
                    FocusIssue(
                        zone_name=record.name,
                        reason_key="braked_early",
                        severity=3.5 + min(3.0, delta / 35),
                        lap_summary=lap_text,
                        upcoming_hint=f"{label}: look farther ahead and move the brake point slightly later, but keep the same release shape.",
                        detail=detail,
                    )
                )

            if (
                record.throttle_pickup_distance_m is not None
                and best.throttle_pickup_distance_m is not None
                and record.throttle_pickup_distance_m - best.throttle_pickup_distance_m >= 35
            ):
                delta = record.throttle_pickup_distance_m - best.throttle_pickup_distance_m
                lap_text, detail = self.detailed_reference_summary(record, best, "late_throttle")
                issues.append(
                    FocusIssue(
                        zone_name=record.name,
                        reason_key="late_throttle",
                        severity=3.5 + min(3.0, delta / 35),
                        lap_summary=lap_text,
                        upcoming_hint=f"{label}: rotate first, then start throttle earlier and build it smoothly.",
                        detail=detail,
                    )
                )

            if (
                record.trail_brake_duration_ms is not None
                and best.trail_brake_duration_ms is not None
                and best.trail_brake_duration_ms - record.trail_brake_duration_ms >= 300
            ):
                delta = best.trail_brake_duration_ms - record.trail_brake_duration_ms
                lap_text, detail = self.detailed_reference_summary(record, best, "less_trail_brake")
                issues.append(
                    FocusIssue(
                        zone_name=record.name,
                        reason_key="less_trail_brake",
                        severity=3.8 + min(3.0, delta / 350),
                        lap_summary=lap_text,
                        upcoming_hint=f"{label}: keep a small amount of brake pressure into turn-in, then release smoothly as the car rotates.",
                        detail=detail,
                    )
                )

            if (
                record.trail_brake_duration_ms is not None
                and best.trail_brake_duration_ms is not None
                and record.trail_brake_duration_ms - best.trail_brake_duration_ms >= 550
            ):
                delta = record.trail_brake_duration_ms - best.trail_brake_duration_ms
                lap_text, detail = self.detailed_reference_summary(record, best, "longer_brake")
                issues.append(
                    FocusIssue(
                        zone_name=record.name,
                        reason_key="longer_brake",
                        severity=3.5 + min(3.0, delta / 550),
                        lap_summary=lap_text,
                        upcoming_hint=f"{label}: release the brake earlier and let the car roll to the apex before throttle.",
                        detail=detail,
                    )
                )

        return max(issues, key=lambda issue: issue.severity, default=None)

    def incident_evidence(self, record):
        if record is None:
            return []
        evidence = []
        if record.max_damage_delta >= 0.5:
            evidence.append(f"damage increased by {record.max_damage_delta:.1f}")
        if record.max_number_of_tyres_out >= 3:
            evidence.append(f"{record.max_number_of_tyres_out} tyres were reported out")
        if record.max_abs_local_angular_vel >= 2.0:
            evidence.append(f"rotation rate spiked to {record.max_abs_local_angular_vel:.1f}")
        if record.max_wheel_slip >= 8.0:
            evidence.append(f"wheel slip peaked at {record.max_wheel_slip:.1f}")
        return evidence

    def has_direct_incident_evidence(self, record):
        return bool(self.incident_evidence(record))

    def classify_incident_cause(self, record, fallback):
        evidence = self.incident_evidence(record)
        if record.max_damage_delta >= 0.5:
            cause = "likely wall contact or car-to-barrier contact"
        elif record.max_number_of_tyres_out >= 3:
            cause = "likely off-track or track-limits event"
        elif record.max_abs_local_angular_vel >= 2.0:
            cause = "likely spin or big recovery"
        elif record.max_wheel_slip >= 8.0 and record.max_abs_steer >= 0.55:
            cause = "likely slide from excessive slip while steering"
        else:
            cause = fallback
        if evidence:
            return f"{cause}; evidence: {', '.join(evidence)}"
        if "telemetry cannot confirm" not in cause:
            return f"{cause}; no direct damage, tyres-out, or spin evidence was captured"
        return cause

    def major_incident_issue(self, record):
        if record.min_speed is None:
            return None

        label = self.zone_label(record.name)
        max_speed = record.max_speed or 0.0
        if record.official_invalid_triggered:
            speed_text = (
                f" at {record.official_invalid_speed_kmh:.0f} km/h"
                if record.official_invalid_speed_kmh is not None
                else ""
            )
            time_text = (
                f" around {fmt_lap_time(record.official_invalid_lap_time_ms)}"
                if record.official_invalid_lap_time_ms is not None
                else ""
            )
            evidence = self.incident_evidence(record)
            evidence_text = f" Extra evidence: {', '.join(evidence)}." if evidence else ""
            return FocusIssue(
                zone_name=record.name,
                reason_key="official_invalid_trigger",
                severity=110,
                lap_summary=(
                    f"{label}: ACC invalidated the lap here{time_text}{speed_text}. "
                    f"Treat this as the main lap-losing event before smaller driving details.{evidence_text}"
                ),
                upcoming_hint=(
                    f"{label}: leave a small safety margin and keep the car fully inside track limits before pushing again."
                ),
            )
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

        threshold = high_speed_minimums.get(record.name)
        if threshold is not None and record.min_speed < threshold:
            if record.min_speed < 15 and max_speed >= threshold:
                if record.high_steer_throttle_samples >= 8:
                    fallback = "likely ran wide or went off track while adding throttle with steering still loaded"
                elif record.max_abs_steer >= 0.70:
                    fallback = "likely spin or off-track recovery"
                else:
                    fallback = "major stop or recovery; telemetry cannot confirm whether it was off track, a spin, or wall contact"
                cause = self.classify_incident_cause(record, fallback)
                summary = self.varied_issue_text(
                    record.name,
                    "major_high_speed_stop",
                    [
                        f"{label}: {cause}. Speed fell from high-speed running to {record.min_speed:.0f} km/h, so treat that lap as invalid first.",
                        f"{label}: big abnormal speed drop detected. The likely cause is: {cause}. This should override small coaching details.",
                        f"{label}: this was not a normal cornering loss. Speed dropped to {record.min_speed:.0f} km/h; {cause}.",
                    ],
                )
                hint = (
                    f"{label}: prioritize staying inside track limits. Make the steering input smoother and lift before the car washes wide."
                )
            else:
                summary = (
                    f"{label}: major speed loss detected. Minimum speed dropped to {record.min_speed:.0f} km/h in a section that should stay fast."
                )
                hint = (
                    f"{label}: reset the priority. Keep the car stable first; lift if needed, avoid adding throttle while the car is washing wide."
                )
            return FocusIssue(
                zone_name=record.name,
                reason_key="major_speed_loss",
                severity=100 + (threshold - record.min_speed),
                lap_summary=summary,
                upcoming_hint=hint,
            )

        threshold = braking_zone_minimums.get(record.name)
        if threshold is not None and record.min_speed < threshold and record.max_abs_steer >= 0.55:
            cause = self.classify_incident_cause(record, "major corner push or recovery")
            return FocusIssue(
                zone_name=record.name,
                reason_key="major_corner_push",
                severity=80 + (threshold - record.min_speed),
                lap_summary=(
                    f"{label}: {cause}. Minimum speed fell to {record.min_speed:.0f} km/h with high steering input."
                ),
                upcoming_hint=(
                    f"{label}: brake earlier and straighter this lap, then release once and rotate before throttle."
                ),
            )

        if record.min_speed < 8 and max_speed >= 80:
            if record.max_abs_steer >= 0.70:
                fallback = "likely spin or recovery"
            else:
                fallback = "major stop or recovery; current telemetry cannot confirm wall contact versus off-track"
            cause = self.classify_incident_cause(record, fallback)
            return FocusIssue(
                zone_name=record.name,
                reason_key="major_stop_recovery",
                severity=90 + max_speed - record.min_speed,
                lap_summary=self.varied_issue_text(
                    record.name,
                    "major_stop_recovery",
                    [
                        f"{label}: {cause}. Speed fell from {max_speed:.0f} km/h to {record.min_speed:.0f} km/h, so this is not a normal cornering mistake.",
                        f"{label}: abnormal stop detected. Speed collapsed from {max_speed:.0f} km/h to {record.min_speed:.0f} km/h; {cause}.",
                        f"{label}: major recovery event. Telemetry shows a drop from {max_speed:.0f} km/h to {record.min_speed:.0f} km/h; {cause}.",
                    ],
                ),
                upcoming_hint=(
                    f"{label}: do not chase lap time here yet. Keep the car on track, reduce steering correction, and rebuild speed only after it is straight."
                ),
            )

        return None

    def choose_lap_focus_issue(self, records):
        usable_records = [record for record in records if record.sample_count >= 20]
        issues = [self.evaluate_record_issue(record) for record in usable_records]
        issues = [issue for issue in issues if issue is not None]
        return max(issues, key=lambda issue: issue.severity, default=None)

    def remember_lap_records(self, records):
        for record in records:
            if record.sample_count < 20:
                continue
            self.zone_history.setdefault(record.name, []).append(record)
            self.zone_history[record.name] = self.zone_history[record.name][-8:]

    def remember_decision_issues(self, issues):
        for issue in issues:
            key = issue_key(issue)
            self.session_issue_counts[key] = self.session_issue_counts.get(key, 0) + 1

    def merged_issue_counts(self):
        counts = dict(self.historical_issue_counts)
        for key, value in self.session_issue_counts.items():
            counts[key] = counts.get(key, 0) + value
        return counts

    def merged_issue_stats(self):
        stats = {key: dict(value) for key, value in self.historical_issue_stats.items()}
        for key, value in self.session_issue_counts.items():
            item = stats.setdefault(
                key,
                {
                    "historical_count": 0,
                    "historical_opportunities": 0,
                    "historical_frequency": 0.0,
                    "classification": "one_time_watchlist",
                },
            )
            item["session_count"] = value
        return stats

    def maybe_load_profile_counts(self, row):
        driver = str(row.get("player_name") or "").strip()
        track = str(row.get("track") or "").strip()
        car = str(row.get("car_model") or "").strip()
        if not driver:
            return
        key = (driver, track, car)
        if self.profile_loaded_for == key:
            return
        profile = load_profile(driver)
        self.historical_issue_counts = live_issue_counts(profile, track=track or None, car_model=car or None)
        self.historical_issue_stats = live_issue_stats(profile, track=track or None, car_model=car or None)
        self.profile_loaded_for = key

    def finish_zone(self):
        state = self.zone_state
        if state is None:
            return None

        record = ZoneSummary(
            name=state.name,
            lap_count=state.lap_count,
            entry_lap_time_ms=state.entry_lap_time_ms,
            exit_lap_time_ms=state.last_lap_time_ms,
            duration_ms=(
                state.last_lap_time_ms - state.entry_lap_time_ms
                if state.entry_lap_time_ms is not None
                and state.last_lap_time_ms is not None
                and state.last_lap_time_ms >= state.entry_lap_time_ms
                else None
            ),
            entry_speed=state.entry_speed,
            exit_speed=state.exit_speed,
            min_speed=state.min_speed,
            max_speed=state.max_speed,
            max_brake=state.max_brake,
            first_brake_lap_time_ms=state.first_brake_lap_time_ms,
            first_brake_distance_m=state.first_brake_distance_m,
            brake_release_lap_time_ms=state.brake_release_lap_time_ms,
            brake_release_distance_m=state.brake_release_distance_m,
            trail_brake_duration_ms=(
                state.brake_release_lap_time_ms - state.first_brake_lap_time_ms
                if state.first_brake_lap_time_ms is not None
                and state.brake_release_lap_time_ms is not None
                and state.brake_release_lap_time_ms >= state.first_brake_lap_time_ms
                else None
            ),
            throttle_pickup_lap_time_ms=state.throttle_pickup_lap_time_ms,
            throttle_pickup_distance_m=state.throttle_pickup_distance_m,
            max_abs_steer=state.max_abs_steer,
            high_steer_throttle_samples=state.high_steer_throttle_samples,
            brake_throttle_overlap_samples=state.brake_throttle_overlap_samples,
            entry_damage_total=state.entry_damage_total,
            max_damage_total=state.max_damage_total,
            max_damage_delta=max(
                0.0,
                (state.max_damage_total or 0.0) - (state.entry_damage_total or 0.0),
            )
            if state.entry_damage_total is not None and state.max_damage_total is not None
            else 0.0,
            max_number_of_tyres_out=state.max_number_of_tyres_out,
            max_abs_local_angular_vel=state.max_abs_local_angular_vel,
            max_wheel_slip=state.max_wheel_slip,
            official_invalid_triggered=state.official_invalid_triggered,
            official_invalid_lap_time_ms=state.official_invalid_lap_time_ms,
            official_invalid_distance_m=state.official_invalid_distance_m,
            official_invalid_speed_kmh=state.official_invalid_speed_kmh,
            sample_count=state.sample_count,
            seen_brake=state.seen_brake,
            seen_throttle_after_brake=state.seen_throttle_after_brake,
        )
        self.completed_zone_records.append(record)
        self.current_lap_zone_records.append(record)

        if state.seen_brake:
            brake_text = (
                f"brake at {fmt_lap_time(state.first_brake_lap_time_ms)}, "
                f"max brake {state.max_brake:.2f}"
            )
        else:
            brake_text = "no clear brake phase"

        if state.seen_throttle_after_brake:
            throttle_text = f"throttle pickup {fmt_lap_time(state.throttle_pickup_lap_time_ms)}"
        else:
            throttle_text = "no throttle pickup after brake detected"

        min_speed_text = f"min speed {state.min_speed:.0f} km/h" if state.min_speed is not None else "min speed unknown"
        summary = f"{self.zone_label(state.name)} summary: {brake_text}; {throttle_text}; {min_speed_text}."
        self.completed_zone_summaries.append(summary)
        if self.verbose_events:
            return self.maybe_message(summary, force=True)
        return None

    def detect_lap_transition(self, row):
        packet_lap_count = to_int(row.get("lap_count"))
        normalized = to_float(row.get("normalized_car_position"))

        wrapped_start_finish = (
            normalized is not None
            and self.last_normalized_position is not None
            and self.last_normalized_position > 0.85
            and normalized < 0.15
        )

        if packet_lap_count is not None:
            self.last_packet_lap_count = packet_lap_count
        if normalized is not None:
            self.last_normalized_position = normalized

        if not wrapped_start_finish:
            return None

        if not self.has_seen_start_finish:
            self.has_seen_start_finish = True
            if packet_lap_count is not None and packet_lap_count > 0:
                self.session_lap_number = packet_lap_count
            self.current_lap_zone_records = []
            self.completed_zone_records = []
            self.zone_state = None
            self.current_zone = None
            self.active_focus_issue = None
            self.current_lap_official_invalid = False
            self.current_lap_validity_seen = False
            self.previous_lap_validity_state = None
            self.triggered_locations_this_lap.clear()
            return {"type": "start", "current_lap": self.session_lap_number}

        completed_session_lap = self.session_lap_number
        # After the first clean crossing, the lap label Rachel announced as
        # started becomes the next completed lap. ACC's packet lap counter can
        # lag or advance depending on when the shared-memory packet arrives, so
        # only use it if it clearly advances beyond our current label.
        if packet_lap_count is not None and packet_lap_count > self.session_lap_number:
            self.session_lap_number = packet_lap_count
        else:
            self.session_lap_number += 1
        return {
            "type": "complete",
            "completed_lap": completed_session_lap,
            "current_lap": self.session_lap_number,
        }

    def lap_summary_messages(self, completed_lap_count, current_lap_count, official_invalid=False, validity_seen=False):
        records = consolidate_zone_records(self.current_lap_zone_records)
        if not records:
            return [f"Lap {current_lap_count} started."]

        issues = [self.evaluate_record_issue(record) for record in records if record.sample_count >= 20]
        issues = [issue for issue in issues if issue is not None]
        decision = build_lap_decision(
            lap_label=completed_lap_count,
            zone_count=len(records),
            issues=issues,
            issue_counts=self.merged_issue_counts(),
            issue_stats=self.merged_issue_stats(),
            official_invalid=official_invalid,
            validity_seen=validity_seen,
        )
        messages = decision["messages"]
        if decision["primary_issue"] is not None:
            primary_key = (
                decision["primary_issue"].get("zone_name"),
                decision["primary_issue"].get("reason_key"),
            )
            self.active_focus_issue = next(
                (
                    issue
                    for issue in issues
                    if (issue.zone_name, issue.reason_key) == primary_key
                ),
                None,
            )
        self.decision_history.append(decision)
        self.remember_decision_issues(issues)
        messages.append(f"Lap {current_lap_count} started.")
        self.remember_lap_records(records)
        self.current_lap_zone_records = []
        self.current_lap_official_invalid = False
        self.current_lap_validity_seen = False
        self.previous_lap_validity_state = None
        return sanitize_coach_messages(messages)

    def final_session_messages(self):
        self.finish_zone()
        records = consolidate_zone_records(self.current_lap_zone_records)
        if not self.has_seen_start_finish or not records:
            return []

        record_by_zone = {record.name: record for record in records}
        issues = [self.evaluate_record_issue(record) for record in records if record.sample_count >= 20]
        issues = [issue for issue in issues if issue is not None]
        if self.current_lap_validity_seen and not self.current_lap_official_invalid:
            # When the user stops a test and then Ctrl+C's the receiver, a final
            # partial lap can contain a speed drop to zero. If ACC still says the
            # lap is valid and no direct incident evidence changed, suppress that
            # speed-only fallback so Rachel does not report a fake crash/off.
            issues = [
                issue
                for issue in issues
                if issue.severity < 80 or self.has_direct_incident_evidence(record_by_zone.get(issue.zone_name))
            ]
        decision = build_lap_decision(
            lap_label=self.session_lap_number,
            zone_count=len(records),
            issues=issues,
            issue_counts=self.merged_issue_counts(),
            issue_stats=self.merged_issue_stats(),
            official_invalid=self.current_lap_official_invalid,
            validity_seen=self.current_lap_validity_seen,
            incomplete=True,
        )
        messages = decision["messages"]
        self.decision_history.append(decision)
        self.remember_decision_issues(issues)

        self.remember_lap_records(records)
        self.current_lap_zone_records = []
        return sanitize_coach_messages(messages)

    def update(self, row):
        messages = []
        self.maybe_load_profile_counts(row)
        distance_m = track_distance(row, self.lap_length_m, self.position_offset_lap)
        position = normalized_lap_position(row, self.position_offset_lap)
        zone = zone_for_distance(self.track_map, distance_m)
        zone_name = zone["name"] if zone else None
        lap_transition = self.detect_lap_transition(row)
        current_lap_count = self.session_lap_number
        speed = to_float(row.get("speed_kmh"))
        lap_time_ms = to_int(row.get("lap_time_ms"))
        moving_fast_enough = speed is not None and speed >= self.min_prompt_speed_kmh

        physics_packet_id = to_int(row.get("packet_id"))
        graphics_packet_id = to_int(row.get("graphics_packet_id"))
        dynamic_values = [
            to_float(row.get("speed_kmh")) or 0.0,
            to_float(row.get("throttle")) or 0.0,
            to_float(row.get("brake")) or 0.0,
            abs(to_float(row.get("steer")) or 0.0),
            float(to_int(row.get("gear")) or 0),
            float(to_int(row.get("rpm")) or 0),
        ]
        physics_stale = physics_packet_id is not None and physics_packet_id == self.last_physics_packet_id
        graphics_moving = graphics_packet_id is not None and graphics_packet_id != self.last_graphics_packet_id
        if all(value == 0.0 for value in dynamic_values) and physics_stale and graphics_moving:
            self.zero_physics_samples += 1
        else:
            self.zero_physics_samples = 0
            self.zero_physics_warning_sent = False

        if self.zero_physics_samples >= 120 and not self.zero_physics_warning_sent:
            messages.append(
                "Telemetry warning: ACC lap data is visible, but speed, pedals, steering, gear, and RPM are all zero. The helper may be connected to stale physics data."
            )
            self.zero_physics_warning_sent = True
        self.last_physics_packet_id = physics_packet_id
        self.last_graphics_packet_id = graphics_packet_id

        intro = self.intro_message(row, speed)
        if intro:
            messages.append(intro)
            messages.append(INTRO_COLLECTION_MESSAGE)

        if lap_transition is not None:
            self.finish_zone()
            completed_official_invalid = self.current_lap_official_invalid
            completed_validity_seen = self.current_lap_validity_seen
            self.zone_state = None
            self.current_zone = None
            self.triggered_locations_this_lap.clear()
            if lap_transition["type"] == "start":
                messages.append(f"Lap {lap_transition['current_lap']} started.")
            else:
                messages.extend(
                    self.lap_summary_messages(
                        lap_transition["completed_lap"],
                        lap_transition["current_lap"],
                        official_invalid=completed_official_invalid,
                        validity_seen=completed_validity_seen,
                    )
                )
            current_lap_count = self.session_lap_number

        if not self.has_seen_start_finish:
            return sanitize_coach_messages(messages)

        if zone_name != self.current_zone:
            exit_message = self.finish_zone()
            if exit_message:
                messages.append(exit_message)
            self.current_zone = zone_name
            if zone_name is not None:
                self.zone_state = ZoneState(
                    name=zone_name,
                    lap_count=current_lap_count,
                    entered_at=time.time(),
                    entry_lap_time_ms=lap_time_ms,
                    last_lap_time_ms=lap_time_ms,
                    entry_speed=speed,
                    exit_speed=speed,
                )
            else:
                self.zone_state = None

        validity = lap_validity_state(row)
        if validity in {"valid", "invalid"}:
            self.current_lap_validity_seen = True
            if (
                validity == "invalid"
                and self.previous_lap_validity_state == "valid"
                and self.zone_state is not None
                and not self.zone_state.official_invalid_triggered
            ):
                self.zone_state.official_invalid_triggered = True
                self.zone_state.official_invalid_lap_time_ms = lap_time_ms
                self.zone_state.official_invalid_distance_m = distance_m
                self.zone_state.official_invalid_speed_kmh = speed
            if validity == "invalid":
                self.current_lap_official_invalid = True
            self.previous_lap_validity_state = validity

        allow_corner_prompts = self.session_lap_number >= 2
        world_messages = self.world_gate_messages(row, moving_fast_enough, allow_prompts=allow_corner_prompts)
        if world_messages:
            messages.extend(world_messages)
        elif allow_corner_prompts and not self.world_path and not self.world_gates:
            messages.extend(self.location_trigger_messages(position, moving_fast_enough))

        state = self.zone_state
        if state is None:
            return sanitize_coach_messages(messages)

        brake = to_float(row.get("brake")) or 0.0
        throttle = to_float(row.get("throttle")) or 0.0
        steer = abs(to_float(row.get("steer")) or 0.0)
        progress = zone_progress(zone, distance_m, self.lap_length_m)

        state.sample_count += 1
        if lap_time_ms is not None:
            state.last_lap_time_ms = lap_time_ms
        if speed is not None:
            state.min_speed = min(speed, state.min_speed) if state.min_speed is not None else speed
            state.max_speed = max(speed, state.max_speed) if state.max_speed is not None else speed
            state.exit_speed = speed
        state.max_brake = max(state.max_brake, brake)
        state.max_abs_steer = max(state.max_abs_steer, steer)
        damage_total = row_damage_total(row)
        if damage_total is not None:
            if state.entry_damage_total is None:
                state.entry_damage_total = damage_total
            state.max_damage_total = max(damage_total, state.max_damage_total) if state.max_damage_total is not None else damage_total
        tyres_out = to_int(row.get("number_of_tyres_out"))
        if tyres_out is not None:
            state.max_number_of_tyres_out = max(state.max_number_of_tyres_out, tyres_out)
        state.max_abs_local_angular_vel = max(
            state.max_abs_local_angular_vel,
            max_abs_fields(row, ("local_angular_vel_x", "local_angular_vel_y", "local_angular_vel_z")),
        )
        state.max_wheel_slip = max(
            state.max_wheel_slip,
            max_abs_fields(row, ("wheel_slip_fl", "wheel_slip_fr", "wheel_slip_rl", "wheel_slip_rr")),
        )
        if steer >= 0.32 and throttle >= 0.45 and (speed or 0.0) >= 55:
            state.high_steer_throttle_samples += 1
        if brake >= 0.15 and throttle >= 0.20:
            state.brake_throttle_overlap_samples += 1

        if not state.seen_brake and brake >= 0.25:
            state.seen_brake = True
            state.first_brake_lap_time_ms = lap_time_ms
            state.first_brake_distance_m = distance_m
            message = self.maybe_message(
                f"{self.zone_label(state.name)}: brake phase detected at {fmt_lap_time(lap_time_ms)}.",
                force=True,
            )
            if message and self.verbose_events:
                messages.append(message)

        if (
            state.seen_brake
            and state.brake_release_lap_time_ms is None
            and brake < 0.10
            and lap_time_ms is not None
        ):
            state.brake_release_lap_time_ms = lap_time_ms
            state.brake_release_distance_m = distance_m

        if state.seen_brake and not state.seen_throttle_after_brake and throttle >= 0.35 and brake < 0.10:
            state.seen_throttle_after_brake = True
            state.throttle_pickup_lap_time_ms = lap_time_ms
            state.throttle_pickup_distance_m = distance_m
            message = self.maybe_message(
                f"{self.zone_label(state.name)}: throttle pickup detected at {fmt_lap_time(lap_time_ms)}.",
                force=True,
            )
            if message and self.verbose_events:
                messages.append(message)

        if progress is not None and progress > 0.72 and state.name in {"La Source", "Campus/Stavelot", "Bus Stop"}:
            if "exit" not in state.messages:
                state.messages.append("exit")
                message = self.maybe_message(f"{self.zone_label(state.name)}: focus exit now.", force=False)
                if message and self.verbose_events:
                    messages.append(message)

        return sanitize_coach_messages(messages)


def print_message(message):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


class VoiceSpeaker:
    def __init__(self, enabled=False, voice=None, rate=205):
        self.enabled = enabled
        self.voice = voice
        self.rate = rate
        self.messages = queue.Queue()
        self.worker = None
        if self.enabled:
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()

    def compact(self, message):
        replacements = {
            "Approaching ": "",
            "T1 La Source": "turn 1",
            "Turn 1 La Source": "turn 1",
            "T2, T3, T4 Eau Rouge/Raidillon, Kemmel": "turn 2, turn 3, turn 4",
            "T5, T6, T7 Les Combes/Malmedy": "turn 5, turn 6, turn 7",
            "T8, T9 Bruxelles": "turn 8, turn 9",
            "T10, T11 No Name/Pouhon entry": "turn 10, turn 11",
            "T12, T13 Pouhon/Fagnes": "turn 12, turn 13",
            "T14, T15 Campus/Stavelot": "turn 14, turn 15",
            "T16, T17 Blanchimont": "turn 16, turn 17",
            "T18, T19 Bus Stop chicane": "turn 18, turn 19 Bus Stop",
            "Next lap focus: ": "Next lap focus. ",
        }
        text = message
        for source, target in replacements.items():
            text = text.replace(source, target)
        return sanitize_directional_distance_text(text)

    def speak(self, message):
        if not self.enabled:
            return False
        self.messages.put(self.compact(message))
        return True

    def run(self):
        while True:
            text = self.messages.get()
            self.say(text)
            self.messages.task_done()

    def say(self, text):
        command = ["say", "-r", str(self.rate)]
        if self.voice:
            command.extend(["-v", self.voice])
        command.append(text)

        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError as exc:
            print(f"Voice output unavailable: {exc}", flush=True)
            self.enabled = False
            return False
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "unknown say failure").strip()
            print(f"Voice output failed: {error}", flush=True)
            return False
        return True


def main():
    parser = argparse.ArgumentParser(description="Real-time ACC text coach for Spa")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--coach-level", default="Beginner", choices=["Beginner", "Intermediate", "Pro"])
    parser.add_argument("--track", default="auto", help="Track hint from the app. Telemetry auto-detection remains preferred.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47777)
    parser.add_argument("--message-gap", type=float, default=2.5)
    parser.add_argument(
        "--verbose-events",
        action="store_true",
        help="Print detailed brake/throttle/zone summary events while driving.",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Speak coach messages using macOS say. Terminal text is still printed and logged.",
    )
    parser.add_argument("--voice-name", default=None, help="Optional macOS say voice name.")
    parser.add_argument("--voice-rate", type=int, default=205, help="macOS say speaking rate.")
    parser.add_argument(
        "--position-offset-lap",
        type=float,
        default=0.0,
        help="Fine-tune corner detection using lap fraction offset. Positive speaks earlier; negative speaks later.",
    )
    parser.add_argument(
        "--min-prompt-speed",
        type=float,
        default=35.0,
        help="Suppress approach prompts below this speed in km/h.",
    )
    parser.add_argument(
        "--disable-world-gates",
        action="store_true",
        help="Use normalized lap-position gates instead of calibrated car_world_x/car_world_z gates.",
    )
    parser.add_argument(
        "--disable-reference-compare",
        action="store_true",
        help="Do not generate a Milestone 3 personal-best comparison report when the run stops.",
    )
    parser.add_argument(
        "--reference",
        default=None,
        help="Optional personal-best reference JSON path for post-run comparison.",
    )
    parser.add_argument(
        "--pro-video-reference",
        default=None,
        help="Optional pro-video reference JSON path. Use 'latest' to load the newest generated pro video reference.",
    )
    parser.add_argument(
        "--disable-driver-profile",
        action="store_true",
        help="Do not update the persistent Milestone 4 driver profile at the end of the run.",
    )
    parser.add_argument(
        "--ai-coach",
        action="store_true",
        help="After the run, generate Rachel's optional LLM phrasing report from coach_ai_context.json.",
    )
    parser.add_argument(
        "--ai-model",
        default="gpt-5-mini",
        help="Model for --ai-coach. Requires OPENAI_API_KEY.",
    )
    args = parser.parse_args()
    if args.message_gap == 2.5:
        args.message_gap = {
            "Beginner": 3.0,
            "Intermediate": 2.35,
            "Pro": 2.0,
        }.get(args.coach_level, 2.5)

    track_map = load_spa_map()
    world_path = None if args.disable_world_gates else load_spa_world_path()
    world_gates = [] if args.disable_world_gates or world_path else load_spa_world_gates()
    pro_video_reference = None
    if args.pro_video_reference:
        pro_video_reference = load_pro_video_reference(None if args.pro_video_reference == "latest" else args.pro_video_reference)
    coach = RealtimeCoach(
        track_map,
        world_gates=world_gates,
        world_path=world_path,
        min_message_gap=args.message_gap,
        verbose_events=args.verbose_events,
        position_offset_lap=args.position_offset_lap,
        min_prompt_speed_kmh=args.min_prompt_speed,
        pro_video_reference=pro_video_reference,
    )
    speaker = VoiceSpeaker(enabled=args.voice, voice=args.voice_name, rate=args.voice_rate)
    run_name = args.run_name or f"realtime-coach-{now_stamp()}"
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    rows = []

    ndjson_path = run_dir / "telemetry.ndjson"
    csv_path = run_dir / "telemetry.csv"
    events_path = run_dir / "coach_events.ndjson"
    summary_path = run_dir / "summary.json"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)

    print(f"Realtime coach listening on udp://{args.host}:{args.port}")
    print(f"Saving run to {run_dir}")
    if world_path:
        trigger_mode = "world path"
    elif world_gates:
        trigger_mode = "world gates"
    else:
        trigger_mode = "normalized lap-position fallback"
    print(f"Corner trigger mode: {trigger_mode}")
    if pro_video_reference:
        print(f"Pro video reference: {pro_video_reference.get('_path')}")
    print("Run the CrossOver helper in another terminal. Stop with Ctrl+C.")

    last_waiting_at = 0.0
    packet_count = 0

    with (
        ndjson_path.open("w", encoding="utf-8") as ndjson_file,
        csv_path.open("w", newline="", encoding="utf-8") as csv_file,
        events_path.open("w", encoding="utf-8") as events_file,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

        try:
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    now = time.time()
                    if now - last_waiting_at > 5:
                        print("Waiting for telemetry packets...", flush=True)
                        last_waiting_at = now
                    continue

                try:
                    packet = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                row = normalize_packet(packet)
                rows.append(row)
                ndjson_file.write(json.dumps(row) + "\n")
                writer.writerow(row)
                packet_count += 1

                for message in coach.update(row):
                    event = {
                        "received_at": row["received_at"],
                        "packet_id": row.get("packet_id"),
                        "lap_count": row.get("lap_count"),
                        "lap_time_ms": row.get("lap_time_ms"),
                        "lap_validity": lap_validity_state(row),
                        "current_lap_invalid": row.get("current_lap_invalid"),
                        "current_lap_valid": row.get("current_lap_valid"),
                        "is_valid_lap": row.get("is_valid_lap"),
                        "message": message,
                    }
                    events_file.write(json.dumps(event) + "\n")
                    print_message(message)
                    if speaker.speak(message):
                        events_file.write(json.dumps({**event, "spoken": True}) + "\n")

                if packet_count % 200 == 0:
                    speed = to_float(row.get("speed_kmh"))
                    zone = coach.current_zone or "unknown zone"
                    print(
                        f"packets={packet_count} speed={speed:.0f} zone={zone}"
                        if speed is not None else f"packets={packet_count} zone={zone}",
                        flush=True,
                    )

        except KeyboardInterrupt:
            print("\nRealtime coach stopped.")
        finally:
            final_row = rows[-1] if rows else {}
            for message in coach.final_session_messages():
                event = {
                    "received_at": final_row.get("received_at"),
                    "packet_id": final_row.get("packet_id"),
                    "lap_count": final_row.get("lap_count"),
                    "lap_time_ms": final_row.get("lap_time_ms"),
                    "lap_validity": lap_validity_state(final_row),
                    "current_lap_invalid": final_row.get("current_lap_invalid"),
                    "current_lap_valid": final_row.get("current_lap_valid"),
                    "is_valid_lap": final_row.get("is_valid_lap"),
                    "message": message,
                    "final_review": True,
                }
                events_file.write(json.dumps(event) + "\n")
                print_message(message)
                if speaker.speak(message):
                    events_file.write(json.dumps({**event, "spoken": True}) + "\n")

            ended_at = time.time()
            summary = summarize(rows, started_at, ended_at)
            detected_tracks = summary.get("tracks_seen") or []
            summary["app"] = {
                "coach_level": args.coach_level,
                "track_hint": args.track,
                "detected_track": detected_tracks[0] if detected_tracks else "",
                "track_detection": "telemetry" if detected_tracks else "fallback",
            }
            summary["coach"] = {
                "events_path": str(events_path),
                "completed_zone_summaries": coach.completed_zone_summaries[-20:],
                "mode": "verbose" if args.verbose_events else "quiet",
                "voice_enabled": args.voice,
                "position_offset_lap": args.position_offset_lap,
                "min_prompt_speed_kmh": args.min_prompt_speed,
                "corner_trigger_mode": trigger_mode.replace(" ", "_"),
                "decision_layer": "coach_decision_v1",
                "decision_history": coach.decision_history[-10:],
                "pro_video_reference": pro_video_reference.get("_path") if pro_video_reference else "",
            }
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"Packets: {packet_count}")
            print(f"Summary: {summary_path}")
            print(f"Events: {events_path}")
            if coach.decision_history:
                decisions_json, ai_context_json, decision_md = write_decision_artifacts(run_dir, coach.decision_history)
                print(f"Coach decisions: {decision_md}")
                print(f"AI context: {ai_context_json}")
                if args.ai_coach:
                    try:
                        from ai_coach import generate_ai_coach_output

                        ai_json, ai_md, ai_output, ai_source = generate_ai_coach_output(run_dir, model=args.ai_model)
                        print(f"AI coach output: {ai_md}")
                        print(f"AI coach source: {ai_source}")
                        for line in ai_output.get("voice_lines", [])[:3]:
                            print_message(line)
                    except Exception as exc:
                        print(f"AI coach skipped: {exc}")
            try:
                turn_json, turn_md = write_turn_timing_report(run_dir, build_turn_timing_report(run_dir))
                print(f"Turn timing: {turn_md}")
            except Exception as exc:
                print(f"Turn timing skipped: {exc}")
            if not args.disable_reference_compare:
                reference_path = Path(args.reference) if args.reference else None
                if reference_path is None:
                    track = next((str(row.get("track")) for row in rows if row.get("track")), "Spa")
                    car = next((str(row.get("car_model")) for row in rows if row.get("car_model")), "")
                    reference_path = REFERENCES_DIR / f"{slug(track)}_{slug(car)}_personal_best.json"
                if reference_path.exists():
                    try:
                        comparison_json, comparison_md, comparison = compare_run(reference_path, run_dir)
                        print(f"Reference comparison: {comparison_md}")
                        print(f"Reference focus: {comparison['recommendation']['text']}")
                        if not args.disable_driver_profile:
                            profile_json, profile_md, profile = update_profile(run_dir, comparison_json)
                            print(f"Driver profile: {profile_json}")
                            print(f"Driver profile update: {profile_md}")
                            if profile.get("current_focus"):
                                focus = profile["current_focus"]
                                print(f"Driver focus: {focus['zone_label']} - {focus['reason']} ({focus['count']}x)")
                            elif profile.get("current_watchlist"):
                                watch = profile["current_watchlist"]
                                print(f"Driver watchlist: {watch['zone_label']} - {watch['reason']} ({watch['count']}x)")
                    except Exception as exc:
                        print(f"Reference comparison skipped: {exc}")
                else:
                    print(f"Reference comparison skipped: no reference found at {reference_path}")
                    if not args.disable_driver_profile:
                        try:
                            profile_json, profile_md, profile = update_profile(run_dir)
                            print(f"Driver profile: {profile_json}")
                            print(f"Driver profile update: {profile_md}")
                        except Exception as exc:
                            print(f"Driver profile update skipped: {exc}")
            elif not args.disable_driver_profile:
                try:
                    profile_json, profile_md, profile = update_profile(run_dir)
                    print(f"Driver profile: {profile_json}")
                    print(f"Driver profile update: {profile_md}")
                except Exception as exc:
                    print(f"Driver profile update skipped: {exc}")
            sock.close()


if __name__ == "__main__":
    main()
