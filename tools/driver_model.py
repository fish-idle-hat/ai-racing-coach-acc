#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path

from reference_compare import (
    ROOT,
    completed_lap_segments,
    first_non_empty,
    lap_summary,
    load_rows,
    load_track_map,
    major_incident_findings,
    slug,
    zone_label,
)


PROFILES_DIR = ROOT / "data" / "driver_profiles"
HABIT_MIN_OPPORTUNITIES = 6
HABIT_MIN_COUNT = 3
HABIT_MIN_FREQUENCY = 0.25


WHY_MAP = {
    "official_invalid_trigger": "ACC's official lap-validity flag changed to invalid in this section, so this is treated as the main lap-losing event even if speed, damage, or tyre-out evidence is not dramatic.",
    "major_speed_loss": "The car lost abnormal speed in a section that should remain faster, so this is treated before fine lap-time details.",
    "major_corner_push": "Minimum speed was very low while steering was high, which points to a major push, recovery, or rotation problem.",
    "major_stop_recovery": "Speed collapsed to near zero, so this is a control/recovery event rather than normal corner technique.",
    "early_throttle": "Throttle came in earlier than the reference, but the car did not gain enough from it; this often means throttle was added before rotation was complete.",
    "late_throttle": "Throttle pickup was later than the reference, costing exit drive.",
    "low_min_speed": "Apex/minimum speed was below the reference or your previous best pass.",
    "low_exit_speed": "The car left the corner slower than the reference, so the loss likely came from rotation or throttle timing before exit.",
    "braked_early": "Brake start was earlier than reference, giving away distance before the corner.",
    "less_trail_brake": "Brake release was shorter than reference, which can reduce rotation into the apex.",
    "longer_brake": "Brake was held longer than reference, which can over-slow the car.",
    "throttle_with_steering": "Throttle was applied while steering load was still high, which can create understeer/push.",
    "pedal_overlap": "Brake and throttle overlapped, which can confuse weight transfer and delay rotation.",
    "line_difference": "World-position trace differed from the reference line enough to affect corner speed or exit.",
}


FIX_MAP = {
    "official_invalid_trigger": "Leave a small safety margin, keep the car fully inside track limits, and only push again after the car is settled.",
    "major_speed_loss": "First make the lap valid and stable. Lift earlier if needed, reduce correction, and do not chase throttle until the car is inside track limits.",
    "major_corner_push": "Brake straighter, release once, wait for rotation, then add throttle only as the wheel opens.",
    "major_stop_recovery": "Do not chase lap time here. Keep the car on track, reduce steering correction, and rebuild speed after the car is straight.",
    "early_throttle": "Delay throttle slightly, let the car rotate, then build throttle progressively instead of forcing exit.",
    "late_throttle": "Once rotation is complete, start throttle earlier and build it smoothly.",
    "low_min_speed": "Carry a little more speed only if the car is rotating; do not solve it by adding early throttle.",
    "low_exit_speed": "Prioritize exit shape: rotate before throttle and open the steering earlier.",
    "braked_early": "Move the brake point slightly later while keeping the same brake release shape.",
    "less_trail_brake": "Keep light brake pressure longer toward turn-in to help rotation.",
    "longer_brake": "Release the brake earlier and let the car roll more freely to apex.",
    "throttle_with_steering": "Wait until steering starts to unwind before committing throttle.",
    "pedal_overlap": "Separate the pedals: finish brake release before throttle build.",
    "line_difference": "Use the reference line as a target, but prioritize a valid lap over matching the trace.",
}


def issue_priority(issue):
    source = issue.get("source")
    reason = issue.get("reason")
    severity = float(issue.get("severity") or 0)
    if source == "major_incident" or str(reason).startswith("major_"):
        return 1000 + severity
    if reason in {"early_throttle", "late_throttle", "low_exit_speed", "low_min_speed", "braked_early"}:
        return 100 + severity
    return severity


def classification_for_frequency(count=0, opportunities=0):
    count = int(count or 0)
    opportunities = int(opportunities or 0)
    frequency = count / opportunities if opportunities > 0 else 0.0
    if (
        count >= HABIT_MIN_COUNT
        and opportunities >= HABIT_MIN_OPPORTUNITIES
        and frequency >= HABIT_MIN_FREQUENCY
    ):
        return "habit"
    if count >= 2:
        return "recurring_watchlist"
    return "one_time_watchlist"


