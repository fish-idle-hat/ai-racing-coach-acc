# Milestone 1 Plan - Basic Telemetry Analyzer

Goal: turn the proven live telemetry stream into a post-session lap analyzer.

## Phase 1A - Rich Capture

Status: passed on `runs/acc-rich-capture-1`

Capture more than physics:

- speed
- throttle
- brake
- steering
- gear
- RPM
- fuel
- current lap time
- last lap time
- best lap time
- completed lap count
- current sector
- session status/type
- track
- car model

Pass condition:

- A 5-minute test run populates lap/session fields in `telemetry.csv` and `summary.json`.

## Phase 1B - Session Report

Status: working initial version

Generate a readable report from a captured run:

- session duration
- packet rate
- track/car if available
- top speed
- throttle/brake/steering activity
- lap count changes
- rough lap summaries where lap metadata is available

Pass condition:

- `tools/analyze_run.py runs/<run-name>` produces a useful `analysis.json` and `analysis.md`.

Current output includes:

- session metrics
- track and car
- best/last lap display
- first observations
- top braking events
- sector summaries

## Phase 1C - Basic Lap Segmentation

Status: working initial version

Use lap count and lap timers to split recorded data into laps.

Initial outputs:

- valid lap windows
- lap time
- max speed per lap
- throttle/brake usage per lap
- rough sector transitions

Current behavior:

- uses current-lap timer resets for timed lap windows
- labels partial/out-lap segments separately from valid timed laps
- labels incomplete current laps separately
- compares valid laps once at least two valid timed laps are captured

## Phase 1D - First Driving Insights

Status: passed for initial feasibility on `runs/acc-insight-test-2`

Start with simple, defensible observations:

- heavy braking zones
- throttle pickup after braking
- minimum speed points
- high steering plus throttle overlap
- potential missed shifts or low RPM exits

No real-time coaching yet.

Current output includes:

- sector-level time loss against the best valid lap
- major braking-event labels and lap-time positions
- braking-event comparison against the best valid lap
- first-pass coaching candidate hints
- robust filtering for out-laps, incomplete laps, timer discontinuities, and post-session distance jumps

Known limitation:

- braking events are matched by sector and nearest lap-time position, not by real corner identity yet
- hints are hypotheses, not final coaching advice

Next step:

- build a track/corner map layer for Spa so events can become named corners or braking zones

## Phase 1E - Spa Braking-Zone Map

Status: passed for initial feasibility on `runs/acc-spa-clean-test-1`

Map major braking events from lap distance into named Spa zones.

Current output includes:

- approximate Spa braking-zone labels in top braking events
- approximate Spa braking-zone labels in braking-event comparisons
- approximate Spa braking-zone labels in coaching candidate hints
- clean validation across 5 valid timed laps in the McLaren 720S GT3 Evo

Known limitation:

- labels are distance-range based and should be refined with more clean laps
- this is a braking-zone map, not yet a full corner-by-corner racing-line model

## Phase 1F - Prioritized Coaching Recommendation

Status: passed for initial feasibility on `runs/acc-spa-clean-test-1`

Convert analysis output into one clear next-lap instruction.

Target behavior:

- choose one issue at a time
- ignore messy out-laps, in-laps, incomplete laps, and obvious mistake laps
- prioritize repeated losses over one-off mistakes
- produce a concise next-lap instruction tied to a named Spa zone
- explain the evidence behind the instruction in the post-session report

Current output includes:

- `Recommended Next-Lap Focus` report section
- one focus zone, sector, instruction, repeated-loss count, and supporting evidence
- mistake-lap filtering for recommendation ranking
- readable Spa reference map exported as `spa_world_path_map.svg` when world-position telemetry is present
- raw world-position trace inset inside the Spa map report

Known limitation:

- raw world-position traces are not yet calibrated to the reference map background
- old runs without world-position fields fall back to schematic lap-distance mapping

## Phase 1G - World-Position Capture

Status: passed for initial feasibility on `runs/acc-position-test-3`

Extend the CrossOver helper and Mac receiver to capture ACC world position.

Current output fields:

- `normalized_car_position`
- `player_car_id`
- `player_car_index`
- `car_world_x`
- `car_world_y`
- `car_world_z`
- temporary legacy coordinate debug fields for offset comparison

Validation target:

- after rebuilding the helper, a 60-90 second live capture shows changing `car_world_x` and `car_world_z`
- `summary.json` reports `pass_hints.has_world_position: true`
- `runs/acc-position-test-3/spa_world_path_map.svg` confirms real trace rendering from captured position data

Current note:

- first position tests showed the old loose check was too permissive
- the ACC-layout coordinate fields were static on this setup, while the legacy-offset Y/Z pair moved correctly
- primary top-down path fields now map `car_world_x = legacy_y`, `car_world_z = legacy_z`, and retain raw ACC/legacy debug fields for comparison
- short runs can generate a single real path trace inset over a readable Spa reference map; full comparison overlays need at least two valid timed laps
- the reference map confirms the session context is Spa, but precise trace-to-map calibration is still pending

