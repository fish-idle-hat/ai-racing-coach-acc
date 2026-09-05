#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from realtime_coach import ROOT, RUNS_DIR


TOOLS_DIR = ROOT / "tools"
HELPER_COMMAND = './windows-helper/AccTelemetryForwarderNetFx/run-in-crossover.sh'


def now_stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_run_name(value):
    value = (value or "").strip()
    if not value:
        return f"acc-app-coach-{now_stamp()}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)[:80].strip("-") or f"acc-app-coach-{now_stamp()}"


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_text(path, limit=12000):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return text[:limit]


def latest_runs(limit=40):
    runs = []
    if not RUNS_DIR.exists():
        return runs
    for path in RUNS_DIR.iterdir():
        if not path.is_dir():
            continue
        summary = read_json(path / "summary.json", {})
        has_data = (path / "telemetry.csv").exists() or bool(summary)
        if not has_data:
            continue
        runs.append(
            {
                "name": path.name,
                "path": str(path),
                "modified_at": path.stat().st_mtime,
                "packets": summary.get("packets") or summary.get("packet_count"),
                "duration_seconds": summary.get("duration_seconds"),
                "track": summary.get("track") or summary.get("first_track") or "",
                "has_replay": (path / "coach_replay.md").exists(),
                "has_decisions": (path / "coach_decision_report.md").exists(),
                "has_profile": (path / "driver_profile_update.md").exists(),
                "has_ai_context": (path / "coach_ai_context.json").exists(),
            }
        )
    runs.sort(key=lambda item: item["modified_at"], reverse=True)
    return runs[:limit]


class CoachProcess:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.run_name = None
        self.started_at = None
        self.output = []

    def start(self, run_name, voice=True):
        with self.lock:
            if self.process and self.process.poll() is None:
                return False, "Rachel is already running."
            run_name = safe_run_name(run_name)
            command = [
                "python3",
                str(TOOLS_DIR / "realtime_coach.py"),
                "--run-name",
                run_name,
            ]
            if voice:
                command.append("--voice")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(TOOLS_DIR)
            self.output = []
            self.process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self.run_name = run_name
            self.started_at = time.time()
            threading.Thread(target=self._read_output, daemon=True).start()
            return True, run_name

    def _read_output(self):
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            with self.lock:
                self.output.append(line.rstrip())
                self.output = self.output[-160:]

    def stop(self):
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return False, "Rachel is not running."
            process.send_signal(2)
            run_name = self.run_name
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=4)
        return True, run_name

    def status(self):
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            return {
                "running": running,
                "run_name": self.run_name,
                "started_at": self.started_at,
                "uptime_seconds": round(time.time() - self.started_at, 1) if running and self.started_at else 0,
                "output": self.output[-80:],
            }


class AppState:
    def __init__(self):
        self.coach = CoachProcess()


