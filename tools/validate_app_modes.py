#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODES = ("Beginner", "Intermediate", "Pro")
EXPECTED_GAPS = {
    "Beginner": "3.0",
    "Intermediate": "2.35",
    "Pro": "2.0",
}


def file_sha256(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def check_source_contract():
    swift = read("mac-app/ACC_AI_Coach.swift")
    realtime = read("tools/realtime_coach.py")
    summary = read("tools/session_summary.py")
    failures = []

    for mode in MODES:
        if f'Text("{mode}").tag("{mode}")' not in swift:
            failures.append(f"Swift LevelPicker missing {mode}")
        if f'title: "{mode}"' not in swift:
            failures.append(f"Swift level card missing {mode}")
        if f'"{mode}"' not in realtime:
            failures.append(f"Realtime coach missing {mode} choice")
        expected_gap = EXPECTED_GAPS[mode]
        if f'"{mode}": {expected_gap}' not in realtime:
            failures.append(f"Realtime coach missing {mode} message gap {expected_gap}")

    required_swift = [
        "--coach-level",
        "selectedMode",
        "--pro-video-reference",
        "session_summary.py",
        "--level",
        "writeRunMetadata",
        "coach_level",
        "driving_level",
    ]
    for token in required_swift:
        if token not in swift:
            failures.append(f"Swift contract missing {token}")

    if 'choices=["Beginner", "Intermediate", "Pro"]' not in realtime:
        failures.append("Realtime CLI coach-level choices are not exactly Beginner/Intermediate/Pro")
    if 'parser.add_argument("--level", default="Unknown")' not in summary:
        failures.append("Session summary does not accept --level")
    if '"driving_level": driving_level' not in summary:
        failures.append("Session summary does not persist driving_level")
    if ".tutorialTarget(.sessionHistory)" not in swift:
        failures.append("Tutorial target for session history is missing")
    if ".tutorialTarget(.curriculum)" not in swift:
        failures.append("Tutorial target for curriculum is missing")
    if "case homeWorkspace" not in swift:
        failures.append("Tutorial target for the full Home workspace is missing")
    if "target: .homeWorkspace" not in swift:
        failures.append("First tutorial step must target the full Home workspace")
    if ".tutorialTarget(.homeWorkspace)" not in swift:
        failures.append("Home workspace is not anchored for the first tutorial step")
    if 'coordinateSpace(name: "TutorialRoot")' not in swift:
        failures.append("Tutorial overlay is missing the shared TutorialRoot coordinate space")
    if "Anchor<CGRect>" in swift:
        failures.append("Tutorial overlay should use concrete CGRect frames, not stale Anchor rectangles")
    if "TutorialPageScrollView" not in swift or "proxy.scrollTo(step.target.rawValue" not in swift:
        failures.append("Tutorial pages must auto-scroll to off-screen tutorial targets")
    summary_match = re.search(r"struct AnalysisSummaryStrip:[\s\S]*?struct SnapshotPill:", swift)
    if summary_match and ".tutorialTarget(.radarPanel)" in summary_match.group(0):
        failures.append("Radar tutorial target must not be anchored to the session summary strip")
    radar_match = re.search(r"struct RadarAnalysisPanel:[\s\S]*?struct ScoreExplanationRow:", swift)
    if radar_match and ".tutorialTarget(.radarPanel)" not in radar_match.group(0):
        failures.append("RadarAnalysisPanel is not anchored to .radarPanel")
    if "Now the tutorial is all complete" not in swift:
        failures.append("Tutorial completion voice message is missing")
    if "tutorialAutoAdvanceWorkItem" not in swift or "scheduleTutorialAutoAdvance" not in swift:
        failures.append("Tutorial auto-advance timer is missing")
    if "tutorialAutoAdvanceDelay" not in swift:
        failures.append("Tutorial auto-advance delay calculation is missing")
    if "tutorialOverlayReady" not in swift or "revealTutorialOverlay" not in swift:
        failures.append("Tutorial overlay must wait until page navigation and scrolling are ready")
    if "TutorialTransitionDimLayer" not in swift:
        failures.append("Tutorial must keep the dim background visible while the next target scrolls into place")
    if "if let step = model.currentTutorialStep, model.tutorialOverlayReady" in swift:
        failures.append("Tutorial overlay must not disappear while waiting for the next target")
    if "selectedPage = step.page" not in swift or "proxy.scrollTo(step.target.rawValue, anchor: .center)" not in swift:
        failures.append("Tutorial highlight, callout, and scroll animations should stay smooth and short")
    if "estimatedSpeechSeconds + 0.05" not in swift:
        failures.append("Tutorial auto-advance gap after Rachel speech should stay short")
    if "revealTutorialOverlay(for: step.id, after: 0.035)" not in swift:
        failures.append("Tutorial reveal delay should stay short after scroll")
    if ".id(model.selectedPage)" not in swift or "offset(x: 8, y: 0)" not in swift:
        failures.append("Detail pages need a lightweight cross-page transition during tutorial navigation")
    if ".onChange(of: model.selectedPage)" in swift:
        failures.append("Tutorial page scroll should not listen to selectedPage; new page onAppear already scrolls and avoids duplicate jumps")
    realtime = (ROOT / "tools" / "realtime_coach.py").read_text(encoding="utf-8")
    if "sanitize_directional_distance_text" not in realtime or "NEGATIVE_DISTANCE_DIRECTION_RE" not in realtime:
        failures.append("Realtime coach must sanitize negative directional distance phrasing before Rachel speaks")
    if "return sanitize_directional_distance_text(text)" not in realtime:
        failures.append("Voice compact output must also sanitize negative directional distance phrasing")
    if ".scale(scale:" in swift:
        failures.append("Tutorial callout transition should avoid scale animation because it feels jumpy")
    if "clampedRect" not in swift:
        failures.append("Tutorial highlight rectangles must be clamped to the visible viewport")
    if "calloutRect(center:" not in swift or "intersectionArea" not in swift or "targetWithBreathingRoom" not in swift:
        failures.append("Tutorial callout must choose a position that avoids covering the highlighted target")
    if "safeRect.maxY - size.height / 2" not in swift or "fixedSize(horizontal: true, vertical: false)" not in swift:
        failures.append("First tutorial callout must sit bottom-left and keep tutorial button labels on one line")
    if "HomeView()\n                            .tutorialTarget(.homeWorkspace)" in swift:
        failures.append("Home workspace target must be anchored to Home content, not the outer detail container")
    metric_tile_match = re.search(r"struct MetricTile:[\s\S]*?struct ActionButtonStyle:", swift)
    if metric_tile_match and ".tutorialTarget(" in metric_tile_match.group(0):
        failures.append("MetricTile must not declare tutorialTarget; it creates unstable duplicate anchors")
    session_history_match = re.search(r"struct SessionHistoryPanel:[\s\S]*?struct SessionCard:", swift)
    if session_history_match and ".tutorialTarget(.sessionHistory)" not in session_history_match.group(0):
        failures.append("SessionHistoryPanel is not anchored to .sessionHistory")
    curriculum_match = re.search(r"struct CurriculumPanel:[\s\S]*?struct CurriculumStep:", swift)
    if curriculum_match and ".tutorialTarget(.curriculum)" not in curriculum_match.group(0):
        failures.append("CurriculumPanel is not anchored to .curriculum")

    return failures


def check_runtime_tools(run_name):
    failures = []
    run_dir = ROOT / "runs" / run_name
    generated_files = [
        run_dir / "session_summary.json",
        run_dir / "session_summary.md",
        run_dir / "performance_radar.svg",
    ]
    backups = {}
    for path in generated_files:
        backups[path] = path.read_bytes() if path.exists() else None

    for mode in MODES:
        result = subprocess.run(
            [sys.executable, "-u", str(ROOT / "tools" / "session_summary.py"), run_name, "--level", mode],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(f"session_summary failed for {mode}: {result.stderr or result.stdout}")
            continue
        summary_path = ROOT / "runs" / run_name / "session_summary.json"
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"session_summary JSON unreadable for {mode}: {exc}")
            continue
        if data.get("driving_level") != mode:
            failures.append(f"session_summary wrote {data.get('driving_level')!r}, expected {mode}")

    replay = subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools" / "replay_coach.py"), run_name, "--pro-video-reference", "latest"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
        capture_output=True,
        text=True,
        check=False,
    )
    if replay.returncode != 0:
        failures.append(f"replay with latest pro video reference failed: {replay.stderr or replay.stdout}")

    decision_path = run_dir / "coach_decisions.json"
    decision_hash_before_validate = file_sha256(decision_path)
    acceptance = subprocess.run(
        [sys.executable, "-u", str(ROOT / "tools" / "validate_milestone_acceptance.py"), run_name],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
        capture_output=True,
        text=True,
        check=False,
    )
    if acceptance.returncode != 0:
        failures.append(f"milestone acceptance failed: {acceptance.stderr or acceptance.stdout}")
    decision_hash_after_validate = file_sha256(decision_path)
    if decision_hash_before_validate != decision_hash_after_validate:
        failures.append("Validate changed coach_decisions.json; validation must not overwrite replay/pro-video decisions")

    for path, data in backups.items():
        if data is None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        else:
            path.write_bytes(data)

    return failures


def main():
    parser = argparse.ArgumentParser(description="Validate app coaching-mode contracts across Beginner, Intermediate, and Pro.")
    parser.add_argument("run", nargs="?", default="Spa-beginner-run-03", help="Existing run to use for offline mode checks.")
    args = parser.parse_args()

    run_dir = ROOT / "runs" / args.run
    if not run_dir.exists():
        raise SystemExit(f"Run not found: {run_dir}")

    failures = []
    failures.extend(check_source_contract())
    failures.extend(check_runtime_tools(args.run))

    report = {
        "run": args.run,
        "modes_checked": list(MODES),
        "expected_message_gaps_seconds": EXPECTED_GAPS,
        "failures": failures,
        "passed": not failures,
    }
    report_path = ROOT / "runs" / args.run / "app_mode_validation.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"App mode validation report: {report_path}")
    print(f"Modes checked: {', '.join(MODES)}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