## Phase 1H - Live Spa Map Prototype

Status: working initial version

Build a real-time browser map before starting full Milestone 2 coaching.

Current output includes:

- `tools/live_spa_map.py`, a local Python UDP listener plus browser map server
- live page at `http://127.0.0.1:8787`
- clean user-provided Spa map as the visual background
- approximate Spa centerline points in `data/track_maps/spa_overlay.json`, fitted to the clean map image
- live car dot and blue trail driven by `normalized_car_position`
- marker overlays hidden until brake/throttle point positions are calibrated
- saved run output with `telemetry.csv`, `telemetry.ndjson`, and `summary.json`

Known limitation:

- this uses `normalized_car_position` along a hand-placed centerline, not final raw world-coordinate calibration
- marker positions are rough and need refinement against real laps and reference braking data
- this is a map/UI feasibility prototype, not the full adaptive coaching engine yet

Next milestone:

- Milestone 2 starts the real-time coaching prototype using the same one-focus recommendation style

## Milestone 2A - Real-Time Zone Detection + Text Coaching

Status: accepted as part of Milestone 2 technical prototype; simulation passed on `runs/realtime-coach-self-test`

Start real-time coaching without depending on a perfectly calibrated visual map.

Current output includes:

- `tools/realtime_coach.py`, a terminal-based live coach that listens on UDP `47777`
- real-time Spa zone detection from `distance_traveled` or `normalized_car_position`
- low-frequency coaching messages for zone entry, brake phase detection, throttle pickup, and exit focus
- saved `telemetry.csv`, `telemetry.ndjson`, `coach_events.ndjson`, and `summary.json`
- simulation support through `tools/fake_sender.py`

Remaining limitation:

- messages are still deterministic, not LLM-authored during live driving
- zone mapping is good enough for coaching, but not yet a polished visual map or apex-perfect racing-line overlay

## Milestone 2B - Quieter Prompts + Lap Focus

Status: accepted as part of Milestone 2 technical prototype; simulation passed on `runs/realtime-coach-2b-quiet-self-test`

Reduce message noise and turn live observations into one next-lap focus.

Current output includes:

- quiet default mode with fewer terminal prompts during the lap
- key approach prompts for important Spa braking/exit zones
- more specific corner cues for brake release, turn-in, throttle pickup, and exit priority
- per-zone entry cooldowns to avoid repeated prompts
- end-of-lap summary
- one specific `Next lap focus` recommendation from live zone observations
- optional `--verbose-events` mode for detailed brake/throttle/debug messages

Remaining limitation:

- focus selection now flows through `tools/coach_decision.py`, but live phrasing remains deterministic unless the optional post-run AI layer is used

## Milestone 2C - Optional Voice Coaching

Status: accepted as part of Milestone 2 technical prototype; compile check passed

Add low-distraction voice output on top of the validated real-time text coach.

Current output includes:

- optional `--voice` mode using macOS `say`
- optional `--voice-name` and `--voice-rate`
- non-blocking speech so telemetry capture continues while messages are spoken
- speech de-overlap: if the previous message is still speaking, the next spoken line is skipped while terminal logging continues
- shorter spoken text for corner labels, while full text remains in the terminal and event log

Remaining limitation:

- voice is still system TTS, not a tuned race-coach voice
- voice messages now queue sequentially; future work should add smarter interruption/priority for urgent calls

## Milestone 2D - Corner Timing Correction

Status: accepted as part of Milestone 2 technical prototype; world-path replay checks passed on saved Spa world-position runs

Fix delayed or incorrect corner identification before adding understeer/brake-point/throttle-specific coaching.

Current output includes:

- real-time Spa corner detection now prefers `car_world_x`/`car_world_z` projected onto `data/track_maps/spa_world_path.json`
- the world tracker follows an ordered calibrated path, so nearby but unrelated Spa sections do not trigger each other's corners
- `normalized_car_position` is now only the fallback when world-path gates are disabled or unavailable
- approach prompts now fire when live world-path progress crosses the next expected corner gate, not when a broad zone changes
- added a T3 Eau Rouge trigger after T2, matching the desired "leaving one corner, coach the next corner" behavior
- low-speed prompt suppression with `--min-prompt-speed`
- optional `--position-offset-lap` fine-tuning
- removed unclear T1 "downhill" wording

Validation:

- replaying `runs/acc-realtime-voice-1` fired one ordered sequence with no prompt burst: T5, T8, T10, T12, T14, T18, T1, then T3 after lap-timer reset
- additional saved world-position runs replayed in forward Spa order from their session start point
- the bogus stopped T1 prompt at session start no longer appears

## Milestone 2E - Adaptive Lap Memory + Focused Reminders

Status: accepted as part of Milestone 2 technical prototype; replay checks passed on `runs/acc-world-corner-test-2`, `runs/acc-realtime-coach-3`, `runs/acc-ai-coach-test-1`, and `runs/acc-coach-decision-test-1`

