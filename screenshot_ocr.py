#!/usr/bin/env python3
import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "site" / "screenshot_ocr.py"

if not SCRIPT.exists():
    raise SystemExit(f"Missing delegated script: {SCRIPT}")

sys.argv[0] = str(SCRIPT)
runpy.run_path(str(SCRIPT), run_name="__main__")