def habit_frequency_text(habit):
    count = int(habit.get("count") or 0)
    opportunities = int(habit.get("opportunities") or 0)
    if opportunities <= 0:
        return f"Seen {count} time(s)."
    frequency = count / opportunities
    return f"Seen {count}/{opportunities} analyzed lap(s), {frequency:.0%} frequency."


def enrich_issue(issue, habit_count=0, opportunities=0):
    enriched = dict(issue)
    reason = enriched.get("reason", "unknown")
    classification = classification_for_frequency(habit_count, opportunities)
    frequency = habit_count / opportunities if opportunities else 0.0
    enriched.setdefault("why", WHY_MAP.get(reason, "This issue was detected from telemetry/reference comparison, but the cause is not specific enough yet."))
    enriched.setdefault("how_to_fix", FIX_MAP.get(reason, "Repeat the next lap cleanly and collect more data before changing technique."))
    enriched.setdefault("classification", classification)
    enriched.setdefault("frequency", round(frequency, 4))
    enriched.setdefault("opportunities", int(opportunities or 0))
    enriched.setdefault("priority_score", round(issue_priority(enriched) + (35 * frequency if classification == "habit" else 0), 3))
    return enriched


def profile_path(driver_name):
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR / f"{slug(driver_name or 'driver')}.json"


def default_profile(driver_name):
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "schema": "acc_ai_coach_driver_profile_v1",
        "driver_name": driver_name or "driver",
        "created_at": now,
        "updated_at": now,
        "sessions": [],
        "habits": {},
        "total_analyzed_laps": 0,
        "current_focus": None,
        "current_watchlist": None,
    }


def load_profile(driver_name):
    path = profile_path(driver_name)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default_profile(driver_name)
    return default_profile(driver_name)


def ensure_frequency_fields(habit):
    if "opportunities" not in habit:
        habit["legacy_count_before_frequency_model"] = int(habit.get("count") or 0)
        habit["count"] = 0
        habit["opportunities"] = 0
    habit.setdefault("frequency", 0.0)
    habit.setdefault("classification", classification_for_frequency(habit.get("count", 0), habit.get("opportunities", 0)))
    return habit


def live_issue_counts(profile, track=None, car_model=None):
    reason_aliases = {
        "throttle_with_steering": "understeer",
        "late_throttle": "no_throttle_pickup",
    }
    counts = {}
    for habit in (profile or {}).get("habits", {}).values():
        if track and habit.get("track") not in (None, "", track):
            continue
        if car_model and habit.get("car_model") not in (None, "", car_model):
            continue
        zone = habit.get("zone")
        reason = habit.get("reason")
        if not zone or not reason:
            continue
        count = int(habit.get("count") or 0)
        for mapped_reason in {reason, reason_aliases.get(reason, reason)}:
            key = f"{zone}::{mapped_reason}"
            counts[key] = max(counts.get(key, 0), count)
    return counts


def live_issue_stats(profile, track=None, car_model=None):
    reason_aliases = {
        "throttle_with_steering": "understeer",
        "late_throttle": "no_throttle_pickup",
    }
    stats = {}
    for habit in (profile or {}).get("habits", {}).values():
        ensure_frequency_fields(habit)
        if track and habit.get("track") not in (None, "", track):
            continue
        if car_model and habit.get("car_model") not in (None, "", car_model):
            continue
        zone = habit.get("zone")
        reason = habit.get("reason")
        if not zone or not reason:
            continue
        count = int(habit.get("count") or 0)
        opportunities = int(habit.get("opportunities") or 0)
        frequency = count / opportunities if opportunities else 0.0
        for mapped_reason in {reason, reason_aliases.get(reason, reason)}:
            key = f"{zone}::{mapped_reason}"
            existing = stats.get(key)
            candidate = {
                "historical_count": count,
                "historical_opportunities": opportunities,
                "historical_frequency": frequency,
                "classification": classification_for_frequency(count, opportunities),
            }
            if existing is None or candidate["historical_frequency"] > existing["historical_frequency"]:
                stats[key] = candidate
    return stats


def refresh_focus(profile):
    habits = list(profile.get("habits", {}).values())
    for habit in habits:
        ensure_frequency_fields(habit)
        opportunities = int(habit.get("opportunities") or 0)
        count = int(habit.get("count") or 0)
        habit["frequency"] = round(count / opportunities, 4) if opportunities else 0.0
        habit["classification"] = classification_for_frequency(count, opportunities)
    habits.sort(
        key=lambda item: (
            item.get("classification") == "habit",
            float(item.get("frequency") or 0.0),
            float(item.get("severity_total") or 0.0),
            int(item.get("count") or 0),
        ),
        reverse=True,
    )
    repeated = [habit for habit in habits if habit.get("classification") == "habit"]
    profile["current_focus"] = repeated[0] if repeated else None
    profile["current_watchlist"] = habits[0] if habits else None
    return profile


