#!/usr/bin/env python3
import argparse
import csv
import json
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from mac_receiver import CSV_FIELDS, normalize_packet, summarize


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
TRACK_MAPS_DIR = ROOT / "data" / "track_maps"
SPA_OVERLAY_PATH = TRACK_MAPS_DIR / "spa_overlay.json"


def now_stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def polyline_lengths(points):
    lengths = []
    total = 0.0
    for index in range(len(points) - 1):
        ax, ay = points[index]
        bx, by = points[index + 1]
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
        lengths.append(length)
        total += length
    return lengths, total


def point_at_fraction(points, fraction):
    if not points:
        return None
    fraction = max(0.0, min(1.0, fraction))
    lengths, total = polyline_lengths(points)
    if total <= 0:
        return points[0]
    target = total * fraction
    walked = 0.0
    for index, length in enumerate(lengths):
        if walked + length >= target:
            local = (target - walked) / length if length else 0.0
            ax, ay = points[index]
            bx, by = points[index + 1]
            return [ax + (bx - ax) * local, ay + (by - ay) * local]
        walked += length
    return points[-1]


def load_overlay():
    if not SPA_OVERLAY_PATH.exists():
        raise SystemExit(f"Missing Spa overlay: {SPA_OVERLAY_PATH}")
    overlay = json.loads(SPA_OVERLAY_PATH.read_text(encoding="utf-8"))
    overlay["centerline_points"] = [
        [float(point[0]), float(point[1])] for point in overlay.get("centerline_points", [])
    ]
    image_path = TRACK_MAPS_DIR / overlay.get("image", "")
    if not image_path.exists():
        raise SystemExit(f"Missing Spa map image: {image_path}")
    overlay["image_path"] = str(image_path)
    return overlay


def marker_points(overlay):
    points = overlay.get("centerline_points", [])
    markers = []
    for marker in overlay.get("coaching_markers", []):
        position = to_float(marker.get("normalized_position"))
        xy = point_at_fraction(points, position or 0.0)
        if xy is None:
            continue
        markers.append({**marker, "x": xy[0], "y": xy[1]})
    return markers


class LiveState:
    def __init__(self, overlay):
        self.overlay = overlay
        self.lock = threading.Lock()
        self.latest = None
        self.rows = []
        self.trail = deque(maxlen=1400)
        self.started_at = time.time()
        self.packet_count = 0

    def update(self, row):
        normalized_position = to_float(row.get("normalized_car_position"))
        x = y = None
        position_source = "none"
        if normalized_position is not None and 0.0 <= normalized_position <= 1.0:
            xy = point_at_fraction(self.overlay["centerline_points"], normalized_position)
            if xy is not None:
                x, y = xy
                position_source = "normalized_car_position"

        packet = {
            "received_at": row.get("received_at"),
            "packet_id": to_int(row.get("packet_id")),
            "track": row.get("track", ""),
            "speed_kmh": to_float(row.get("speed_kmh")),
            "throttle": to_float(row.get("throttle")),
            "brake": to_float(row.get("brake")),
            "steer": to_float(row.get("steer")),
            "gear": to_int(row.get("gear")),
            "rpm": to_int(row.get("rpm")),
            "lap_time_ms": to_int(row.get("lap_time_ms")),
            "lap_count": to_int(row.get("lap_count")),
            "normalized_car_position": normalized_position,
            "raw_world": {
                "x": to_float(row.get("car_world_x")),
                "y": to_float(row.get("car_world_y")),
                "z": to_float(row.get("car_world_z")),
            },
            "map": {"x": x, "y": y, "source": position_source},
        }

        with self.lock:
            self.rows.append(row)
            self.latest = packet
            self.packet_count += 1
            if normalized_position is not None:
                self.trail.append([normalized_position, x, y, time.time()])

    def snapshot(self):
        with self.lock:
            trail = list(self.trail)
            latest = dict(self.latest) if self.latest else None
            return {
                "ok": latest is not None,
                "started_at": self.started_at,
                "now": time.time(),
                "packet_count": self.packet_count,
                "latest": latest,
                "trail": [
                    {
                        "normalized_car_position": round(position, 6),
                        "x": round(x, 2) if x is not None else None,
                        "y": round(y, 2) if y is not None else None,
                    }
                    for position, x, y, _ in trail
                ],
                "markers": marker_points(self.overlay),
                "overlay": {
                    "track_key": self.overlay.get("track_key"),
                    "name": self.overlay.get("name"),
                    "image_width": self.overlay.get("image_width"),
                    "image_height": self.overlay.get("image_height"),
                    "centerline_points": self.overlay.get("centerline_points", []),
                },
            }


