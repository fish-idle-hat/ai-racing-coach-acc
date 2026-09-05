#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/build/AI Racing Coach - ACC.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
APP_PROJECT="$RESOURCES/AppProject"
CACHE="$ROOT/build/swift-cache"
ICON_SOURCE_DIR="$ROOT/mac-app/AppIcon.appiconset"
ICONSET="$ROOT/build/AppIcon.iconset"
ICON_FILE="$RESOURCES/AppIcon.icns"

mkdir -p "$MACOS" "$RESOURCES" "$CACHE"

if [[ -d "$ICON_SOURCE_DIR" ]]; then
  cp "$ICON_SOURCE_DIR/1024.png" "$RESOURCES/AppIcon.png"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  cp "$ICON_SOURCE_DIR/16.png" "$ICONSET/icon_16x16.png"
  cp "$ICON_SOURCE_DIR/32.png" "$ICONSET/icon_32x32.png"
  cp "$ICON_SOURCE_DIR/64.png" "$ICONSET/icon_64x64.png"
  cp "$ICON_SOURCE_DIR/128.png" "$ICONSET/icon_128x128.png"
  cp "$ICON_SOURCE_DIR/256.png" "$ICONSET/icon_256x256.png"
  cp "$ICON_SOURCE_DIR/512.png" "$ICONSET/icon_512x512.png"
  cp "$ICON_SOURCE_DIR/1024.png" "$ICONSET/icon_1024x1024.png"
  ICONSET="$ICONSET" ICON_FILE="$ICON_FILE" python3 - <<'PY'
from pathlib import Path
import os
import struct

iconset = Path(os.environ["ICONSET"])
out = Path(os.environ["ICON_FILE"])
items = [
    ("icp4", "icon_16x16.png"),
    ("icp5", "icon_32x32.png"),
    ("icp6", "icon_64x64.png"),
    ("ic07", "icon_128x128.png"),
    ("ic08", "icon_256x256.png"),
    ("ic09", "icon_512x512.png"),
    ("ic10", "icon_1024x1024.png"),
]
chunks = []
for code, name in items:
    data = (iconset / name).read_bytes()
    chunks.append(code.encode("ascii") + struct.pack(">I", len(data) + 8) + data)
body = b"".join(chunks)
out.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
PY
fi

rm -rf "$APP_PROJECT"
mkdir -p "$APP_PROJECT/tools" "$APP_PROJECT/windows-helper" "$APP_PROJECT/data" "$APP_PROJECT/build" "$APP_PROJECT/runs" "$APP_PROJECT/mac-app"
rsync -a --exclude '__pycache__' "$ROOT/tools/" "$APP_PROJECT/tools/"
rsync -a "$ROOT/windows-helper/" "$APP_PROJECT/windows-helper/"
rsync -a "$ROOT/data/" "$APP_PROJECT/data/"
cp "$ROOT/mac-app/ACC_AI_Coach.swift" "$APP_PROJECT/mac-app/"
cp "$ROOT/build/AccTelemetryForwarderNetFx-v2.exe" "$APP_PROJECT/build/" 2>/dev/null || true
cp "$ROOT/build/AccTelemetryForwarderNetFx.exe" "$APP_PROJECT/build/" 2>/dev/null || true
cp "$ROOT/README.md" "$APP_PROJECT/" 2>/dev/null || true
cp "$ROOT/build_mac_app.sh" "$APP_PROJECT/" 2>/dev/null || true
if [[ -d "$ROOT/runs/self-check-pro-video-smoke" ]]; then
  rsync -a "$ROOT/runs/self-check-pro-video-smoke" "$APP_PROJECT/runs/"
fi
if [[ -d "$ROOT/runs/Spa-beginner-run-03" ]]; then
  rsync -a "$ROOT/runs/Spa-beginner-run-03" "$APP_PROJECT/runs/"
fi

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>AI Racing Coach - ACC</string>
  <key>CFBundleIdentifier</key>
  <string>local.ai-racing-coach-acc.prototype</string>
  <key>CFBundleName</key>
  <string>AI Racing Coach - ACC</string>
  <key>CFBundleDisplayName</key>
  <string>AI Racing Coach - ACC</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.6.19</string>
  <key>CFBundleVersion</key>
  <string>6ZA</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSDocumentsFolderUsageDescription</key>
  <string>AI Racing Coach - ACC reads saved telemetry runs and writes coaching reports in the selected or bundled project folder.</string>
  <key>NSDesktopFolderUsageDescription</key>
  <string>AI Racing Coach - ACC may read user-selected screenshots or exported reports when requested.</string>
</dict>
</plist>
PLIST

xcrun swiftc \
  -parse-as-library \
  -target arm64-apple-macosx14.0 \
  -module-cache-path "$CACHE" \
  -framework SwiftUI \
  -framework AppKit \
  "$ROOT/mac-app/ACC_AI_Coach.swift" \
  -o "$MACOS/AI Racing Coach - ACC"

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "$APP"
