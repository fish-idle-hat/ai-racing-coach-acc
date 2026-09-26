#!/usr/bin/env python3
import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from reference_compare import completed_lap_segments, lap_summary, load_rows

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def first_track(summary, reference):
    tracks = summary.get("tracks_seen") or []
    if tracks:
        return tracks[0]
    return reference.get("track") or "Unknown"


def lap_stats(reference, run_dir):
    laps = reference.get("laps_detected") or []
    if not laps and (run_dir / "telemetry.csv").exists():
        rows = load_rows(run_dir)
        laps = [lap_summary(segment, rows) for segment in completed_lap_segments(rows)]
    completed = [lap for lap in laps if lap.get("completed_by_position_wrap")]
    valid = [lap for lap in completed if lap.get("valid_for_reference")]
    invalid = [lap for lap in completed if not lap.get("valid_for_reference")]
    incomplete = [lap for lap in laps if not lap.get("completed_by_position_wrap")]
    best = min((lap.get("lap_time_ms") for lap in valid if lap.get("lap_time_ms")), default=None)
    return {
        "completed": len(completed),
        "valid": len(valid),
        "invalid": len(invalid),
        "incomplete": len(incomplete),
        "best_lap_ms": best,
        "best_lap_display": format_lap_time(best) if best else "n/a",
    }


def format_lap_time(ms):
    total = int(ms)
    minutes, remainder = divmod(total, 60000)
    second, millis = divmod(remainder, 1000)
    return f"{minutes}:{second:02d}.{millis:03d}"


def collect_issues(decisions, reference):
    issues = []
    for decision in decisions:
        primary = decision.get("primary_issue") or {}
        if primary:
            issues.append({
                "zone": primary.get("zone_name", "Unknown"),
                "reason": primary.get("reason_key", "unknown"),
                "severity": float(primary.get("severity") or 0),
                "text": primary.get("lap_summary") or "",
                "hint": primary.get("upcoming_hint") or "",
                "lap": decision.get("lap_label"),
                "source": "coach",
            })
        for issue in decision.get("ignored_issues") or []:
            issues.append({
                "zone": issue.get("zone_name", "Unknown"),
                "reason": issue.get("reason_key", "unknown"),
                "severity": float(issue.get("severity") or 0) * 0.55,
                "text": issue.get("lap_summary") or "",
                "hint": issue.get("upcoming_hint") or "",
                "lap": decision.get("lap_label"),
                "source": "secondary",
            })

    for incident in reference.get("major_incidents") or []:
        issues.append({
            "zone": incident.get("zone", "Unknown"),
            "reason": incident.get("reason", "major_incident"),
            "severity": float(incident.get("score") or 0) / 25,
            "text": incident.get("text") or incident.get("summary") or "",
            "hint": "Prioritize keeping the car valid and stable before chasing time.",
            "lap": incident.get("lap_number"),
            "source": "incident",
        })

    return issues


def score_categories(summary, lap_info, issues):
    packets_ok = 1 if summary.get("packet_count", 0) > 0 else 0
    timing_health = summary.get("receiver_timing_health") or {}
    telemetry_score = 96 if packets_ok and timing_health.get("intervals_over_0_5s", 0) == 0 else 74

    completed = max(1, lap_info["completed"])
    valid_ratio = lap_info["valid"] / completed
    consistency = clamp(45 + valid_ratio * 45 - lap_info["invalid"] * 7)

    issue_by_reason = Counter(issue["reason"] for issue in issues)
    understeer_count = issue_by_reason.get("understeer", 0)
    pedal_count = issue_by_reason.get("pedal_overlap", 0)
    incident_count = sum(1 for issue in issues if issue["source"] == "incident")

    braking = clamp(88 - issue_by_reason.get("brake_too_early", 0) * 10 - pedal_count * 4 - incident_count * 8)
    rotation = clamp(86 - understeer_count * 5 - incident_count * 9)
    throttle = clamp(84 - issue_by_reason.get("late_throttle", 0) * 8 - understeer_count * 3 - pedal_count * 4)
    stability = clamp(90 - incident_count * 18 - lap_info["invalid"] * 12)

    return {
        "Telemetry": round(telemetry_score),
        "Consistency": round(consistency),
        "Braking": round(braking),
        "Rotation": round(rotation),
        "Throttle": round(throttle),
        "Stability": round(stability),
    }


