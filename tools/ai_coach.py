#!/usr/bin/env python3
import argparse
import json
import os
import re
from pathlib import Path

from coach_decision import build_ai_context


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"


DEFAULT_MODEL = "gpt-5-mini"


SYSTEM_PROMPT = """You are Rachel, a concise ACC driving coach.

Use only the supplied coach decision context. Telemetry-derived decisions are authoritative.
Do not invent new incidents, turn numbers, weather, lap validity, setup problems, car damage, tyre-out events, or spin/contact evidence.
Your job is phrasing and coaching clarity only.

Voice lines must be short enough to speak while driving. Prefer one concrete correction over general advice.
If the context says an issue lacks direct evidence, keep that uncertainty.
Also produce a mistake analyzer, a short training plan, and driver-style notes from the supplied context.
"""


OUTPUT_SCHEMA = {
    "type": "json_schema",
    "name": "acc_ai_coach_output",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "session_summary": {"type": "string"},
            "voice_lines": {
                "type": "array",
                "items": {"type": "string"},
            },
            "next_lap_plan": {"type": "string"},
            "mistake_analyzer": {
                "type": "array",
                "items": {"type": "string"},
            },
            "training_plan": {
                "type": "array",
                "items": {"type": "string"},
            },
            "driver_style_notes": {"type": "string"},
            "level_adjustment": {"type": "string"},
            "do_not_focus_yet": {
                "type": "array",
                "items": {"type": "string"},
            },
            "confidence_notes": {"type": "string"},
        },
        "required": [
            "session_summary",
            "voice_lines",
            "next_lap_plan",
            "mistake_analyzer",
            "training_plan",
            "driver_style_notes",
            "level_adjustment",
            "do_not_focus_yet",
            "confidence_notes",
        ],
    },
}


def resolve_run(run):
    path = Path(run)
    if path.is_dir():
        return path
    candidate = RUNS_DIR / run
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Run not found: {run}")


def load_context(run_dir):
    context_path = run_dir / "coach_ai_context.json"
    decisions_path = run_dir / "coach_decisions.json"
    if context_path.exists():
        return json.loads(context_path.read_text(encoding="utf-8"))
    if decisions_path.exists():
        decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        return build_ai_context(decisions)
    raise SystemExit(
        f"Missing coach AI context. Run replay first: PYTHONPATH=tools python3 tools/replay_coach.py {run_dir.name}"
    )


def fallback_output(context):
    decisions = context.get("decisions") or []
    latest = decisions[-1] if decisions else {}
    primary = latest.get("primary_issue") or {}
    ignored = latest.get("ignored_issues") or []
    messages = latest.get("messages") or []
    voice_lines = [
        message
        for message in messages
        if not message.startswith("Lap ")
        and not message.startswith("Final incomplete")
        and not message.startswith("ACC marked")
    ][:3]
    if not voice_lines and primary:
        voice_lines = [primary.get("lap_summary", "Keep this lap clean and collect one more reference lap.")]

    zone = primary.get("zone_name", "the current focus corner")
    reason = primary.get("reason_key", "current focus")
    issue_text = primary.get("lap_summary") or (messages[-1] if messages else f"{zone}: {reason}")
    return {
        "session_summary": "Local fallback summary generated from the deterministic coach decision layer.",
        "voice_lines": voice_lines,
        "next_lap_plan": primary.get("upcoming_hint") or f"Focus on {zone}: {reason}.",
        "mistake_analyzer": [
            issue_text,
            f"Primary focus: {zone} - {reason}.",
            "Lower-priority details are kept in the deterministic decision report.",
        ],
        "training_plan": [
            primary.get("upcoming_hint") or f"Repeat {zone} with one clean correction.",
            "Collect one stable lap before chasing smaller technique changes.",
            "After the next run, compare whether the same corner remains the primary issue.",
        ],
        "driver_style_notes": "Local mode can track repeated issues and watchlists, but richer style analysis needs more completed runs or OpenAI phrasing.",
        "level_adjustment": "Stay in Beginner mode until laps are mostly valid and the same correction can be repeated cleanly.",
        "do_not_focus_yet": [
            f"{item.get('zone_name')}: {item.get('reason_key')}"
            for item in ignored[:4]
        ],
        "confidence_notes": "No OpenAI API call was made. Set OPENAI_API_KEY and rerun without --dry-run for Rachel's LLM phrasing layer.",
    }


