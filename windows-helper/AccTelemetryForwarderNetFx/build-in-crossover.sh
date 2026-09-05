#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOTTLE="$HOME/Library/Application Support/CrossOver/Bottles/ACC"
CROSSOVER="/Applications/CrossOver Preview.app/Contents/SharedSupport/CrossOver"
WINE="$CROSSOVER/CrossOver-Hosted Application/wine"
CSC="C:\\windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe"
SRC_Z="$(printf 'Z:%s' "$ROOT/windows-helper/AccTelemetryForwarderNetFx/Program.cs" | sed 's#/#\\\\#g')"
OUT_DIR="$ROOT/build"
OUT_Z="$(printf 'Z:%s' "$OUT_DIR/AccTelemetryForwarderNetFx-v2.exe" | sed 's#/#\\\\#g')"

mkdir -p "$OUT_DIR"

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

"$WINE" "$CSC" /nologo /optimize+ /platform:x64 "/out:$OUT_Z" "$SRC_Z"

echo "Built: $OUT_DIR/AccTelemetryForwarderNetFx-v2.exe"