def strongest_weakest(scores):
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return ordered[:2], ordered[-2:]


def prioritized_recommendations(issues, reference):
    grouped = defaultdict(lambda: {"score": 0.0, "count": 0, "examples": [], "hint": ""})
    for issue in issues:
        key = (issue["zone"], issue["reason"])
        grouped[key]["score"] += issue["severity"]
        grouped[key]["count"] += 1
        if issue["text"] and len(grouped[key]["examples"]) < 2:
            grouped[key]["examples"].append(issue["text"])
        if issue["hint"]:
            grouped[key]["hint"] = issue["hint"]

    ranked = sorted(grouped.items(), key=lambda item: (item[1]["score"], item[1]["count"]), reverse=True)
    results = []
    for (zone, reason), data in ranked[:5]:
        label = readable_reason(reason)
        recommendation = recommendation_for(reason, data["hint"])
        results.append({
            "zone": zone,
            "reason": reason,
            "label": label,
            "priority_score": round(data["score"], 1),
            "occurrences": data["count"],
            "recommendation": recommendation,
            "examples": data["examples"],
        })

    if not results:
        recommendation = reference.get("recommendation") or {}
        if recommendation.get("available"):
            results.append({
                "zone": recommendation.get("focus_zone", "Reference focus"),
                "reason": "reference_delta",
                "label": "Reference delta",
                "priority_score": round(float(recommendation.get("score") or 0), 1),
                "occurrences": 1,
                "recommendation": recommendation.get("text", "Review the largest reference delta first."),
                "examples": [recommendation.get("text", "")],
            })
    return results


def readable_reason(reason):
    return {
        "understeer": "Throttle while steering loaded",
        "pedal_overlap": "Brake and throttle overlap",
        "late_throttle": "Late throttle pickup",
        "brake_too_early": "Early braking",
        "major_speed_loss": "Major stability loss",
        "major_stop_recovery": "Stop or recovery",
        "official_invalid_trigger": "Official invalidation",
    }.get(reason, reason.replace("_", " ").title())


def recommendation_for(reason, fallback):
    if reason == "understeer":
        return "Wait for rotation before throttle. Start squeezing power only as steering begins to open."
    if reason == "pedal_overlap":
        return "Separate brake and throttle. Finish the brake release before rebuilding throttle."
    if reason == "late_throttle":
        return "Once the car is rotated, start throttle pickup earlier and build it progressively."
    if reason == "brake_too_early":
        return "Move the brake point a small step later, then keep the initial brake phase straight."
    if reason in {"major_speed_loss", "major_stop_recovery", "official_invalid_trigger"}:
        return "Make the lap valid first. Leave margin, reduce steering correction, and only push again after the car is settled."
    return fallback or "Repeat the cleanest rhythm and collect another valid reference lap."


