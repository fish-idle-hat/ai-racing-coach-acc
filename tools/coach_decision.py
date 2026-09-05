#!/usr/bin/env python3
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path

from driver_model import FIX_MAP, WHY_MAP
from reference_compare import zone_label


MAJOR_THRESHOLD = 80.0


def issue_to_dict(issue):
    if issue is None:
        return None
    if is_dataclass(issue):
        return asdict(issue)
    return dict(issue)


def issue_key(issue):
    return f"{issue.zone_name}::{issue.reason_key}"


def issue_priority(issue, prior_count=0, frequency=0.0):
    severity = float(issue.severity or 0.0)
    summary = issue.lap_summary or ""
    evidence_bonus = 90.0 if "evidence:" in summary else 0.0
    uncertainty_penalty = 70.0 if "no direct" in summary or "telemetry cannot confirm" in summary else 0.0
    official_bonus = 150.0 if issue.reason_key == "official_invalid_trigger" else 0.0
    frequency_bonus = min(40.0, max(0.0, float(frequency or 0.0)) * 100.0)
    if severity >= MAJOR_THRESHOLD:
        return 1000.0 + severity + evidence_bonus + official_bonus - uncertainty_penalty + frequency_bonus + min(25.0, prior_count * 8.0)
    if issue.reason_key in {"official_invalid_trigger", "major_speed_loss", "major_corner_push", "major_stop_recovery"}:
        return 1000.0 + severity + evidence_bonus + official_bonus - uncertainty_penalty + frequency_bonus
    if issue.reason_key == "pro_video_delta":
        detail = getattr(issue, "detail", {}) or {}
        time_loss = float(detail.get("time_loss_s") or 0.0)
        return 500.0 + severity + min(390.0, max(0.0, time_loss * 70.0))
    if issue.reason_key in {"understeer", "late_throttle", "low_min_speed", "braked_early", "no_throttle_pickup"}:
        return 100.0 + severity + frequency_bonus + min(20.0, prior_count * 5.0)
    return severity + frequency_bonus + min(10.0, prior_count * 3.0)


def classify_pattern(prior_count, stats=None):
    stats = stats or {}
    classification = stats.get("classification")
    if classification == "habit":
        return "habit"
    session_count = int(stats.get("session_count") or 0)
    if session_count >= 1:
        return "repeated_this_session"
    if prior_count == 1:
        return "seen_before"
    if prior_count >= 2:
        return "recurring_watchlist"
    return "one_time_watchlist"


def pattern_voice_message(pattern, stats=None):
    stats = stats or {}
    if pattern == "habit":
        opportunities = int(stats.get("historical_opportunities") or 0)
        count = int(stats.get("historical_count") or 0)
        frequency = float(stats.get("historical_frequency") or 0.0)
        if opportunities > 0:
            return f"This is a high-frequency pattern: {count} of {opportunities} analyzed laps, about {frequency:.0%}."
        return "This is a high-frequency pattern."
    if pattern == "repeated_this_session":
        return "This happened again in this session."
    if pattern == "seen_before":
        return "I have seen this once before, but I am not treating it as a habit yet."
    if pattern == "recurring_watchlist":
        return "This is on the watchlist, but its frequency is not high enough to call it a habit."
    return None


def issue_reason_key(issue):
    mapping = {
        "understeer": "throttle_with_steering",
        "no_throttle_pickup": "late_throttle",
    }
    return mapping.get(issue.reason_key, issue.reason_key)


def why_text(issue):
    return WHY_MAP.get(
        issue_reason_key(issue),
        "The coach selected this because it had the strongest telemetry evidence or highest lap impact in this lap.",
    )


def fix_text(issue):
    if issue.upcoming_hint:
        return issue.upcoming_hint
    return FIX_MAP.get(
        issue_reason_key(issue),
        "Repeat the next lap cleanly and collect more data before changing technique.",
    )