def html_page():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ACC Spa Live Map</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b1020;
      color: #e5e7eb;
    }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(0, 1fr) 340px;
      gap: 0;
    }
    main {
      padding: 18px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    aside {
      border-left: 1px solid #1f2937;
      background: #111827;
      padding: 18px;
      overflow: auto;
    }
    h1, h2 {
      margin: 0;
      letter-spacing: 0;
    }
    h1 { font-size: 20px; }
    h2 { font-size: 14px; color: #9ca3af; margin-top: 18px; }
    .map-wrap {
      width: min(100%, 1500px);
      aspect-ratio: 1200 / 914;
      background: #ffffff;
      border: 1px solid #334155;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
    }
    svg { display: block; width: 100%; height: 100%; }
    .fit-line {
      fill: none;
      stroke: rgba(255, 255, 255, 0);
      stroke-width: 16;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .trail {
      fill: none;
      stroke: #2563eb;
      stroke-width: 4;
      stroke-linecap: round;
      stroke-linejoin: round;
      opacity: 0.92;
    }
    .car {
      fill: #ef4444;
      stroke: white;
      stroke-width: 2;
    }
    .stat-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 16px;
    }
    .stat {
      background: #0f172a;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 10px;
      min-height: 58px;
    }
    .label {
      color: #9ca3af;
      font-size: 12px;
    }
    .value {
      font-size: 21px;
      margin-top: 4px;
      font-weight: 700;
    }
    .status {
      margin-top: 10px;
      color: #a7f3d0;
      font-size: 13px;
    }
    .warning { color: #fbbf24; }
    .coach {
      margin-top: 12px;
      background: #0f172a;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 12px;
      line-height: 1.4;
    }
    .control {
      margin-top: 16px;
      background: #0f172a;
      border: 1px solid #1f2937;
      border-radius: 8px;
      padding: 12px;
    }
    .control-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
    }
    input[type="range"] {
      width: 100%;
      margin-top: 10px;
    }
    @media (max-width: 980px) {
      body { grid-template-columns: 1fr; }
      aside { border-left: 0; border-top: 1px solid #1f2937; }
    }
  </style>
</head>
<body>
  <main>
    <div class="map-wrap">
      <svg id="map" viewBox="0 0 1200 914" aria-label="Spa live telemetry map">
        <image id="trackImage" href="/track_map.png" x="0" y="0" width="1200" height="914" preserveAspectRatio="xMidYMid meet"></image>
        <path id="fitLine" class="fit-line"></path>
        <path id="trail" class="trail"></path>
        <circle id="car" class="car" cx="-100" cy="-100" r="7"></circle>
      </svg>
    </div>
  </main>
  <aside>
    <h1>ACC Spa Live Map</h1>
    <div id="status" class="status warning">Waiting for telemetry...</div>
    <div class="stat-grid">
      <div class="stat"><div class="label">Speed</div><div id="speed" class="value">-</div></div>
      <div class="stat"><div class="label">Gear</div><div id="gear" class="value">-</div></div>
      <div class="stat"><div class="label">Throttle</div><div id="throttle" class="value">-</div></div>
      <div class="stat"><div class="label">Brake</div><div id="brake" class="value">-</div></div>
      <div class="stat"><div class="label">Lap</div><div id="lap" class="value">-</div></div>
      <div class="stat"><div class="label">Packets</div><div id="packets" class="value">0</div></div>
    </div>
    <h2>Current Coaching Panel</h2>
    <div id="coach" class="coach">Live map only. Brake/throttle markers are hidden until their positions are calibrated.</div>
    <h2>Map Calibration</h2>
    <div class="control">
      <div class="control-row">
        <div class="label">Position offset</div>
        <div id="offsetValue" class="label">0.000 lap</div>
      </div>
      <input id="offsetSlider" type="range" min="-0.250" max="0.250" step="0.002" value="0">
    </div>
  </aside>
  <script>
    const pathFromPoints = (points) => {
      if (!points || !points.length) return "";
      return points.map((p, i) => `${i === 0 ? "M" : "L"} ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    };
    const storedOffset = Number(localStorage.getItem("spaMapOffset") || "0");
    const offsetSlider = document.getElementById("offsetSlider");
    offsetSlider.value = Number.isFinite(storedOffset) ? storedOffset : 0;
    let overlayInitialized = false;

    function setText(id, value) {
      document.getElementById(id).textContent = value;
    }

    function activeOffset() {
      const value = Number(offsetSlider.value || 0);
      localStorage.setItem("spaMapOffset", String(value));
      document.getElementById("offsetValue").textContent = `${value >= 0 ? "+" : ""}${value.toFixed(3)} lap`;
      return value;
    }

    function wrapFraction(value) {
      return ((value % 1) + 1) % 1;
    }

    function polylineLengths(points) {
      const lengths = [];
      let total = 0;
      for (let i = 0; i < points.length - 1; i++) {
        const ax = points[i][0], ay = points[i][1];
        const bx = points[i + 1][0], by = points[i + 1][1];
        const length = Math.hypot(bx - ax, by - ay);
        lengths.push(length);
        total += length;
      }
      return { lengths, total };
    }

    function pointAtFraction(points, fraction) {
      if (!points || !points.length) return null;
      const wrapped = wrapFraction(fraction);
      const { lengths, total } = polylineLengths(points);
      if (total <= 0) return points[0];
      const target = total * wrapped;
      let walked = 0;
      for (let i = 0; i < lengths.length; i++) {
        const length = lengths[i];
        if (walked + length >= target) {
          const local = length ? (target - walked) / length : 0;
          const ax = points[i][0], ay = points[i][1];
          const bx = points[i + 1][0], by = points[i + 1][1];
          return { x: ax + (bx - ax) * local, y: ay + (by - ay) * local };
        }
        walked += length;
      }
      const last = points[points.length - 1];
      return { x: last[0], y: last[1] };
    }

    function initializeOverlay(overlay) {
      if (overlayInitialized) return;
      const width = overlay.image_width || 1200;
      const height = overlay.image_height || 914;
      document.getElementById("map").setAttribute("viewBox", `0 0 ${width} ${height}`);
      const image = document.getElementById("trackImage");
      image.setAttribute("width", width);
      image.setAttribute("height", height);
      const mapWrap = document.querySelector(".map-wrap");
      mapWrap.style.aspectRatio = `${width} / ${height}`;
      overlayInitialized = true;
    }

    function updateFitLine(points) {
      const params = new URLSearchParams(window.location.search);
      if (params.get("debugLine") === "1") {
        const line = document.getElementById("fitLine");
        line.style.stroke = "rgba(255, 255, 255, 0.35)";
        line.setAttribute("d", pathFromPoints(points));
      }
    }

    async function refresh() {
      const response = await fetch("/state", { cache: "no-store" });
      const state = await response.json();
      const offset = activeOffset();
      setText("packets", state.packet_count || 0);
      initializeOverlay(state.overlay);
      updateFitLine(state.overlay.centerline_points);

      if (!state.ok || !state.latest || !state.latest.map || state.latest.map.x == null) {
        document.getElementById("status").textContent = "Waiting for usable Spa position telemetry...";
        document.getElementById("status").className = "status warning";
        return;
      }

      const latest = state.latest;
      const point = pointAtFraction(
        state.overlay.centerline_points,
        (latest.normalized_car_position || 0) + offset
      ) || { x: latest.map.x, y: latest.map.y };
      document.getElementById("status").textContent = `Live: ${latest.track || "track unknown"} (${latest.map.source})`;
      document.getElementById("status").className = "status";
      const calibratedTrail = (state.trail || [])
        .filter((row) => row.normalized_car_position != null)
        .map((row) => {
          const p = pointAtFraction(state.overlay.centerline_points, row.normalized_car_position + offset);
          return p ? [p.x, p.y] : null;
        })
        .filter(Boolean);
      document.getElementById("trail").setAttribute("d", pathFromPoints(calibratedTrail));
      document.getElementById("car").setAttribute("cx", point.x);
      document.getElementById("car").setAttribute("cy", point.y);
      setText("speed", latest.speed_kmh == null ? "-" : `${latest.speed_kmh.toFixed(0)} km/h`);
      setText("gear", latest.gear == null ? "-" : latest.gear);
      setText("throttle", latest.throttle == null ? "-" : `${Math.round(latest.throttle * 100)}%`);
      setText("brake", latest.brake == null ? "-" : `${Math.round(latest.brake * 100)}%`);
      setText("lap", latest.lap_count == null ? "-" : latest.lap_count);
      document.getElementById("coach").textContent = "Live map tracking active. If the dot is ahead, move Position offset negative until it matches your actual location.";
    }

    setInterval(() => refresh().catch(() => {}), 100);
    refresh().catch(() => {});
  </script>
</body>
</html>
"""


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def send_bytes(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_bytes(html_page().encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path == "/state":
                self.send_bytes(json.dumps(state.snapshot()).encode("utf-8"), "application/json")
                return
            if parsed.path == "/track_map.png":
                image_path = Path(state.overlay["image_path"])
                if not image_path.exists():
                    self.send_error(404, f"Missing {image_path.name}")
                    return
                self.send_bytes(image_path.read_bytes(), "image/png")
                return
            self.send_error(404, "Not found")

    return Handler


def udp_loop(state, args, ndjson_file, csv_writer):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.udp_host, args.udp_port))
    sock.settimeout(0.5)
    last_print = 0.0

    while not args.stop_event.is_set():
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            packet = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        row = normalize_packet(packet)
        state.update(row)
        ndjson_file.write(json.dumps(row) + "\n")
        csv_writer.writerow(row)

        now = time.time()
        if now - last_print >= 2.0:
            latest = state.snapshot()["latest"] or {}
            speed = latest.get("speed_kmh")
            position = latest.get("normalized_car_position")
            print(
                f"packets={state.packet_count} "
                f"track={latest.get('track') or ''} "
                f"speed={speed:.1f} "
                f"norm_pos={position:.4f}" if speed is not None and position is not None else
                f"packets={state.packet_count} waiting for complete position fields"
            )
            last_print = now

    sock.close()


def main():
    parser = argparse.ArgumentParser(description="Live Spa map for ACC telemetry")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=47777)
    parser.add_argument("--http-host", default="127.0.0.1")
    parser.add_argument("--http-port", type=int, default=8787)
    args = parser.parse_args()
    args.stop_event = threading.Event()

    overlay = load_overlay()
    run_name = args.run_name or f"live-map-{now_stamp()}"
    run_dir = RUNS_DIR / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    state = LiveState(overlay)

    ndjson_path = run_dir / "telemetry.ndjson"
    csv_path = run_dir / "telemetry.csv"
    summary_path = run_dir / "summary.json"

    with ndjson_path.open("w", encoding="utf-8") as ndjson_file, csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

        udp_thread = threading.Thread(target=udp_loop, args=(state, args, ndjson_file, writer), daemon=True)
        udp_thread.start()

        server = ThreadingHTTPServer((args.http_host, args.http_port), make_handler(state))
        server.timeout = 0.5

        print(f"Live Spa map: http://{args.http_host}:{args.http_port}")
        print(f"Listening for ACC telemetry on udp://{args.udp_host}:{args.udp_port}")
        print(f"Saving run to {run_dir}")
        print("Stop with Ctrl+C.")

        try:
            while True:
                server.handle_request()
        except KeyboardInterrupt:
            print("\nStopping live map...")
        finally:
            args.stop_event.set()
            udp_thread.join(timeout=2.0)
            ended_at = time.time()
            summary_path.write_text(json.dumps(summarize(state.rows, started_at, ended_at), indent=2), encoding="utf-8")
            print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