def comparison_issues(comparison_path):
    if not comparison_path or not Path(comparison_path).exists():
        return []
    comparison = json.loads(Path(comparison_path).read_text(encoding="utf-8"))
    issues = []
    for incident in comparison.get("major_incidents", []):
        issues.append(
            {
                "zone": incident["zone"],
                "zone_label": incident.get("zone_label") or zone_label(incident["zone"]),
                "reason": incident["reason"],
                "severity": max(80.0, float(incident.get("score") or 80)),
                "text": incident["text"],
                "source": "major_incident",
                "lap_number": incident.get("lap_number"),
            }
        )
    for lap in comparison.get("comparisons", []):
        for finding in lap.get("top_findings", [])[:5]:
            issues.append(
                {
                    "zone": finding["zone"],
                    "zone_label": zone_label(finding["zone"]),
                    "reason": finding["reason"],
                    "severity": float(finding.get("score") or 1),
                    "text": f"{zone_label(finding['zone'])}: {finding['text']}",
                    "source": "reference_delta",
                    "lap_number": lap.get("lap_number"),
                }
            )
    return issues


def inferred_issues(run_dir):
    rows = load_rows(run_dir)
    track = first_non_empty(rows, "track") or "Spa"
    track_map = load_track_map(track)
    if track_map is None:
        return []
    laps = [lap_summary(segment, rows) for segment in completed_lap_segments(rows)]
    return [
        {
            "zone": item["zone"],
            "zone_label": item.get("zone_label") or zone_label(item["zone"]),
            "reason": item["reason"],
            "severity": max(80.0, float(item.get("score") or 80)),
            "text": item["text"],
            "source": "major_incident",
            "lap_number": item.get("lap_number"),
        }
        for item in major_incident_findings(laps, rows, track_map)
    ]


def update_profile(run_dir, comparison_path=None):
    run_dir = Path(run_dir)
    rows = load_rows(run_dir)
    driver = first_non_empty(rows, "player_name") or "driver"
    track = first_non_empty(rows, "track") or "unknown"
    car = first_non_empty(rows, "car_model") or "unknown"
    profile = load_profile(driver)
    now = datetime.now().isoformat(timespec="seconds")
    run_key = str(run_dir)

    for session in profile.get("sessions", []):
        if session.get("run") == run_key:
            profile = refresh_focus(profile)
            path = profile_path(driver)
            path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
            md_path = run_dir / "driver_profile_update.md"
            md_path.write_text(markdown_summary(profile, session), encoding="utf-8")
            return path, md_path, profile

    issues = comparison_issues(comparison_path)
    if not issues:
        issues = inferred_issues(run_dir)
    analyzed_laps = max(1, len(completed_lap_segments(rows)))

    session = {
        "run": run_key,
        "created_at": now,
        "track": track,
        "car_model": car,
        "analyzed_laps": analyzed_laps,
        "issue_count": len(issues),
        "top_issues": issues[:5],
    }
    profile["sessions"].append(session)
    profile["sessions"] = profile["sessions"][-30:]

    aggregated = {}
    for issue in issues:
        key = f"{slug(track)}::{slug(car)}::{slug(issue['zone'])}::{slug(issue['reason'])}"
        entry = aggregated.setdefault(key, {"issue": issue, "occurrences": 0, "severity_total": 0.0})
        entry["occurrences"] += 1
        entry["severity_total"] += float(issue.get("severity") or 0.0)
        if issue_priority(issue) > issue_priority(entry["issue"]):
            entry["issue"] = issue

    enriched_by_key = {}
    for key, aggregate in aggregated.items():
        issue = aggregate["issue"]
        habit = profile["habits"].setdefault(
            key,
            {
                "track": track,
                "car_model": car,
                "zone": issue["zone"],
                "zone_label": issue["zone_label"],
                "reason": issue["reason"],
                "count": 0,
                "opportunities": 0,
                "frequency": 0.0,
                "classification": "one_time_watchlist",
                "severity_total": 0.0,
                "last_seen_at": None,
                "last_seen_run": None,
                "latest_text": "",
            },
        )
        ensure_frequency_fields(habit)
        habit["count"] += aggregate["occurrences"]
        habit["opportunities"] = int(habit.get("opportunities") or 0) + analyzed_laps
        habit["frequency"] = round(habit["count"] / habit["opportunities"], 4) if habit["opportunities"] else 0.0
        habit["classification"] = classification_for_frequency(habit["count"], habit["opportunities"])
        habit["severity_total"] = round(float(habit["severity_total"]) + aggregate["severity_total"], 3)
        habit["last_seen_at"] = now
        habit["last_seen_run"] = str(run_dir)
        habit["latest_text"] = issue["text"]
        enriched = enrich_issue(issue, habit["count"], habit["opportunities"])
        enriched["session_occurrences"] = aggregate["occurrences"]
        existing = enriched_by_key.get(key)
        if existing is None or enriched["priority_score"] > existing["priority_score"]:
            enriched_by_key[key] = enriched

    enriched_issues = list(enriched_by_key.values())
    enriched_issues.sort(key=lambda item: item["priority_score"], reverse=True)
    session["prioritized_issues"] = enriched_issues[:8]
    session["ignored_small_issues"] = [
        issue for issue in enriched_issues[8:] if issue["priority_score"] < 120
    ][:8]

    profile = refresh_focus(profile)
    profile["total_analyzed_laps"] = sum(int(session.get("analyzed_laps") or 0) for session in profile.get("sessions", []))
    profile["updated_at"] = now

    path = profile_path(driver)
    path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    md_path = run_dir / "driver_profile_update.md"
    md_path.write_text(markdown_summary(profile, session), encoding="utf-8")
    return path, md_path, profile


