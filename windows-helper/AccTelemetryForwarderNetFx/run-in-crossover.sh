#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOTTLE="$HOME/Library/Application Support/CrossOver/Bottles/ACC"
CROSSOVER="/Applications/CrossOver Preview.app/Contents/SharedSupport/CrossOver"
WINE="$CROSSOVER/CrossOver-Hosted Application/wine"
EXE="$ROOT/build/AccTelemetryForwarderNetFx-v2.exe"
if [[ ! -f "$EXE" ]]; then
  EXE="$ROOT/build/AccTelemetryForwarderNetFx.exe"
fi
EXE_Z="$(printf 'Z:%s' "$EXE" | sed 's#/#\\\\#g')"

export WINEPREFIX="$BOTTLE"
export CX_BOTTLE="ACC"

if [[ ! -d "$BOTTLE" ]]; then
  echo "CrossOver bottle not found: $BOTTLE" >&2
  echo "Open CrossOver and confirm the bottle is named exactly: ACC" >&2
  exit 1
fi

if [[ ! -x "$WINE" ]]; then
  echo "CrossOver hosted launcher not found: $WINE" >&2
  exit 1
fi

if [[ ! -f "$EXE" ]]; then
  echo "Helper executable not found: $ROOT/build/AccTelemetryForwarderNetFx-v2.exe or $ROOT/build/AccTelemetryForwarderNetFx.exe" >&2
  echo "Run ./windows-helper/AccTelemetryForwarderNetFx/build-in-crossover.sh first." >&2
  exit 1
fi

echo "Using helper executable: $EXE"
exec "$WINE" "$EXE_Z" 127.0.0.1 47777 30
