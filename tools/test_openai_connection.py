#!/usr/bin/env python3
import argparse
import json
import os

from ai_coach import DEFAULT_MODEL, exception_summary, invalid_key_hint


def main():
    parser = argparse.ArgumentParser(description="Test the OpenAI connection for ACC AI Coach.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    key = os.environ.get("OPENAI_API_KEY") or ""
    if not key.strip():
        print("OpenAI connection: not configured")
        print("No OPENAI_API_KEY was provided by the app.")
        raise SystemExit(2)

    try:
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=args.model,
            input="Reply with exactly: Rachel AI connection OK",
            max_output_tokens=20,
        )
        text = (response.output_text or "").strip()
        payload = {
            "ok": True,
            "model": args.model,
            "source": "openai_responses_api",
            "message": text or "OpenAI API call succeeded.",
        }
        print("OpenAI connection: OK")
        print(json.dumps(payload, indent=2))
    except Exception as exc:
        print("OpenAI connection: FAILED")
        print(invalid_key_hint())
        print(exception_summary(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