def markdown_summary(profile, session):
    focus = profile.get("current_focus")
    watchlist = profile.get("current_watchlist")
    prioritized = session.get("prioritized_issues") or session.get("top_issues", [])
    ignored = session.get("ignored_small_issues") or []
    lines = [
        "# Driver Profile Update",
        "",
        f"Driver: {profile['driver_name']}",
        f"Run: `{session['run']}`",
        f"Track: {session['track']}",
        f"Car: {session['car_model']}",
        f"Issues added this session: {session['issue_count']}",
        "",
        "## Current Focus",
        "",
    ]
    if focus:
        lines.extend(
            [
                f"{focus['zone_label']} - {focus['reason']}",
                "",
                habit_frequency_text(focus),
                "",
                focus["latest_text"],
            ]
        )
    else:
        lines.append("No recurring issue tracked yet.")
        if watchlist:
            lines.extend(
                [
                    "",
                    "## Watchlist",
                    "",
                    f"{watchlist['zone_label']} - {watchlist['reason']}",
                    "",
                    f"{habit_frequency_text(watchlist)} Do not treat as a confirmed habit unless its frequency stays high.",
                    "",
                    watchlist["latest_text"],
                ]
            )
    lines.extend(["", "## This Session Priority", ""])
    if prioritized:
        for index, issue in enumerate(prioritized[:5], start=1):
            enriched = enrich_issue(issue, issue.get("session_occurrences", 0), session.get("analyzed_laps", 0))
            lines.extend(
                [
                    f"{index}. {issue['zone_label']} - {issue['reason']} ({issue.get('classification') or enriched['classification']})",
                    f"   Why: {issue.get('why') or enriched['why']}",
                    f"   Next update: {issue.get('how_to_fix') or enriched['how_to_fix']}",
                    f"   Frequency: {issue.get('session_occurrences', 1)}/{session.get('analyzed_laps', 1)} this session; {issue.get('frequency', 0):.0%} lifetime for this issue.",
                    "",
                ]
            )
    else:
        lines.append("No priority issue detected.")
    lines.extend(["## Temporarily Ignore", ""])
    if ignored:
        for issue in ignored[:5]:
            lines.append(f"- {issue['zone_label']} - {issue['reason']}: lower priority this session.")
    else:
        lines.append("No lower-priority issues were suppressed.")
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description="Update persistent AI Racing Coach - ACC driver profile from a run.")
    parser.add_argument("run")
    parser.add_argument("--comparison-json", default=None)
    args = parser.parse_args()
    path, md_path, profile = update_profile(args.run, args.comparison_json)
    print(f"Driver profile: {path}")
    print(f"Update report: {md_path}")
    if profile.get("current_focus"):
        focus = profile["current_focus"]
        print(f"Current focus: {focus['zone_label']} - {focus['reason']} ({focus['count']}x)")
    elif profile.get("current_watchlist"):
        watch = profile["current_watchlist"]
        print(f"Watchlist: {watch['zone_label']} - {watch['reason']} ({watch['count']}x)")
    else:
        print("Current focus: none")


if __name__ == "__main__":
    main()