Add the first intelligence layer on top of correct corner timing.

Current output includes:

- first lap stays quiet for corner approach calls while the coach collects baseline data
- reduced repetition on later laps after the first lap baseline
- richer per-zone lap records: brake start, throttle pickup, minimum speed, steering/throttle conflict, pedal overlap, and zone timing metadata
- lap-end voice/text summaries that identify one likely bad zone instead of repeating generic advice
- next-lap focus memory, so the coach gives a targeted reminder when the bad corner is coming up again
- targeted reminders now say "For the upcoming corner..." before the instruction
- zero-speed startup intro from coach Rachel using player name and available weather data
- startup/finish lap detection from ACC lap-count changes or normalized-position start/finish wrap, because ACC lap count can stay constant in some CrossOver captures
- queued macOS voice output so multi-line lap summaries are spoken sequentially instead of skipped

Current safe issue signals:

- major mistake / likely invalid lap: unusually large speed loss in high-speed zones such as Eau Rouge/Raidillon
- major corner push: very low minimum speed plus high steering input in major braking zones
- possible understeer/exit push: high steering while adding throttle
- no clean throttle pickup after braking
- brake/throttle overlap
- minimum speed lower than the user's previous best recorded pass through that zone
- brake point earlier than the user's previous best recorded pass
- throttle pickup later than the user's previous best recorded pass

Remaining limitation:

- this is still deterministic coaching, not live LLM reasoning
- understeer is inferred from input behavior, not from tire slip angle or direct physics telemetry
- weather currently uses air and road temperature only; rain/cloud/wind are not captured yet
- tire temperatures are captured in telemetry but no longer spoken as real-time reminders
- coarse zone-duration comparison is intentionally not spoken yet because it can be misleading around lap-boundary/session-start cases

## Milestone 3A - Personal-Best Reference Comparison

Status: implemented; validated on `runs/acc-adaptive-coach-test-2`

Build the first reference comparison engine the AI coach will reason from.

Current output includes:

- `tools/reference_compare.py`
- personal-best reference JSON under `data/references/`
- automatic best valid lap selection from one or more captured runs
- corner/zone comparison against the reference lap
- JSON report: `runs/<run-name>/reference_comparison.json`
- Markdown report: `runs/<run-name>/reference_comparison.md`

Current comparison signals:

- brake point delta
- heavy-brake point delta
- trail-brake duration delta
- throttle pickup delta
- full-throttle point delta
- minimum speed delta
- exit speed delta
- upshift count difference
- racing-line distance from the reference path when world position exists
- throttle while steering load is high
- brake/throttle overlap

Known limitation:

- comparison is against the user's own best lap first, not pro telemetry yet
- zones are still broad Spa sections, not every individual turn apex
- live voice coaching has not yet used the reference report during the lap

## Milestone 3B - Post-Run Reference Report Integration

Status: implemented; compile and saved-run comparison checks passed

Wire the reference engine into the current real-time coach workflow.

Current output includes:

- `tools/realtime_coach.py` automatically looks for `data/references/<track>_<car>_personal_best.json` when a run stops
- if a matching reference exists, the coach writes:
  - `runs/<run-name>/reference_comparison.json`
  - `runs/<run-name>/reference_comparison.md`
- terminal prints the reference report path and top focus recommendation
- optional `--reference <path>` to force a specific reference file
- optional `--disable-reference-compare` to skip the post-run report

Current limitation:

- reference comparison is post-run only; the next step is using the reference profile inside live lap summaries and upcoming-corner reminders

## Milestone 3D - Accuracy Lock

Status: complete for the current CrossOver telemetry route; acceptance passed on `runs/acc-ai-coach-test-1` and `runs/acc-coach-decision-test-1`

Purpose:

- harden lap validity, incident evidence, replay regression, and reference-lap exclusion before expanding the coach model.

Current output includes:

- official ACC lap validity captured through `is_valid_lap`
- completed and incomplete lap segmentation using start/finish wrap
- invalid laps excluded from personal-best/reference comparison
- official invalidation trigger detection mapped to the current Spa zone
- incident evidence capture from damage, tyres-out, wheel slip, and angular velocity fields
- conservative incident wording when direct contact/off-track/spin evidence is missing
- replay/regression checks through `tools/coach_regression_check.py`
- acceptance checks through `tools/validate_milestone_acceptance.py`
- acceptance report at `runs/milestone_acceptance_report.md`

Validation:

- `PYTHONPATH=tools python3 tools/validate_milestone_acceptance.py acc-ai-coach-test-1 acc-coach-decision-test-1`
- Result: both runs passed all Milestone 2 and Milestone 3D checks.

Remaining limitation:

- exact apex-level turn boundaries are not locked yet; Spa is still represented as turn groups/zones.
- screen OCR and pro-video reference remain secondary-source milestones, not blockers for the current telemetry accuracy lock.
