#!/usr/bin/env python3
import argparse
import json
import math
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np

from reference_compare import ROOT


OUT_DIR = ROOT / "data" / "pro_video_references"
SPA_MAP_PATH = ROOT / "data" / "track_maps" / "spa.json"
SPA_TURN_GROUPS = [
    ("T1", "La Source", "hairpin entry, rotation, exit traction"),
    ("T2-T4", "Eau Rouge/Raidillon", "flat commitment, compression, track limit margin"),
    ("T5-T7", "Les Combes/Malmedy", "brake reference, change of direction, exit line"),
    ("T8-T9", "Bruxelles", "long brake release, patient throttle"),
    ("T10-T11", "No Name/Pouhon entry", "minimum speed and steering load"),
    ("T12-T13", "Pouhon/Fagnes", "high-speed rotation and throttle confidence"),
    ("T14-T15", "Campus/Stavelot", "exit speed and throttle pickup"),
    ("T16-T17", "Blanchimont", "commitment and small steering corrections"),
    ("T18-T19", "Bus Stop", "straight braking, rotation, traction on exit"),
]
SPA_ZONE_LABELS = {
    "La Source": "T1 La Source",
    "Eau Rouge/Raidillon/Kemmel": "T2, T3, T4 Eau Rouge/Raidillon/Kemmel",
    "Les Combes/Malmedy": "T5, T6, T7 Les Combes/Malmedy",
    "Bruxelles": "T8, T9 Bruxelles",
    "No Name/Pouhon Entry": "T10, T11 No Name/Pouhon entry",
    "Pouhon/Fagnes": "T12, T13 Pouhon/Fagnes",
    "Campus/Stavelot": "T14, T15 Campus/Stavelot",
    "Blanchimont": "T16, T17 Blanchimont",
    "Bus Stop": "T18, T19 Bus Stop chicane",
}


def tool_path(name):
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(prefix) / name
        if candidate.exists():
            return str(candidate)
    return None


def run_command(command):
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def media_info(path):
    ffprobe = tool_path("ffprobe")
    if not ffprobe:
        return {"available": False, "error": "ffprobe not found"}
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate:stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = run_command(command)
    if not result["ok"]:
        return {"available": False, "error": result["stderr"] or result["stdout"]}
    try:
        return {"available": True, "ffprobe": json.loads(result["stdout"])}
    except json.JSONDecodeError:
        return {"available": False, "error": "ffprobe returned invalid JSON"}


