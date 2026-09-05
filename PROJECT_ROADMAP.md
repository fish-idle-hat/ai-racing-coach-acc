# ACC AI Coach Roadmap

This file is the high-level roadmap for the project. Detailed implementation notes for the current prototype remain in `MILESTONE_1_PLAN.md`.

## Milestone 0 - Feasibility

Prove that ACC running through CrossOver can send useful live telemetry to macOS.

Target outcome:

- ACC telemetry reaches the Mac reliably.
- Speed, throttle, brake, steering, gear, RPM, lap metadata, and world position can be captured.
- The game remains smooth while the helper and receiver are running.

## Milestone 1 - Data Foundation

Turn the raw telemetry stream into useful session data.

Target outcome:

- Rich telemetry capture.
- Lap segmentation.
- Spa zone/corner mapping.
- World-position capture.
- Post-session reports.
- First-pass driving insights.

## Milestone 2 - Real-Time Coaching Prototype

Build a technical real-time coach before building a full app.

Status: complete for the technical prototype as of the `acc-ai-coach-test-1` and `acc-coach-decision-test-1` acceptance checks.

Target outcome:

- Correct corner timing from live car position.
- Low-distraction voice coaching.
- Lap summaries.
- One focused next-lap recommendation.
- Basic adaptive memory from previous laps.
- Rule-based explanations for braking, throttle pickup, steering/throttle conflict, pedal overlap, and minimum-speed loss.

Remaining limitation:

- This is still a heuristic coach, not the full AI driving-coach model.
- Visual app UI, screen OCR, and pro-video reference are intentionally later milestones.

## Milestone 3 - Reference Comparison Engine

Build the comparison layer the AI coach will reason from.

Target outcome:

- Compare the current lap against the user's own best lap.
- Compare corner by corner:
  - brake point
  - brake pressure curve
  - trail-brake release
  - throttle pickup
  - throttle application rate
  - steering angle
  - minimum speed
  - exit speed
  - shift timing
  - racing line from world position
- Add pro/reference data when available.
- Use pro video only as secondary reference unless real telemetry is available.

## Milestone 3D - Accuracy Lock

Harden the data foundation before the coach model learns from it.

Status: complete for the current CrossOver telemetry route as of `runs/milestone_acceptance_report.md`.

Target outcome:

- Capture official ACC lap-validity fields when available through CrossOver/shared memory.
- Verify whether `current_lap_invalid`, `current_lap_valid`, or `is_valid_lap` are reliable under the user's CrossOver setup.
- Exclude officially invalid laps from personal-best/reference comparison.
- Keep inferred invalid-style detection as fallback when official fields are unavailable.
- Add replay/regression checks so old telemetry can validate coach behavior before new driving tests.
- Produce one validation report per test run showing:
  - direct validity captured or not
  - valid/invalid states seen
  - completed versus incomplete laps
  - laps accepted or rejected for reference comparison

Exit criteria:

- A short live test confirms whether official lap validity can be captured. Complete.
- Replay/regression checks pass on saved runs. Complete.
- Rachel does not analyze partial laps before the first clean start/finish crossing. Complete.
- Rachel does not claim exact off-track/spin/wall-contact status unless the data source supports it. Complete.
- Offline reference comparison prioritizes the official ACC invalidation trigger when available. Complete.

Remaining limitation:

- Turn-group boundaries still use the current Spa zone map. The system can lock onto the official invalidation zone, but exact T10 versus T11 versus T12 edge cases may still need finer apex-level calibration.

## Milestone 4 - AI Driving Coach Model

Turn telemetry and reference comparisons into a real AI driving instructor.

Status: complete for the deterministic coach path. Live LLM phrasing remains optional and is paused until API credits and latency tradeoffs are worth revisiting.

Target outcome:

