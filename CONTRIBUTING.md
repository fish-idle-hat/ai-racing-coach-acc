# Contributing

This project is in active prototype development. Contributions are most useful when they improve one of the remaining mechanisms listed in `PARTNERS.md`.

## Before Opening a PR

1. Keep changes focused.
2. Avoid committing generated `runs/`, `build/`, or `submission/` files.
3. Run the relevant local checks when possible:

```bash
python3 tools/validate_app_modes.py Spa-beginner-run-03
python3 tools/coach_regression_check.py
```

Some checks require local saved telemetry runs that are intentionally not committed to GitHub.

## Development Notes

- Core live coaching is Python.
- The native macOS app is SwiftUI.
- The ACC telemetry helper is C# and runs inside the CrossOver/Windows environment.
- The project currently focuses on Spa and the McLaren 720S GT3 Evo path.