def duration_seconds(info):
    try:
        return float(((info.get("ffprobe") or {}).get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return None


def format_duration(seconds):
    if seconds is None:
        return "unknown"
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:05.2f}"


def format_ms(ms):
    if ms is None:
        return "unavailable"
    return format_duration(ms / 1000)


def parse_timecode(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if ":" not in text:
        return float(text)
    parts = text.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise argparse.ArgumentTypeError(f"Invalid time format: {value}. Use seconds, M:SS, or H:MM:SS.")


def video_stream(info):
    probe = info.get("ffprobe") or {}
    return next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {})


def parse_rate(value):
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            den_value = float(den)
            return float(num) / den_value if den_value else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def video_summary(info):
    if not info.get("available"):
        return {"duration": "unknown", "resolution": "unknown", "frame_rate": "unknown", "codec": "unknown"}
    stream = video_stream(info)
    width = stream.get("width")
    height = stream.get("height")
    return {
        "duration": format_duration(duration_seconds(info)),
        "resolution": f"{width}x{height}" if width and height else "unknown",
        "frame_rate": stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "unknown",
        "codec": stream.get("codec_name") or "unknown",
    }


def safe_stem(path):
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    return "".join(char if char in allowed else "-" for char in path.stem).strip("-") or "video"


def extract_visual_reference(path, out_dir, max_frames=16):
    ffmpeg = tool_path("ffmpeg")
    if not ffmpeg:
        return {"available": False, "error": "ffmpeg not found", "frames": [], "contact_sheet": ""}
    info = media_info(path)
    seconds = duration_seconds(info) or 0
    interval = max(1, math.floor(seconds / max_frames)) if seconds else 15
    video_dir = out_dir / safe_stem(path)
    frame_dir = video_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    contact_sheet = video_dir / "contact_sheet.jpg"

    frame_pattern = frame_dir / "frame_%03d.jpg"
    frames_result = run_command([
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-vf",
        f"fps=1/{interval},scale=640:-1",
        "-frames:v",
        str(max_frames),
        str(frame_pattern),
    ])
    sheet_result = run_command([
        ffmpeg,
        "-y",
        "-i",
        str(path),
        "-vf",
        f"fps=1/{interval},scale=320:-1,tile=4x4:padding=8:margin=8:color=white",
        "-frames:v",
        "1",
        str(contact_sheet),
    ])
    frames = sorted(str(item) for item in frame_dir.glob("frame_*.jpg"))
    return {
        "available": bool(frames),
        "interval_seconds": interval,
        "frames": frames,
        "contact_sheet": str(contact_sheet) if contact_sheet.exists() else "",
        "frame_extract_ok": frames_result["ok"],
        "contact_sheet_ok": sheet_result["ok"],
        "error": "" if frames else (frames_result["stderr"] or sheet_result["stderr"]),
    }


def roi(frame, box):
    height, width, _ = frame.shape
    x1 = max(0, min(width - 1, int(width * box[0])))
    y1 = max(0, min(height - 1, int(height * box[1])))
    x2 = max(x1 + 1, min(width, int(width * box[2])))
    y2 = max(y1 + 1, min(height, int(height * box[3])))
    return frame[y1:y2, x1:x2]


def colored_fill(region, color):
    if region.size == 0:
        return None
    r = region[:, :, 0].astype(np.int16)
    g = region[:, :, 1].astype(np.int16)
    b = region[:, :, 2].astype(np.int16)
    if color == "green":
        mask = (g > 85) & (g > r + 18) & (g > b + 18)
    elif color == "red":
        mask = (r > 85) & (r > g + 18) & (r > b + 18)
    else:
        return None
    return float(mask.mean())


def steering_estimate(region):
    if region.size == 0:
        return None
    r = region[:, :, 0].astype(np.int16)
    g = region[:, :, 1].astype(np.int16)
    b = region[:, :, 2].astype(np.int16)
    bright = (r + g + b) / 3
    mask = bright > 155
    if mask.mean() < 0.002:
        return None
    xs = np.where(mask)[1]
    center = (region.shape[1] - 1) / 2
    if center <= 0:
        return None
    return float(max(-1.0, min(1.0, (xs.mean() - center) / center)))


def yellow_digit_mask(region):
    if region.size == 0:
        return None
    r = region[:, :, 0].astype(np.int16)
    g = region[:, :, 1].astype(np.int16)
    b = region[:, :, 2].astype(np.int16)
    return (r > 70) & (g > 60) & (b < 95) & ((r + g - b) > 130) & (r >= g - 55)


def segment_fill(mask):
    if mask is None or mask.size == 0:
        return {}
    height, width = mask.shape
    if height < 8 or width < 8:
        return {}

    def area(y1, y2, x1, x2):
        y1 = max(0, min(height - 1, int(height * y1)))
        y2 = max(y1 + 1, min(height, int(height * y2)))
        x1 = max(0, min(width - 1, int(width * x1)))
        x2 = max(x1 + 1, min(width, int(width * x2)))
        return float(mask[y1:y2, x1:x2].mean())

    return {
        "a": area(0.00, 0.22, 0.15, 0.85),
        "b": area(0.08, 0.52, 0.55, 1.00),
        "c": area(0.43, 0.92, 0.55, 1.00),
        "d": area(0.75, 1.00, 0.15, 0.85),
        "e": area(0.43, 0.92, 0.00, 0.45),
        "f": area(0.08, 0.52, 0.00, 0.45),
        "g": area(0.36, 0.64, 0.15, 0.85),
    }


def estimate_cockpit_gear(frame, box):
    mask = yellow_digit_mask(roi(frame, box))
    if mask is None or float(mask.mean()) < 0.025:
        return None, 0.0
    fills = segment_fill(mask)
    if not fills:
        return None, 0.0
    on = {key for key, value in fills.items() if value >= 0.10}
    weak_on = {key for key, value in fills.items() if value >= 0.065}
    height, width = mask.shape
    left_slice = mask[int(height * 0.15) : int(height * 0.85), 0 : max(1, int(width * 0.35))]
    left_row_coverage = float((left_slice.mean(axis=1) > 0.05).mean()) if left_slice.size else 0.0

    digit = None
    confidence = 0.0
    if {"b", "c"}.issubset(on) and "g" not in weak_on and "f" not in weak_on:
        digit, confidence = 1, 0.58
    elif {"b", "c", "g"}.issubset(on) and fills.get("d", 0) < 0.045 and left_row_coverage < 0.50:
        digit, confidence = 3, 0.68
    elif {"b", "c", "g"}.issubset(on) and "e" not in weak_on and fills.get("f", 0) < 0.18:
        digit, confidence = 3, 0.62
    elif {"f", "b", "c", "g"}.issubset(weak_on) and "e" not in weak_on:
        digit, confidence = 4, 0.58
    elif {"f", "g", "c"}.issubset(weak_on) and "b" not in on and "e" not in on:
        digit, confidence = 5, 0.55
    elif {"f", "g", "e", "c"}.issubset(weak_on):
        digit, confidence = 6, 0.55
    elif {"a", "b", "c"}.issubset(weak_on) and "e" not in weak_on and "f" not in on:
        digit, confidence = 7, 0.50
    elif len(weak_on) >= 6:
        digit, confidence = 8, 0.45
    elif {"a", "b", "c", "f", "g"}.issubset(weak_on):
        digit, confidence = 9, 0.50

    if digit is None:
        return None, 0.0
    confidence = min(0.95, confidence + min(0.20, float(mask.mean())))
    return digit, round(confidence, 3)


def estimate_cockpit_speed(frame, box):
    # The speed digits in the user's current 480p source are too small/blurred
    # for the lightweight numpy recognizer. Keep the ROI in the manifest so a
    # later OCR pass can use the correct wheel-display location.
    _ = roi(frame, box)
    return None, 0.0


def stable_numeric(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    values = sorted(values)
    return values[len(values) // 2]


def stable_mode(values):
    counts = {}
    for value in values:
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def gear_detection_quality(samples):
    gears = [sample.get("estimated_gear") for sample in samples if sample.get("estimated_gear") is not None]
    unique_gears = sorted(set(gears))
    coverage = len(gears) / len(samples) if samples else 0.0
    dominant_ratio = 0.0
    if gears:
        dominant_ratio = max(gears.count(value) for value in unique_gears) / len(gears)
    reliable = coverage >= 0.25 and len(unique_gears) >= 3 and dominant_ratio <= 0.72
    if reliable:
        reason = "reliable"
    elif coverage < 0.25:
        reason = "low reliability: not enough cockpit gear samples were readable"
    elif len(unique_gears) < 3:
        reason = "low reliability: not enough believable gear variation across the lap"
    else:
        reason = "low reliability: one detected gear dominates the lap, which suggests the digit recognizer is locking onto cockpit graphics"
    return {
        "attempted": True,
        "method": "cockpit_wheel_display_yellow_digit",
        "roi": "cockpit_gear_roi",
        "sample_count": len(gears),
        "sample_coverage": round(coverage, 3),
        "dominant_ratio": round(dominant_ratio, 3),
        "unique_values": unique_gears,
        "reliable": reliable,
        "reason": reason,
    }


def read_scaled_frames(path, fps=5, width=640, start_s=None, end_s=None):
    ffmpeg = tool_path("ffmpeg")
    info = media_info(path)
    stream = video_stream(info)
    source_width = stream.get("width")
    source_height = stream.get("height")
    if not ffmpeg or not source_width or not source_height:
        return [], {"ok": False, "error": "ffmpeg or video dimensions unavailable"}
    height = max(2, int(round(width * int(source_height) / int(source_width))))
    if height % 2:
        height += 1
    command = [
        ffmpeg,
        "-v",
        "error",
    ]
    if start_s is not None and start_s > 0:
        command.extend(["-ss", str(start_s)])
    command.extend([
        "-i",
        str(path),
    ])
    if start_s is not None and end_s is not None and end_s > start_s:
        command.extend(["-t", str(end_s - start_s)])
    command.extend([
        "-vf",
        f"fps={fps},scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ])
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0:
        return [], {"ok": False, "error": result.stderr.decode("utf-8", errors="replace")}
    frame_size = width * height * 3
    frames = []
    for index in range(0, len(result.stdout), frame_size):
        chunk = result.stdout[index : index + frame_size]
        if len(chunk) != frame_size:
            continue
        frames.append(np.frombuffer(chunk, dtype=np.uint8).reshape((height, width, 3)))
    return frames, {"ok": True, "width": width, "height": height, "fps": fps}


def load_spa_zones(track):
    if track.lower() != "spa" or not SPA_MAP_PATH.exists():
        return 7004, []
    data = json.loads(SPA_MAP_PATH.read_text(encoding="utf-8"))
    return data.get("lap_length_m", 7004), data.get("braking_zones", [])


def zone_for_progress(progress, lap_length_m, zones):
    distance = progress * lap_length_m
    for zone in zones:
        start = zone["start_m"]
        end = zone["end_m"]
        if start <= distance < min(end, lap_length_m):
            return zone["name"]
        if end > lap_length_m and (distance >= start or distance < end - lap_length_m):
            return zone["name"]
    return None


def first_index(samples, predicate):
    for index, sample in enumerate(samples):
        if predicate(sample):
            return index
    return None


def pro_zone_metrics(samples, lap_length_m, zone=None):
    if not samples:
        return {}
    brake_start_index = first_index(samples, lambda item: item.get("brake", 0) >= 0.18)
    brake_release_index = None
    if brake_start_index is not None:
        for index in range(brake_start_index, len(samples)):
            if samples[index].get("brake", 0) < 0.08:
                brake_release_index = index
                break
    throttle_pickup_index = first_index(samples, lambda item: item.get("throttle", 0) >= 0.25 and item.get("brake", 0) < 0.10)
    full_throttle_index = first_index(samples, lambda item: item.get("throttle", 0) >= 0.75 and item.get("brake", 0) < 0.08)

    def distance(index):
        if index is None:
            return None
        return round(samples[index]["progress"] * lap_length_m, 3)

    def ms(index):
        if index is None:
            return None
        return int(samples[index]["time_s"] * 1000)

    entry_time_ms = int(samples[0]["time_s"] * 1000)
    exit_time_ms = int(samples[-1]["time_s"] * 1000)
    duration_ms = max(0, exit_time_ms - entry_time_ms)
    valid_speeds = [item.get("estimated_speed_kmh") for item in samples if item.get("estimated_speed_kmh") is not None]
    valid_gears = [item.get("estimated_gear") for item in samples if item.get("estimated_gear") is not None]
    edge_count = max(1, int(len(samples) * 0.20))
    entry_speed = stable_numeric(item.get("estimated_speed_kmh") for item in samples[:edge_count])
    exit_speed = stable_numeric(item.get("estimated_speed_kmh") for item in samples[-edge_count:])
    entry_gear = stable_mode(item.get("estimated_gear") for item in samples[:edge_count])
    exit_gear = stable_mode(item.get("estimated_gear") for item in samples[-edge_count:])

    trail_ms = None
    if brake_start_index is not None and brake_release_index is not None:
        trail_ms = max(0, ms(brake_release_index) - ms(brake_start_index))

    return {
        "sample_count": len(samples),
        "turn_label": SPA_ZONE_LABELS.get(zone["name"], zone["name"]) if zone else "",
        "zone_start_distance_m": zone.get("start_m") if zone else None,
        "zone_end_distance_m": zone.get("end_m") if zone else None,
        "entry_time_ms": entry_time_ms,
        "exit_time_ms": exit_time_ms,
        "duration_ms": duration_ms,
        "entry_timecode": format_ms(entry_time_ms),
        "exit_timecode": format_ms(exit_time_ms),
        "estimated_entry_speed_kmh": round(entry_speed, 1) if entry_speed is not None else None,
        "estimated_exit_speed_kmh": round(exit_speed, 1) if exit_speed is not None else None,
        "estimated_min_speed_kmh": round(min(valid_speeds), 1) if valid_speeds else None,
        "estimated_entry_gear": entry_gear,
        "estimated_exit_gear": exit_gear,
        "gear_sample_count": len(valid_gears),
        "speed_sample_count": len(valid_speeds),
        "brake_start_distance_m": distance(brake_start_index),
        "brake_end_distance_m": distance(brake_release_index),
        "trail_brake_duration_ms": trail_ms,
        "throttle_pickup_distance_m": distance(throttle_pickup_index),
        "full_throttle_distance_m": distance(full_throttle_index),
        "max_brake": round(max(item.get("brake", 0) for item in samples), 3),
        "max_throttle": round(max(item.get("throttle", 0) for item in samples), 3),
        "max_abs_steer": round(max(abs(item.get("steer", 0)) for item in samples), 3),
        "input_confidence": round(float(np.mean([item.get("confidence", 0) for item in samples])), 3),
    }


def normalize_input_channels(samples):
    for channel in ("throttle", "brake"):
        values = np.array([float(sample.get(channel, 0.0)) for sample in samples], dtype=float)
        if values.size == 0:
            continue
        low = float(np.percentile(values, 5))
        high = float(np.percentile(values, 95))
        if high - low < 0.01:
            high = float(values.max())
            low = float(values.min())
        if high - low < 0.005:
            continue
        for sample in samples:
            raw = float(sample.get(channel, 0.0))
            sample[f"raw_{channel}"] = round(raw, 5)
            sample[channel] = round(max(0.0, min(1.0, (raw - low) / (high - low))), 3)
    return samples


def extract_input_trace(path, track, sample_fps=5, lap_start_s=None, lap_end_s=None):
    info = media_info(path)
    media_duration = duration_seconds(info)
    if not media_duration:
        return {"available": False, "error": "video duration unavailable"}
    segment_start = max(0.0, lap_start_s or 0.0)
    segment_end = lap_end_s if lap_end_s is not None and lap_end_s > segment_start else media_duration
    duration = max(0.001, segment_end - segment_start)
    frames, frame_info = read_scaled_frames(path, fps=sample_fps, start_s=segment_start, end_s=segment_end)
    if not frames:
        return {"available": False, "error": frame_info.get("error", "no frames sampled")}

    layout = {
        "steering_roi": [0.68, 0.62, 0.98, 0.98],
        "throttle_roi": [0.88, 0.68, 0.98, 0.98],
        "brake_roi": [0.77, 0.68, 0.88, 0.98],
        "cockpit_gear_roi": [0.45, 0.66, 0.535, 0.84],
        "cockpit_speed_roi": [0.45, 0.78, 0.54, 0.88],
        "position_method": "estimated_from_video_time",
        "speed_gear_method": "cockpit_wheel_display_best_effort",
        "sample_fps": sample_fps,
    }
    lap_length_m, zones = load_spa_zones(track)
    samples = []
    for index, frame in enumerate(frames):
        time_s = index / sample_fps
        progress = min(0.9999, max(0.0, time_s / duration))
        throttle = colored_fill(roi(frame, layout["throttle_roi"]), "green")
        brake = colored_fill(roi(frame, layout["brake_roi"]), "red")
        steer = steering_estimate(roi(frame, layout["steering_roi"]))
        gear, gear_confidence = estimate_cockpit_gear(frame, layout["cockpit_gear_roi"])
        speed, speed_confidence = estimate_cockpit_speed(frame, layout["cockpit_speed_roi"])
        confidence_parts = [
            1.0 if throttle is not None else 0.0,
            1.0 if brake is not None else 0.0,
            1.0 if steer is not None else 0.0,
        ]
        samples.append(
            {
                "time_s": round(time_s, 3),
                "progress": round(progress, 6),
                "distance_m": round(progress * lap_length_m, 3),
                "zone": zone_for_progress(progress, lap_length_m, zones),
                "throttle": round(min(1.0, (throttle or 0.0) * 8.0), 3),
                "brake": round(min(1.0, (brake or 0.0) * 8.0), 3),
                "steer": round(steer or 0.0, 3),
                "estimated_speed_kmh": speed,
                "estimated_speed_confidence": speed_confidence,
                "estimated_gear": gear,
                "estimated_gear_confidence": gear_confidence,
                "confidence": round(sum(confidence_parts) / len(confidence_parts), 3),
            }
        )
    samples = normalize_input_channels(samples)

    zone_metrics = {}
    for zone in zones:
        zone_samples = [sample for sample in samples if sample["zone"] == zone["name"]]
        zone_metrics[zone["name"]] = pro_zone_metrics(zone_samples, lap_length_m, zone)

    gear_quality = gear_detection_quality(samples)
    if not gear_quality["reliable"]:
        for metric in zone_metrics.values():
            metric["estimated_entry_gear"] = None
            metric["estimated_exit_gear"] = None

    available_metrics = {
        "steering": any(abs(sample.get("steer", 0.0)) > 0.01 for sample in samples),
        "throttle": any(sample.get("throttle", 0.0) > 0.10 for sample in samples),
        "brake": any(sample.get("brake", 0.0) > 0.10 for sample in samples),
        "turn_entry_exit_time_nodes": bool(zone_metrics),
        "turn_entry_exit_speed": any(metric.get("estimated_entry_speed_kmh") is not None for metric in zone_metrics.values()),
        "turn_entry_exit_gear": gear_quality["reliable"] and any(metric.get("estimated_entry_gear") is not None for metric in zone_metrics.values()),
    }

    return {
        "available": True,
        "schema": "acc_ai_coach_pro_video_input_trace_v1",
        "track": track,
        "duration_seconds": round(duration, 3),
        "source_duration_seconds": round(media_duration, 3),
        "lap_segment_start_s": round(segment_start, 3),
        "lap_segment_end_s": round(segment_end, 3),
        "lap_length_m": lap_length_m,
        "layout": layout,
        "available_metrics": available_metrics,
        "gear_detection": gear_quality,
        "speed_detection": {
            "attempted": True,
            "method": "cockpit_wheel_display_small_number",
            "roi": "cockpit_speed_roi",
            "sample_count": 0,
            "reliable": False,
            "reason": "low reliability: the current 480p video makes the small speed digits too blurred for the lightweight recognizer",
        },
        "samples": samples,
        "zones": zone_metrics,
        "limitations": [
            "Steering, throttle, and brake are estimated from the video HUD color/shape, not from game telemetry.",
            "Pro video gear and speed are read from the steering-wheel display only when reliability checks pass.",
            "Track position is estimated from video time unless a later minimap OCR/calibration pass is added.",
            "Use this as a pro-style reference layer; ACC telemetry remains authoritative for the user's live run.",
        ],
    }


def write_pro_reference(out_dir, manifest):
    entries = []
    for entry in manifest["entries"]:
        trace = entry.get("input_trace") or {}
        if trace.get("available"):
            entries.append(entry)
    reference = {
        "schema": "acc_ai_coach_pro_video_reference_v1",
        "created_at": manifest["created_at"],
        "track": manifest["track"],
        "car_model": manifest.get("car_model", ""),
        "reference_type": "pro_video_hud_estimate",
        "source_videos": [entry["path"] for entry in entries],
        "lap_time_ms": int((entries[0]["input_trace"]["duration_seconds"] if entries else 0) * 1000) if entries else None,
        "lap_time_display": format_duration(entries[0]["input_trace"]["duration_seconds"] if entries else None) if entries else "unknown",
        "lap_profile": {
            "zones": entries[0]["input_trace"]["zones"] if entries else {},
            "input_trace_sample": entries[0]["input_trace"]["samples"][:400] if entries else [],
            "available_metrics": entries[0]["input_trace"].get("available_metrics", {}) if entries else {},
            "gear_detection": entries[0]["input_trace"].get("gear_detection", {}) if entries else {},
            "speed_detection": entries[0]["input_trace"].get("speed_detection", {}) if entries else {},
        },
        "reference_quality": reference_quality(entries[0]["input_trace"] if entries else {}),
        "confidence": "estimated_from_video_hud",
        "limitations": entries[0]["input_trace"].get("limitations", []) if entries else [],
    }
    output = out_dir / "pro_video_reference.json"
    output.write_text(json.dumps(reference, indent=2), encoding="utf-8")
    return output, reference


def reference_quality(trace):
    metrics = trace.get("available_metrics") or {}
    missing = [name for name, available in metrics.items() if not available]
    usable = [name for name, available in metrics.items() if available]
    return {
        "usable_metrics": usable,
        "missing_metrics": missing,
        "summary": (
            "HUD inputs, cockpit wheel-display gear, and per-turn timing nodes available; speed OCR unavailable."
            if "turn_entry_exit_gear" in usable
            else "HUD inputs and per-turn timing nodes available; cockpit speed/gear OCR attempted but unavailable."
            if {"steering", "throttle", "brake", "turn_entry_exit_time_nodes"}.issubset(set(usable))
            else "Partial pro-video reference only."
        ),
    }


def build_reference_plan(track):
    if track.lower() != "spa":
        return [
            {
                "section": "Unknown track",
                "review_goal": "Manually identify braking markers, apex timing, and throttle pickup points.",
                "confidence": "manual_required",
            }
        ]
    return [
        {
            "section": code,
            "name": name,
            "review_goal": goal,
            "what_to_extract": [
                "braking marker or visual landmark",
                "turn-in timing",
                "minimum-speed phase",
                "throttle pickup point",
                "exit track usage",
            ],
            "confidence": "visual_reference_only",
        }
        for code, name, goal in SPA_TURN_GROUPS
    ]


def build_manifest(videos, track, car, notes, lap_start_s=None, lap_end_s=None):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUT_DIR / f"pro-video-reference-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for item in videos:
        path = Path(item).expanduser()
        info = media_info(path) if path.exists() else {"available": False, "error": "file not found"}
        visuals = extract_visual_reference(path, out_dir) if path.exists() and info.get("available") else {
            "available": False,
            "error": info.get("error", "media unavailable"),
            "frames": [],
            "contact_sheet": "",
        }
        input_trace = extract_input_trace(path, track, lap_start_s=lap_start_s, lap_end_s=lap_end_s) if path.exists() and info.get("available") else {
            "available": False,
            "error": info.get("error", "media unavailable"),
        }
        entry = {
            "path": str(path),
            "exists": path.exists(),
            "track": track,
            "car_model": car,
            "lap_start_s": lap_start_s,
            "lap_end_s": lap_end_s,
            "media_info": info,
            "media_summary": video_summary(info),
            "visual_reference": visuals,
            "input_trace": input_trace,
            "status": "ready_for_manual_cue_review" if visuals.get("available") else "registered_without_visual_extract",
            "notes": notes or "Use this as a secondary visual reference. Telemetry remains authoritative.",
        }
        entries.append(entry)
    manifest = {
        "schema": "acc_ai_coach_pro_video_reference_v2",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "track": track,
        "car_model": car,
        "entries": entries,
        "reference_plan": build_reference_plan(track),
        "limitations": [
            "Steering, throttle, and brake are estimated from the visible video HUD, not captured from game telemetry.",
            "The first version estimates track position from video time; precise minimap tracking needs a later calibration/OCR pass.",
            "Video reference data is weaker than ACC telemetry, so Rachel treats it as coaching context instead of official evidence.",
        ],
    }
    output = out_dir / "pro_video_manifest.json"
    report = out_dir / "pro_video_report.md"
    pro_reference_path, _ = write_pro_reference(out_dir, manifest)
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_markdown(report, manifest)
    latest = OUT_DIR / "latest_pro_video_reference.json"
    latest.write_text(
        json.dumps(
            {
                "manifest": str(output),
                "report": str(report),
                "pro_reference": str(pro_reference_path),
                "created_at": manifest["created_at"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return output, report, manifest


def write_markdown(output, manifest):
    lines = [
        "# Pro Video Reference",
        "",
        f"Created: {manifest['created_at']}",
        f"Track: {manifest['track']}",
        f"Car: {manifest['car_model'] or 'unspecified'}",
        "",
        "## Purpose",
        "",
        "Use pro-driver MP4 files as secondary visual references for landmarks, line choice, braking references, and throttle-pickup timing. This does not replace ACC telemetry.",
        "",
        "## Videos",
        "",
    ]
    for index, entry in enumerate(manifest["entries"], start=1):
        summary = entry["media_summary"]
        visual = entry["visual_reference"]
        lines.extend([
            f"{index}. `{entry['path']}`",
            f"   Exists: {entry['exists']}",
            f"   Status: {entry['status']}",
            f"   Duration: {summary['duration']}",
            f"   Resolution: {summary['resolution']}",
            f"   Frame rate: {summary['frame_rate']}",
            f"   Codec: {summary['codec']}",
            f"   Extracted frames: {len(visual.get('frames') or [])}",
            f"   Contact sheet: `{visual.get('contact_sheet') or 'not generated'}`",
            f"   Input trace: {'available' if (entry.get('input_trace') or {}).get('available') else 'not available'}",
            "",
        ])
        if visual.get("error"):
            lines.extend([f"   Visual extraction note: {visual['error']}", ""])
        trace = entry.get("input_trace") or {}
        if trace.get("available"):
            metrics = trace.get("available_metrics") or {}
            gear_detection = trace.get("gear_detection") or {}
            speed_detection = trace.get("speed_detection") or {}
            lines.extend([
                "   Detected input summary:",
                f"   - Samples: {len(trace.get('samples') or [])}",
                f"   - Lap segment: {trace.get('lap_segment_start_s')}s to {trace.get('lap_segment_end_s')}s",
                f"   - Position method: {trace.get('layout', {}).get('position_method', 'unknown')}",
                f"   - Steering input: {'available' if metrics.get('steering') else 'unavailable'}",
                f"   - Throttle input: {'available' if metrics.get('throttle') else 'unavailable'}",
                f"   - Brake input: {'available' if metrics.get('brake') else 'unavailable'}",
                f"   - Per-turn time nodes: {'available' if metrics.get('turn_entry_exit_time_nodes') else 'unavailable'}",
                f"   - Pro speed per turn: {'available' if metrics.get('turn_entry_exit_speed') else 'unavailable - cockpit display OCR calibration required'}",
                f"   - Pro gear per turn: {'available' if metrics.get('turn_entry_exit_gear') else 'unavailable - cockpit display reliability check failed'}",
                f"   - Gear detection: {gear_detection.get('reason', 'not attempted')} ({gear_detection.get('sample_count', 0)} samples, unique={gear_detection.get('unique_values', [])})",
                f"   - Speed detection: {speed_detection.get('reason', 'not attempted')}",
                "   - Confidence: estimated from visible HUD pixels",
                "",
            ])
            lines.extend(["   Per-turn pro reference:", ""])
            lines.append("   | Turn group | Pro time node | Duration | Brake start | Brake release | Throttle pickup | Max steer | Speed/Gear |")
            lines.append("   | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
            for zone_name, metric in (trace.get("zones") or {}).items():
                if not metric:
                    continue
                speed_gear = "unavailable"
                if metric.get("estimated_entry_speed_kmh") is not None or metric.get("estimated_entry_gear") is not None:
                    speed_gear = (
                        f"{metric.get('estimated_entry_speed_kmh', '?')}->{metric.get('estimated_exit_speed_kmh', '?')} km/h, "
                        f"G{metric.get('estimated_entry_gear', '?')}->{metric.get('estimated_exit_gear', '?')}"
                    )
                lines.append(
                    "   | "
                    f"{metric.get('turn_label') or zone_name} | "
                    f"{metric.get('entry_timecode')} to {metric.get('exit_timecode')} | "
                    f"{format_duration((metric.get('duration_ms') or 0) / 1000)} | "
                    f"{metric.get('brake_start_distance_m') if metric.get('brake_start_distance_m') is not None else 'n/a'} m | "
                    f"{metric.get('brake_end_distance_m') if metric.get('brake_end_distance_m') is not None else 'n/a'} m | "
                    f"{metric.get('throttle_pickup_distance_m') if metric.get('throttle_pickup_distance_m') is not None else 'n/a'} m | "
                    f"{metric.get('max_abs_steer')} | "
                    f"{speed_gear} |"
                )
            lines.append("")
    lines.extend(["## Spa Review Checklist", ""])
    for item in manifest["reference_plan"]:
        lines.append(f"- {item['section']} {item.get('name', '')}: {item['review_goal']}")
    lines.extend([
        "",
        "## How This Feeds Rachel Later",
        "",
        "- The contact sheet helps inspect the pro driver's visible reference points.",
        "- The input trace estimates pro steering, throttle, and brake from the lower-right HUD region.",
        "- Pro gear and speed are attempted from the cockpit steering-wheel display only when reliability checks pass.",
        "- Rachel can compare the user's ACC telemetry against the pro-video reference by Spa turn group.",
        "",
        "## Limitations",
        "",
    ])
    lines.extend(f"- {item}" for item in manifest["limitations"])
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def latest_report():
    latest = OUT_DIR / "latest_pro_video_reference.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def main():
    parser = argparse.ArgumentParser(description="Analyze pro-driver video files as secondary ACC references.")
    parser.add_argument("videos", nargs="*")
    parser.add_argument("--track", default="Spa")
    parser.add_argument("--car", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--lap-start", type=parse_timecode, default=None, help="Optional start time of the reference lap. Accepts seconds, M:SS, or H:MM:SS.")
    parser.add_argument("--lap-end", type=parse_timecode, default=None, help="Optional end time of the reference lap. Accepts seconds, M:SS, or H:MM:SS.")
    parser.add_argument("--latest", action="store_true", help="Print the latest pro-video reference report path.")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.latest:
        latest = latest_report()
        if not latest:
            raise SystemExit("No pro video reference has been generated yet.")
        print(f"Latest pro video manifest: {latest['manifest']}")
        print(f"Latest pro video report: {latest['report']}")
        if latest.get("pro_reference"):
            print(f"Latest pro video reference: {latest['pro_reference']}")
        return
    if not args.videos:
        raise SystemExit("Provide at least one MP4/video path.")
    manifest_path, report_path, manifest = build_manifest(args.videos, args.track, args.car, args.notes, args.lap_start, args.lap_end)
    print(f"Pro video manifest: {manifest_path}")
    print(f"Pro video report: {report_path}")
    latest = latest_report() or {}
    if latest.get("pro_reference"):
        print(f"Pro video data reference: {latest['pro_reference']}")
    for entry in manifest["entries"]:
        summary = entry["media_summary"]
        visual = entry["visual_reference"]
        trace = entry.get("input_trace") or {}
        print(
            f"{entry['path']}: exists={entry['exists']} status={entry['status']} "
            f"duration={summary['duration']} resolution={summary['resolution']} "
            f"frames={len(visual.get('frames') or [])} input_trace={'yes' if trace.get('available') else 'no'}"
        )


if __name__ == "__main__":
    main()