- Explain why the driver is slow, not just where.
- Maintain a persistent driver model.
- Detect repeated habits across sessions.
- Adapt feedback to the user's learning style.
- Build a personalized curriculum.
- Generate corner-specific, lap-specific coaching instructions.
- Eventually reason about setup changes only after driving-input issues are separated from setup issues.

Subgoals:

- 4A - Persistent driver model:
  - Save driver, track, car, repeated issues, current focus, and improvement trends across sessions.
  - Separate one-off mistakes from repeated habits.
  - Feed historical issue counts back into the live lap-decision priority model.
- 4B - Habit detection:
  - Detect repeated issues by turn group, issue type, severity, and frequency.
  - Use issue frequency, not raw count alone, before calling something a habit.
  - Increase priority for high-frequency recurring problems and reduce priority for isolated noise.
  - Explain whether the current issue is a new watchlist item, repeated in the current session, previously seen, or a high-frequency habit.
- 4C - Memory-aware coaching:
  - Record why the mistake happened, whether it is one-off or recurring, and how the next lap should change.
  - Pick which issue should be handled first and which small issues should be ignored temporarily.
  - Carry a current focus and watchlist across runs by driver, track, and car.
- 4D - Screen vision / OCR secondary source:
  - Status: paused by product decision. Telemetry remains the primary source for now.
  - Capture the ACC/CrossOver window when macOS Screen Recording permission is granted.
  - OCR visible lap/invalid markers, HUD warnings, car/session info, and setup-screen values when needed.
  - Use screen vision only as a secondary source to cross-check telemetry or fill gaps.
  - Never let OCR override reliable telemetry without confidence checks.
  - Use screen data to debug mismatches between Rachel's lap/turn understanding and the game HUD.
- 4E - Coach output audit and fallback intelligence:
  - Generate replayable coach-event reports from previous runs so bugs can be found without driving more laps.
  - Keep deterministic coaching strong enough to work without OpenAI API credits.
  - Store a structured `coach_ai_context.json` so an LLM can later rewrite Rachel's phrasing without inventing facts.
  - Validate official lap validity, turn timing, incident evidence, memory classification, and report generation together.

## Milestone 5 - Mac App

Turn the technical prototype into a usable app.

Status: complete for the current Spa/local-coach product path. 5A browser dashboard is superseded by the native macOS app. 5B/5C/5D are functional in the app, with session history, driver profile reports, post-session scoring, and native start/stop controls.

Target outcome:

- Start/stop coaching from a normal app interface.
- ACC connection status.
- Voice coach controls.
- Session history.
- Lap summary dashboard.
- Corner-by-corner weakness list.
- Reference-lap comparison view.

Subgoals:

- 5A - Local coach dashboard:
  - Start and stop Rachel from a browser UI.
  - Show helper command for the CrossOver telemetry forwarder.
  - List saved runs and show replay, decision, profile, turn timing, and reference reports.
  - Run replay, validation, and profile update from buttons.
- 5B - Connection workflow:
  - Show telemetry packet status and current active run.
  - Reduce the two-terminal workflow as much as CrossOver allows.
- 5C - Session history and driver profile:
  - Make the persistent driver profile readable from the app.
  - Highlight watchlist, repeated session issues, and high-frequency habits.
- 5D - Native packaging:
  - Package the dashboard/server launcher into a Mac app-style entry point.
  - Add icons, launch state, and clearer error screens.
- 5E - Commercial UI/UX polish:
  - Replace terminal-like workflow with a polished native macOS interface.
  - Provide Home, Drive, Analyze, Profile, AI, Setup, and Settings sections.
  - Keep Beginner coaching as the active product path.
  - Show Intermediate, Pro, and Setup Coach as prepared UI surfaces without enabling unfinished functionality.
- Driver profile and progress tracking.
- Coach settings for voice, personality, directness, and feedback frequency.
- Optional live map/overlay once map calibration is reliable enough. Paused for now.

## Milestone 6 - Commercial UI/UX Layer

Turn the functional native app into a cohesive desktop training product.

