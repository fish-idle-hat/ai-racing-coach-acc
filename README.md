# AI Racing Coach - ACC

AI Racing Coach - ACC is a local macOS training app prototype for Assetto Corsa Competizione. It captures ACC telemetry from a CrossOver/Windows helper, records driving sessions on macOS, and turns the data into live voice coaching, replayable reports, reference-lap comparisons, and a native SwiftUI dashboard.

The goal is simple: help sim racers practice with specific, turn-level feedback from their own driving instead of guessing from generic tutorial videos.

## Looking For Partners

This project is currently looking for partners who can help improve the remaining coaching mechanisms, especially telemetry calibration, corner-level comparison, personalized coaching logic, setup-coach reasoning, UI polish, and real-driver testing.

See `PARTNERS.md` for the current collaboration areas.

Open a GitHub issue with the `Partner collaboration` template if you want to help build the next version.

## Why It Matters

Most sim-racing improvement tools either show raw telemetry or require manual post-session review. AI Racing Coach - ACC is aimed at a different workflow: drive laps, hear short coaching cues at the right time, then review a focused training plan after the session.

This makes the project useful for:

- ACC drivers who want faster feedback loops.
- Coaches who want telemetry-backed student review.
- Developers interested in local AI, motorsport telemetry, SwiftUI, and game-tooling integrations.

## Current Status

- Native macOS SwiftUI app shell is available in `mac-app/`.
- Local browser dashboard is available through `tools/coach_app.py`.
- Python real-time coach, replay tools, validation tools, driver model, and reference comparison are implemented in `tools/`.
- ACC telemetry helper source is available in `windows-helper/`.
- Current product focus is Spa with the McLaren 720S GT3 Evo reference path.

The app is a runnable prototype, not a finished commercial driving coach.

## Requirements

- macOS 14 or newer for the native SwiftUI app.
- Python 3.10 or newer.
- CrossOver with ACC installed for live telemetry.
- Xcode command line tools for building the macOS app with `xcrun swiftc`.
- Optional Python packages from `requirements.txt` for pro-video and report-generation utilities.

Install optional Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Run The Local Dashboard

The simplest runnable app path is the local dashboard:

```bash
./run_coach_app.sh
```

Then open:

```text
http://127.0.0.1:8788
```

From the dashboard you can:

- Start and stop the live coach.
- See the CrossOver helper command.
- Browse saved runs.
- Generate replay, validation, profile, timing, and reference reports.

## Build The Native macOS App

Build the SwiftUI app bundle:

```bash
./build_mac_app.sh
```

The script prints the generated app path, usually:

```text
build/AI Racing Coach - ACC.app
```

Open the app from Finder or Terminal after the build completes.

## Live ACC Workflow

1. Start ACC through CrossOver and enter a practice session.
2. Start the macOS app or local dashboard.
3. Start coaching from the app.
4. Run the CrossOver helper:

```bash
./windows-helper/AccTelemetryForwarderNetFx/run-in-crossover.sh
```

5. Drive laps.
6. Stop coaching from the app.
7. Review the generated run under `runs/<run-name>/`.

Generated runs are intentionally ignored by Git because they can become very large.

## Offline Checks

Run the app-mode validation against a local saved run:

```bash
python3 tools/validate_app_modes.py Spa-beginner-run-03
```

Run the coach regression check:

```bash
python3 tools/coach_regression_check.py
```

These checks require saved telemetry runs. They may not work immediately after a fresh clone until you create or copy local run data.

## Repository Layout

```text
mac-app/          Native SwiftUI macOS app source and icon assets
tools/            Python receiver, coach, dashboard, replay, validation, and analysis tools
windows-helper/   C# ACC telemetry forwarder for CrossOver/Windows
data/             Track maps, reference data, and driver profiles
build_mac_app.sh  Local macOS app bundle builder
run_coach_app.sh  Local browser dashboard launcher
```

## Project Direction

The prototype already proves the main local path:

```text
ACC in CrossOver -> Windows telemetry helper -> UDP -> macOS coach -> reports/app UI
```

The remaining work is improving accuracy, product quality, and coaching intelligence. Partner contributions are welcome in those areas.

## Promotion

Shareable project copy, launch-post drafts, and outreach targets are in `PROMOTION.md`.