def radar_svg(scores, path):
    labels = list(scores.keys())
    values = [scores[label] for label in labels]
    cx, cy, radius = 260, 250, 150
    points = []
    grid = []
    for ring in [0.25, 0.5, 0.75, 1.0]:
        ring_points = []
        for index in range(len(labels)):
            angle = -math.pi / 2 + index * 2 * math.pi / len(labels)
            ring_points.append((cx + math.cos(angle) * radius * ring, cy + math.sin(angle) * radius * ring))
        grid.append(ring_points)
    for index, value in enumerate(values):
        angle = -math.pi / 2 + index * 2 * math.pi / len(labels)
        scale = value / 100
        points.append((cx + math.cos(angle) * radius * scale, cy + math.sin(angle) * radius * scale))

    def poly(items):
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in items)

    label_text = []
    for index, label in enumerate(labels):
        angle = -math.pi / 2 + index * 2 * math.pi / len(labels)
        x = cx + math.cos(angle) * (radius + 52)
        y = cy + math.sin(angle) * (radius + 52)
        label_text.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle">{label} {scores[label]}</text>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="520" height="500" viewBox="0 0 520 500">
  <style>
    text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; fill: #f1f5f9; font-size: 14px; font-weight: 650; }}
    .grid {{ fill: none; stroke: rgba(255,255,255,0.16); stroke-width: 1; }}
    .axis {{ stroke: rgba(255,255,255,0.18); stroke-width: 1; }}
    .shape {{ fill: rgba(83, 196, 206, 0.30); stroke: #53c4ce; stroke-width: 3; }}
    .dot {{ fill: #e4b65b; }}
  </style>
  <rect width="520" height="500" rx="22" fill="#2f3338"/>
  {''.join(f'<polygon class="grid" points="{poly(ring)}"/>' for ring in grid)}
  {''.join(f'<line class="axis" x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}"/>' for x, y in grid[-1])}
  <polygon class="shape" points="{poly(points)}"/>
  {''.join(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="4"/>' for x, y in points)}
  {''.join(label_text)}
</svg>
'''
    path.write_text(svg, encoding="utf-8")


def markdown_report(result):
    lines = [
        "# Session Performance Summary",
        "",
        f"Run: `{result['run_name']}`",
        f"Track: {result['track']}",
        f"Driving level: {result['driving_level']}",
        f"Created: {result['created_at']}",
        "",
        f"Overall score: **{result['overall_score']}/100**",
        f"Completed laps: {result['laps']['completed']} | Valid laps: {result['laps']['valid']} | Invalid laps: {result['laps']['invalid']} | Incomplete laps: {result['laps']['incomplete']} | Best lap: {result['laps']['best_lap_display']}",
        "",
        "## Scores",
        "",
    ]
    for name, score in result["scores"].items():
        lines.append(f"- {name}: {score}/100")
    lines.extend(["", "## Strongest Areas", ""])
    for name, score in result["strongest_areas"]:
        lines.append(f"- {name}: {score}/100")
    lines.extend(["", "## Weakest Areas", ""])
    for name, score in result["weakest_areas"]:
        lines.append(f"- {name}: {score}/100")
    lines.extend(["", "## Prioritized Improvement Points", ""])
    if result["recommendations"]:
        for index, item in enumerate(result["recommendations"], start=1):
            lines.append(f"{index}. {item['zone']} - {item['label']}")
            lines.append(f"   Recommendation: {item['recommendation']}")
            for example in item["examples"][:1]:
                if example:
                    lines.append(f"   Session example: {example}")
    else:
        lines.append("No priority issue detected. Collect more clean laps for stronger analysis.")
    lines.extend(["", f"Radar visualization: `{result['radar_svg']}`"])
    return "\n".join(lines).rstrip() + "\n"


def build_summary(run_dir, driving_level="Unknown"):
    summary = read_json(run_dir / "summary.json", {})
    decisions = read_json(run_dir / "coach_decisions.json", [])
    reference = read_json(run_dir / "reference_comparison.json", {})
    track = first_track(summary, reference)
    issues = collect_issues(decisions, reference)
    laps = lap_stats(reference, run_dir)
    scores = score_categories(summary, laps, issues)
    overall = round(sum(scores.values()) / len(scores))
    strongest, weakest = strongest_weakest(scores)
    recommendations = prioritized_recommendations(issues, reference)
    radar_path = run_dir / "performance_radar.svg"
    radar_svg(scores, radar_path)
    result = {
        "schema": "acc_ai_coach_session_summary_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "run_name": run_dir.name,
        "track": track,
        "driving_level": driving_level,
        "overall_score": overall,
        "scores": scores,
        "laps": laps,
        "strongest_areas": strongest,
        "weakest_areas": weakest,
        "recommendations": recommendations,
        "radar_svg": str(radar_path),
    }
    (run_dir / "session_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (run_dir / "session_summary.md").write_text(markdown_report(result), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate a post-session ACC coaching summary.")
    parser.add_argument("run_name")
    parser.add_argument("--level", default="Unknown")
    args = parser.parse_args()
    run_dir = Path(args.run_name)
    if not run_dir.exists():
        run_dir = RUNS_DIR / args.run_name
    if not run_dir.exists():
        raise SystemExit(f"Run not found: {args.run_name}")
    result = build_summary(run_dir, driving_level=args.level)
    print(f"Session summary: {run_dir / 'session_summary.md'}")
    print(f"Radar visualization: {run_dir / 'performance_radar.svg'}")
    print(f"Overall score: {result['overall_score']}/100")


if __name__ == "__main__":
    main()