Status: complete for the current native app pass. The app now has a polished Home dashboard, Drive workspace, Analyze workspace, app-native radar, session cards, clickable curriculum, run history, app-visible reports, and guided first-run onboarding.

Subgoals:

- 6A - Home dashboard:
  - Show current coach status, latest score, next focus, quick start, recent runs, and curriculum state.
- 6B - Drive workspace:
  - Keep start/stop/test voice, level selection, track auto-detection fallback, and live Rachel output in one polished workflow.
- 6C - Analyze workspace:
  - Show session cards, app-native radar, score explanations, lap snapshot, strongest/weakest areas, and generated reports in the app.
- 6D - Driver curriculum:
  - Turn profile memory into a readable training path with active/prepared/stable steps.
- 6E - Interaction polish:
  - Add hover, selected, pressed, loading, glass surfaces, and readable light/dark appearance.
- 6F - Run history and comparison:
  - Make previous runs selectable and reviewable from the app, with score and metadata visible before opening reports.
- 6G - Bug and quality pass:
  - Ensure visible buttons produce app-visible results, preserve existing coaching, and avoid browser-only analysis surfaces.
- 6H - Guided tutorial / first-run onboarding:
  - Trigger a commercial in-app tutorial on first launch or from the Home Tutorial button.
  - Highlight key navigation items, Drive controls, Pro Video Reference, Analyze reports, radar scoring, driver curriculum, AI settings, and Setup Coach.
  - Pair each step with English explanatory text, Rachel voice narration, progress, Back, Next, Skip, and Done controls.

Paused scope:

- True AI Driving Coach / OpenAI live coaching: paused until API credits are available and latency is acceptable.
- Screen Vision / OCR: paused unless telemetry fields are missing or HUD verification becomes necessary.
- Setup Coach: UI placeholder only; no setup recommendations until driving-input issues can be separated from setup issues.
- Multi-track support: paused; current product focus remains Spa.

## Milestone 7 - Pro Video Reference

Use pro-driver MP4 files as secondary visual references for Spa.

Status: implemented as a local video-reference workflow with first-pass automatic HUD input extraction, per-turn time nodes, and Rachel comparison integration. Speed/gear OCR is not treated as reliable yet.

Subgoals:

- 7A - Video intake:
  - Select one or more local MP4/video files from the native app.
  - Store track, optional car model, notes, and source metadata.
- 7B - Media analysis:
  - Use `ffprobe` to capture duration, resolution, frame rate, codec, and availability.
  - Use `ffmpeg` to extract sampled frames and a contact sheet for quick visual review.
  - Estimate steering, throttle, and brake from visible lower-HUD pixels when the source video exposes a usable ACC-style input display.
- 7C - Spa cue checklist:
  - Produce turn-group review targets for braking markers, apex timing, throttle pickup, and exit usage.
- 7D - App integration:
  - Provide a dedicated Pro Video page with setup controls, selected videos, checklist, app-visible contact sheets, and report output.
- 7E - Safety boundary:
  - Treat video references as secondary evidence; ACC telemetry and official lap validity remain authoritative.
- 7F - Rachel comparison:
  - Drive sessions can opt into the latest pro-video reference.
  - Rachel compares turn-level braking, brake release, trail braking, and throttle pickup against the pro-video reference when confidence is usable.
- 7G - Pro-loss prioritization:
  - Rachel ranks pro-video comparison issues by estimated turn/section time loss.
  - Major incidents, official invalidation, spin/recovery, contact-like evidence, and off-track-style events still override pro deltas.
  - When Pro Video Reference is enabled, smaller local-only driving issues are muted unless they cause a major lap-losing event.
- 7H - Speed and gear boundary:
  - Per-turn entry/exit time nodes are captured from the selected reference-lap time range.
  - Pro video speed and gear fields exist in the data model, but are marked unavailable unless OCR can read them reliably.
  - Current reliable inputs are steering, throttle, brake, time nodes, and live ACC telemetry from the user's run.