def build_lap_decision(
    *,
    lap_label,
    zone_count,
    issues,
    issue_counts,
    issue_stats=None,
    official_invalid=False,
    validity_seen=False,
    incomplete=False,
):
    scored = []
    issue_stats = issue_stats or {}
    for issue in issues:
        key = issue_key(issue)
        stats = issue_stats.get(key, {})
        prior_count = int(issue_counts.get(key, 0))
        frequency = float(stats.get("historical_frequency") or 0.0)
        scored.append(
            {
                "issue": issue,
                "prior_count": prior_count,
                "stats": stats,
                "priority": issue_priority(issue, prior_count, frequency),
            }
        )
    scored.sort(key=lambda item: item["priority"], reverse=True)

    primary = scored[0] if scored else None
    secondary_major = [
        item
        for item in scored[1:]
        if item["issue"].severity >= MAJOR_THRESHOLD
        or item["issue"].reason_key in {"official_invalid_trigger", "major_speed_loss", "major_corner_push", "major_stop_recovery"}
    ][:2] if primary else []
    ignored = [
        item
        for item in scored[1:]
        if item not in secondary_major
        and (item["issue"].severity < MAJOR_THRESHOLD or item["priority"] < primary["priority"] * 0.75)
    ][:4] if primary else []

    title = (
        f"Final incomplete lap review: {zone_count} Spa zones reviewed."
        if incomplete
        else f"Lap {lap_label} complete: {zone_count} Spa zones reviewed."
    )
    detail_messages = [title]
    messages = [title]
    if official_invalid:
        detail_messages.append("ACC marked this lap invalid.")
        messages.append("ACC marked this lap invalid.")
    elif validity_seen:
        validity_message = "ACC marked this lap valid so far." if incomplete else "ACC marked this lap valid."
        detail_messages.append(validity_message)

    decision = {
        "lap_label": lap_label,
        "zone_count": zone_count,
        "official_invalid": official_invalid,
        "validity_seen": validity_seen,
        "incomplete": incomplete,
        "primary_issue": issue_to_dict(primary["issue"]) if primary else None,
        "secondary_major_issues": [issue_to_dict(item["issue"]) for item in secondary_major],
        "ignored_issues": [issue_to_dict(item["issue"]) for item in ignored],
        "messages": messages,
    }

    if primary:
        issue = primary["issue"]
        prior_count = primary["prior_count"]
        stats = primary.get("stats") or {}
        pattern = classify_pattern(prior_count, stats)
        heading = "Main issue detected this lap:" if issue.severity >= MAJOR_THRESHOLD else "Main improvement target this lap:"
        detail_messages.append(heading)
        detail_messages.append(issue.lap_summary)
        detail_messages.append(f"Why: {why_text(issue)}")
        detail_messages.append(f"Pattern: {pattern.replace('_', ' ')}.")
        if stats.get("historical_opportunities"):
            detail_messages.append(
                f"Frequency: {int(stats.get('historical_count') or 0)}/{int(stats.get('historical_opportunities') or 0)} analyzed laps."
            )
        detail_messages.append(f"Next correction: {fix_text(issue)}")
        messages.append(issue.lap_summary)
        pattern_message = pattern_voice_message(pattern, stats)
        if pattern_message:
            messages.append(pattern_message)
        if secondary_major:
            detail_messages.append("Other major events from the same lap:")
            for item in secondary_major:
                other = item["issue"]
                detail_messages.append(other.lap_summary)
                messages.append(f"Also this lap: {other.lap_summary}")
        messages.append(f"Next correction: {fix_text(issue)}")
        if ignored:
            ignored_labels = ", ".join(
                f"{zone_label(item['issue'].zone_name)} {item['issue'].reason_key}" for item in ignored[:3]
            )
            detail_messages.append(f"Ignoring for now: {ignored_labels}.")
        decision["pattern"] = pattern
        decision["priority_score"] = round(primary["priority"], 3)
        decision["prior_count"] = prior_count
        decision["pattern_stats"] = stats
    else:
        no_issue = "No priority issue detected. Repeat the rhythm and collect another clean lap."
        detail_messages.append(no_issue)
        messages.append(no_issue)
        decision["pattern"] = None
        decision["priority_score"] = 0.0
        decision["prior_count"] = 0

    decision["messages"] = messages
    decision["detail_messages"] = detail_messages
    return decision


def build_ai_context(decisions):
    return {
        "schema": "acc_ai_coach_context_v1",
        "purpose": "Stable input for a future LLM phrasing/reasoning layer. Telemetry-derived classifications remain authoritative.",
        "guardrails": [
            "Do not invent incidents not present in primary_issue or ignored_issues.",
            "Do not override ACC official lap validity.",
            "Use one coaching focus per upcoming lap.",
            "Prefer concise, low-distraction voice phrasing while driving.",
        ],
        "decisions": decisions,
    }


def decision_report_markdown(decisions):
    lines = ["# Coach Decision Report", ""]
    if not decisions:
        lines.append("No lap decisions were generated.")
        return "\n".join(lines) + "\n"
    for index, decision in enumerate(decisions, start=1):
        label = decision.get("lap_label")
        suffix = " incomplete" if decision.get("incomplete") else ""
        lines.extend([f"## Decision {index} - Lap {label}{suffix}", ""])
        for message in decision.get("detail_messages") or decision.get("messages") or []:
            lines.append(f"- {message}")
        primary = decision.get("primary_issue")
        if primary:
            lines.extend(
                [
                    "",
                    f"Priority score: {decision.get('priority_score')}",
                    f"Pattern: {decision.get('pattern')}",
                    f"Prior count: {decision.get('prior_count', 0)}",
                    f"Primary issue: `{primary.get('zone_name')}::{primary.get('reason_key')}`",
                ]
            )
        ignored = decision.get("ignored_issues") or []
        if ignored:
            lines.extend(["", "Ignored lower-priority issues:"])
            for issue in ignored:
                lines.append(f"- {zone_label(issue.get('zone_name'))}: {issue.get('reason_key')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_decision_artifacts(run_dir, decisions):
    run_dir = Path(run_dir)
    decisions_path = run_dir / "coach_decisions.json"
    ai_context_path = run_dir / "coach_ai_context.json"
    report_path = run_dir / "coach_decision_report.md"
    decisions_path.write_text(json.dumps(decisions, indent=2), encoding="utf-8")
    ai_context_path.write_text(json.dumps(build_ai_context(decisions), indent=2), encoding="utf-8")
    report_path.write_text(decision_report_markdown(decisions), encoding="utf-8")
    return decisions_path, ai_context_path, report_path
