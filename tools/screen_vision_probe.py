#!/usr/bin/env python3
import argparse
import shutil
import json
import subprocess
from datetime import datetime
from pathlib import Path

from reference_compare import ROOT


OUT_DIR = ROOT / "screen_captures"


def capture_screen(output):
    command = ["screencapture", "-x", str(output)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return result.returncode, result.stderr.strip()


def run_ocr(image_path, lang="eng"):
    if shutil.which("tesseract") is None:
        return {
            "available": False,
            "status": "tesseract_cli_missing",
            "text": "",
            "error": "Install the tesseract CLI later if we need real HUD OCR.",
        }
    command = ["tesseract", str(image_path), "stdout", "-l", lang]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return {
        "available": result.returncode == 0,
        "status": "ok" if result.returncode == 0 else "ocr_failed",
        "text": result.stdout.strip(),
        "error": result.stderr.strip(),
    }


def write_markdown(report_path, report):
    lines = [
        "# ACC Screen Vision Probe",
        "",
        f"Created: {report['created_at']}",
        f"Image: `{report['image_path']}`",
        f"Capture ok: {report['capture_ok']}",
        f"OCR status: {report['ocr']['status']}",
        "",
        "## Role",
        "",
        "Screen vision is a secondary source only. Telemetry remains authoritative unless the OCR result is clear and the telemetry field is missing or inconsistent.",
        "",
        "## OCR Text",
        "",
    ]
    text = report["ocr"].get("text") or ""
    lines.append("```text")
    lines.append(text[:3000] if text else "(no OCR text)")
    lines.append("```")
    if report.get("error"):
        lines.extend(["", "## Capture Error", "", f"```text\n{report['error']}\n```"])
    report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Secondary screen-vision/OCR probe for ACC/CrossOver.")
    parser.add_argument("--tag", default="acc-screen")
    parser.add_argument("--ocr", action="store_true", help="Try OCR with the local tesseract CLI if installed.")
    parser.add_argument("--lang", default="eng", help="Tesseract language code for --ocr.")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    image_path = OUT_DIR / f"{args.tag}-{stamp}.png"
    code, error = capture_screen(image_path)
    ocr = run_ocr(image_path, lang=args.lang) if args.ocr and code == 0 and image_path.exists() else {
        "available": False,
        "status": "disabled",
        "text": "",
        "error": "Run with --ocr to try local OCR.",
    }
    report = {
        "schema": "acc_ai_coach_screen_probe_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "image_path": str(image_path),
        "capture_ok": code == 0 and image_path.exists(),
        "ocr": ocr,
        "authority": "secondary_source_only",
        "notes": [
            "This is a secondary source only.",
            "Use it to verify HUD/lap/invalid markers when telemetry is questionable.",
            "macOS Screen Recording permission may be required for useful captures.",
            "OCR text is never allowed to override reliable telemetry without confidence checks.",
        ],
        "error": error,
    }
    report_path = OUT_DIR / f"{args.tag}-{stamp}.json"
    md_path = OUT_DIR / f"{args.tag}-{stamp}.md"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(md_path, report)
    print(f"Screen capture: {image_path}")
    print(f"Probe report: {report_path}")
    print(f"Probe markdown: {md_path}")
    print(f"Capture ok: {report['capture_ok']}")
    print(f"OCR status: {report['ocr']['status']}")


if __name__ == "__main__":
    main()