def run_command(args, timeout=30):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(TOOLS_DIR)
    result = subprocess.run(
        args,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_details(name):
    run_dir = RUNS_DIR / safe_run_name(name)
    if not run_dir.exists():
        return None
    reports = {}
    for key, filename in {
        "summary": "summary.json",
        "replay": "coach_replay.md",
        "decisions": "coach_decision_report.md",
        "profile": "driver_profile_update.md",
        "turn_timing": "turn_timing.md",
        "reference": "reference_comparison.md",
    }.items():
        path = run_dir / filename
        if path.exists():
            reports[key] = {
                "path": str(path),
                "content": read_json(path, {}) if filename.endswith(".json") else read_text(path),
            }
    return {"name": run_dir.name, "path": str(run_dir), "reports": reports}


def html_page():
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ACC AI Coach</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0b1220;
      color: #e8edf7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      background: #0b1220;
    }
    nav {
      border-right: 1px solid #273246;
      background: #101827;
      padding: 18px;
      overflow: auto;
    }
    main {
      padding: 20px;
      display: grid;
      gap: 16px;
      align-content: start;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 22px; }
    h2 { font-size: 16px; color: #c7d2e5; }
    h3 { font-size: 14px; color: #c7d2e5; }
    button, input, label {
      font: inherit;
    }
    button {
      border: 1px solid #334155;
      background: #172033;
      color: #e8edf7;
      border-radius: 8px;
      padding: 9px 11px;
      cursor: pointer;
    }
    button.primary { background: #2563eb; border-color: #3b82f6; }
    button.danger { background: #7f1d1d; border-color: #b91c1c; }
    button:disabled { opacity: .45; cursor: default; }
    input[type="text"] {
      width: 100%;
      border: 1px solid #334155;
      background: #0b1220;
      color: #e8edf7;
      border-radius: 8px;
      padding: 9px 10px;
    }
    .panel {
      border: 1px solid #273246;
      background: #101827;
      border-radius: 8px;
      padding: 14px;
    }
    .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .stack { display: grid; gap: 10px; }
    .muted { color: #97a6bd; }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: #c7d2e5;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #64748b;
    }
    .dot.live { background: #22c55e; }
    .runs {
      display: grid;
      gap: 8px;
      margin-top: 14px;
    }
    .run-item {
      width: 100%;
      text-align: left;
      border-radius: 8px;
      padding: 10px;
    }
    .run-item.active { border-color: #60a5fa; background: #172554; }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      border: 1px solid #273246;
      background: #0b1220;
      border-radius: 8px;
      padding: 12px;
      min-height: 72px;
    }
    .stat strong { display: block; font-size: 24px; margin-top: 6px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow: auto;
      max-height: 460px;
      line-height: 1.45;
      color: #dbe7ff;
    }
    .tabs { display: flex; gap: 8px; flex-wrap: wrap; }
    .tab.active { background: #1d4ed8; border-color: #60a5fa; }
    code { color: #bfdbfe; }
    @media (max-width: 900px) {
      body { grid-template-columns: 1fr; }
      nav { border-right: 0; border-bottom: 1px solid #273246; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <nav>
    <div class="stack">
      <h1>ACC AI Coach</h1>
      <div class="status"><span id="statusDot" class="dot"></span><span id="statusText">Checking...</span></div>
      <label class="stack">
        <span class="muted">Run name</span>
        <input id="runName" type="text" value="">
      </label>
      <label class="row">
        <input id="voice" type="checkbox" checked>
        <span>Voice</span>
      </label>
      <div class="row">
        <button id="startBtn" class="primary">Start Rachel</button>
        <button id="stopBtn" class="danger">Stop</button>
      </div>
      <div class="panel stack">
        <h3>Terminal 2 Helper</h3>
        <p class="muted">Run this after Rachel starts:</p>
        <pre><code>cd "/Users/larryrus/Documents/ChatGPT/ACC AI COACH"
./windows-helper/AccTelemetryForwarderNetFx/run-in-crossover.sh</code></pre>
      </div>
      <div>
        <div class="row" style="justify-content: space-between;">
          <h2>Runs</h2>
          <button id="refreshBtn">Refresh</button>
        </div>
        <div id="runs" class="runs"></div>
      </div>
    </div>
  </nav>
  <main>
    <section class="panel stack">
      <div class="row" style="justify-content: space-between;">
        <div>
          <h2 id="selectedTitle">No run selected</h2>
          <p id="selectedPath" class="muted"></p>
        </div>
        <div class="row">
          <button id="replayBtn">Generate Replay</button>
          <button id="validateBtn">Validate</button>
          <button id="profileBtn">Update Profile</button>
        </div>
      </div>
      <div class="grid">
        <div class="stat"><span class="muted">Rachel</span><strong id="statRachel">-</strong></div>
        <div class="stat"><span class="muted">Run</span><strong id="statRun">-</strong></div>
        <div class="stat"><span class="muted">Packets</span><strong id="statPackets">-</strong></div>
        <div class="stat"><span class="muted">Reports</span><strong id="statReports">-</strong></div>
      </div>
    </section>
    <section class="panel stack">
      <div class="tabs">
        <button class="tab active" data-tab="replay">Replay</button>
        <button class="tab" data-tab="decisions">Decisions</button>
        <button class="tab" data-tab="profile">Driver Profile</button>
        <button class="tab" data-tab="terminal">Rachel Output</button>
      </div>
      <pre id="content">Select a run from the left.</pre>
    </section>
  </main>
  <script>
    let state = { status: null, runs: [], selected: null, selectedDetails: null, tab: "replay" };

    function $(id) { return document.getElementById(id); }
    async function api(path, options) {
      const res = await fetch(path, options);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    }
    function defaultRunName() {
      const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/T/, "-").slice(0, 15);
      return `acc-app-test-${stamp}`;
    }
    function reportCount(run) {
      return ["has_replay", "has_decisions", "has_profile", "has_ai_context"].filter(k => run[k]).length;
    }
    function formatTime(seconds) {
      if (!seconds && seconds !== 0) return "-";
      const m = Math.floor(seconds / 60);
      const s = Math.round(seconds % 60);
      return `${m}:${String(s).padStart(2, "0")}`;
    }
    function renderStatus() {
      const status = state.status || {};
      $("statusDot").className = `dot ${status.running ? "live" : ""}`;
      $("statusText").textContent = status.running ? `Rachel running: ${status.run_name}` : "Rachel stopped";
      $("statRachel").textContent = status.running ? formatTime(status.uptime_seconds) : "Stopped";
      $("statRun").textContent = status.run_name || (state.selected ? state.selected.name : "-");
      $("stopBtn").disabled = !status.running;
      $("startBtn").disabled = !!status.running;
    }
    function renderRuns() {
      const box = $("runs");
      box.innerHTML = "";
      for (const run of state.runs) {
        const btn = document.createElement("button");
        btn.className = `run-item ${state.selected && state.selected.name === run.name ? "active" : ""}`;
        btn.innerHTML = `<strong>${run.name}</strong><br><span class="muted">${run.packets || "-"} packets · ${reportCount(run)} reports</span>`;
        btn.onclick = () => selectRun(run.name);
        box.appendChild(btn);
      }
    }
    function renderSelected() {
      const run = state.selected;
      $("selectedTitle").textContent = run ? run.name : "No run selected";
      $("selectedPath").textContent = run ? run.path : "";
      $("statPackets").textContent = run ? (run.packets || "-") : "-";
      $("statReports").textContent = run ? String(reportCount(run)) : "-";
      renderContent();
    }
    function activeReportContent() {
      const details = state.selectedDetails;
      if (!details) return "Select a run from the left.";
      if (state.tab === "terminal") return (state.status && state.status.output || []).join("\\n") || "(no live output)";
      const report = details.reports[state.tab];
      if (!report) return `No ${state.tab} report yet. Use the buttons above to generate it.`;
      if (typeof report.content === "object") return JSON.stringify(report.content, null, 2);
      return report.content || "(empty report)";
    }
    function renderContent() {
      $("content").textContent = activeReportContent();
      document.querySelectorAll(".tab").forEach(btn => btn.classList.toggle("active", btn.dataset.tab === state.tab));
    }
    async function refresh() {
      state.status = await api("/api/status");
      state.runs = (await api("/api/runs")).runs;
      if (!state.selected && state.runs.length) await selectRun(state.runs[0].name, false);
      renderStatus();
      renderRuns();
      renderSelected();
    }
    async function selectRun(name, rerender = true) {
      state.selected = state.runs.find(run => run.name === name) || { name };
      state.selectedDetails = await api(`/api/run?name=${encodeURIComponent(name)}`);
      if (rerender) {
        renderRuns();
        renderSelected();
      }
    }
    async function postAction(path, body) {
      const data = await api(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      await refresh();
      return data;
    }
    $("runName").value = defaultRunName();
    $("startBtn").onclick = () => postAction("/api/start", { run_name: $("runName").value, voice: $("voice").checked }).catch(alert);
    $("stopBtn").onclick = () => postAction("/api/stop").catch(alert);
    $("refreshBtn").onclick = () => refresh().catch(alert);
    $("replayBtn").onclick = () => state.selected && postAction("/api/replay", { run_name: state.selected.name }).catch(alert);
    $("validateBtn").onclick = () => state.selected && postAction("/api/validate", { run_name: state.selected.name }).catch(alert);
    $("profileBtn").onclick = () => state.selected && postAction("/api/profile", { run_name: state.selected.name }).catch(alert);
    document.querySelectorAll(".tab").forEach(btn => btn.onclick = () => { state.tab = btn.dataset.tab; renderContent(); });
    refresh().catch(err => { $("content").textContent = String(err); });
    setInterval(refresh, 2500);
  </script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    state = AppState()

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = html_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/status":
            self.send_json(self.state.coach.status())
            return
        if parsed.path == "/api/runs":
            self.send_json({"runs": latest_runs()})
            return
        if parsed.path == "/api/run":
            name = parse_qs(parsed.query).get("name", [""])[0]
            details = run_details(name)
            if details is None:
                self.send_json({"error": "Run not found"}, status=404)
                return
            self.send_json(details)
            return
        self.send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        payload = self.read_body()
        run_name = safe_run_name(payload.get("run_name"))
        try:
            if parsed.path == "/api/start":
                ok, message = self.state.coach.start(run_name, voice=bool(payload.get("voice", True)))
                self.send_json({"ok": ok, "message": message, "helper_command": HELPER_COMMAND}, status=200 if ok else 409)
                return
            if parsed.path == "/api/stop":
                ok, message = self.state.coach.stop()
                self.send_json({"ok": ok, "message": message}, status=200 if ok else 409)
                return
            if parsed.path == "/api/replay":
                result = run_command(["python3", str(TOOLS_DIR / "replay_coach.py"), run_name], timeout=60)
                self.send_json(result, status=200 if result["ok"] else 500)
                return
            if parsed.path == "/api/validate":
                result = run_command(["python3", str(TOOLS_DIR / "validate_milestone_acceptance.py"), run_name], timeout=60)
                self.send_json(result, status=200 if result["ok"] else 500)
                return
            if parsed.path == "/api/profile":
                result = run_command(["python3", str(TOOLS_DIR / "driver_model.py"), str(RUNS_DIR / run_name)], timeout=60)
                self.send_json(result, status=200 if result["ok"] else 500)
                return
        except subprocess.TimeoutExpired as exc:
            self.send_json({"ok": False, "error": f"Command timed out: {exc}"}, status=500)
            return
        self.send_json({"error": "Not found"}, status=404)


def main():
    parser = argparse.ArgumentParser(description="Milestone 5 local dashboard for ACC AI Coach.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ACC AI Coach app: http://{args.host}:{args.port}")
    print("Use the dashboard to start/stop Rachel. Run the CrossOver helper separately when prompted.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCoach app stopped.")
        Handler.state.coach.stop()


if __name__ == "__main__":
    main()
