# Milestone 0 Status - ACC Telemetry Feasibility

Status: passed core feasibility

Date: 2026-08-14

## What Worked

- ACC was running through CrossOver in the `ACC` bottle.
- The Windows helper ran inside the same CrossOver bottle.
- The helper successfully read ACC physics shared memory.
- The helper forwarded telemetry to the Mac receiver over local UDP.
- The Mac receiver recorded live driving data to CSV and NDJSON.
- Gear display was corrected to match ACC's in-game gear.

## Stability Run

Run folder:

```text
runs/acc-stability-1
```

Summary:

- Duration: 1014.975 seconds, about 16.9 minutes
- Packets recorded: 28,737
- Average receiver rate: 28.313 packets/sec
- Mean instant packet rate: 29.083 Hz
- Max receiver interval: 0.111 seconds
- Receiver intervals over 0.5 seconds: 0
- Receiver intervals over 1.0 seconds: 0
- Max speed: 261.875 km/h
- Throttle range: 0.0 to 1.0
- Brake range: 0.0 to 1.0
- Steering range: approximately -1.0 to 1.0

## Conclusion

The telemetry path is stable enough to continue to the next feasibility layer. The app can use a CrossOver-side helper to read ACC live physics data and forward it to a Mac process reliably enough for Milestone 1 prototyping.

## Known Gaps

- Current helper reads the physics shared-memory page only.
- Lap count, lap time, track name, and car model are not captured yet.
- No corner segmentation exists yet.
- No session UI exists yet.

## Recommended Next Step

Extend the Windows helper to also read ACC graphics/static shared-memory pages, then capture:

- current lap time
- last lap time
- best lap time
- completed lap count
- track name
- car model
- session status

After that, run another 5-minute test and confirm those fields populate correctly.