def invalid_key_hint():
    key = os.environ.get("OPENAI_API_KEY") or ""
    if key.startswith("AIza"):
        return "OPENAI_API_KEY looks like a Google API key, not an OpenAI API key."
    if key and not key.startswith(("sk-", "sess-")):
        return "OPENAI_API_KEY does not look like a current OpenAI API key."
    return "OpenAI API call failed; using local fallback output."


def redact_secrets(text):
    if not text:
        return ""
    text = re.sub(r"sk-[A-Za-z0-9_\\-]{8,}", "sk-...REDACTED", str(text))
    text = re.sub(r"sk-proj-[A-Za-z0-9_\\-]{8,}", "sk-proj-...REDACTED", text)
    text = re.sub(r"AIza[A-Za-z0-9_\\-]{8,}", "AIza...REDACTED", text)
    return text


def exception_summary(exc):
    parts = [f"type={type(exc).__name__}"]
    for attr in ("status_code", "code", "type", "param"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={value}")
    message = getattr(exc, "message", None) or str(exc)
    if message:
        parts.append(f"message={redact_secrets(message)}")
    return "; ".join(parts)


def call_openai(context, model):
    from openai import OpenAI

    client = OpenAI()
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Create concise Rachel coaching output from this JSON context. "
                    "Return only JSON matching the schema.\n\n"
                    + json.dumps(context, ensure_ascii=False)
                ),
            },
        ],
        text={"format": OUTPUT_SCHEMA},
    )
    return json.loads(response.output_text)


def write_output(run_dir, output, source, model):
    output_path = run_dir / "ai_coach_output.json"
    md_path = run_dir / "ai_coach_output.md"
    payload = {
        "schema": "acc_ai_coach_llm_output_v1",
        "source": source,
        "model": model,
        "output": output,
    }
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# AI Coach Output",
        "",
        f"Source: {source}",
        f"Model: {model}",
        "",
        "## Session Summary",
        "",
        output["session_summary"],
        "",
        "## Voice Lines",
        "",
    ]
    lines.extend(f"- {line}" for line in output["voice_lines"])
    lines.extend(["", "## Next Lap Plan", "", output["next_lap_plan"], ""])
    lines.extend(["## Mistake Analyzer", ""])
    lines.extend(f"- {line}" for line in output.get("mistake_analyzer", []))
    lines.extend(["", "## Training Plan", ""])
    lines.extend(f"- {line}" for line in output.get("training_plan", []))
    lines.extend(["", "## Driver Style Notes", "", output.get("driver_style_notes", ""), ""])
    lines.extend(["## Level Adjustment", "", output.get("level_adjustment", ""), "", "## Do Not Focus Yet", ""])
    if output["do_not_focus_yet"]:
        lines.extend(f"- {line}" for line in output["do_not_focus_yet"])
    else:
        lines.append("None.")
    lines.extend(["", "## Confidence Notes", "", output["confidence_notes"]])
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path, md_path


def generate_ai_coach_output(run_dir, model=DEFAULT_MODEL, dry_run=False, debug_error=False):
    context = load_context(run_dir)
    if dry_run or not os.environ.get("OPENAI_API_KEY"):
        output = fallback_output(context)
        source = "local_fallback"
    else:
        try:
            output = call_openai(context, model)
            source = "openai_responses_api"
        except Exception as exc:
            output = fallback_output(context)
            if debug_error:
                output["confidence_notes"] = f"{invalid_key_hint()} {exception_summary(exc)}"
            else:
                output["confidence_notes"] = f"{invalid_key_hint()} Error type: {type(exc).__name__}."
            source = "local_fallback_after_openai_error"
    return (*write_output(run_dir, output, source, model), output, source)


def main():
    parser = argparse.ArgumentParser(description="Generate Rachel's AI phrasing from coach_ai_context.json.")
    parser.add_argument("run", help="Run folder or run name")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="Do not call OpenAI; generate local fallback output.")
    parser.add_argument("--debug-error", action="store_true", help="Print sanitized OpenAI error details.")
    args = parser.parse_args()

    run_dir = resolve_run(args.run)
    output_json, output_md, output, source = generate_ai_coach_output(
        run_dir,
        model=args.model,
        dry_run=args.dry_run,
        debug_error=args.debug_error,
    )
    print(f"AI coach JSON: {output_json}")
    print(f"AI coach report: {output_md}")
    print(f"Source: {source}")
    print(f"Confidence: {output['confidence_notes']}")
    for line in output["voice_lines"]:
        print(f"Rachel: {line}")


if __name__ == "__main__":
    main()
