#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Run OCR at HH:11:00 and HH:41:00 ET (10:00-22:00; no 22:41)

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
SLEEP = 0.5
GRACE_SECONDS = 3
BASE = Path(__file__).resolve().parent
PICKS = ("pick2", "pick3", "pick4", "pick5")


def resolve_data_dir() -> Path:
    raw = (os.getenv("DATA_DIR") or "").strip()
    if not raw:
        return BASE
    path = Path(raw)
    if not path.is_absolute():
        path = (BASE.parent / path).resolve()
    return path


DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

CANDIDATES = [
    BASE / "screenshot_ocr.py",
    BASE / "site" / "screenshot_ocr.py",
    BASE.parent / "site" / "screenshot_ocr.py",
]
BOT_CANDIDATE = next((p for p in CANDIDATES if p.exists()), None)
if BOT_CANDIDATE is None:
    raise SystemExit("ERROR: screenshot_ocr.py not found near scheduler.py")
BOT = BOT_CANDIDATE.resolve()

LATEST_CANDIDATES = [
    DATA_DIR / "latest.json",
    BOT.parent / "latest.json",
    BOT.parent.parent / "site" / "latest.json",
]

LOG = (DATA_DIR / "schedule.log").resolve()


def log(msg: str):
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def day_slots(d: date):
    slots = []
    for h in range(10, 23):   # 10..22 => :11
        slots.append(datetime(d.year, d.month, d.day, h, 11, 0, tzinfo=TZ))
    for h in range(10, 22):   # 10..21 => :41 (no 22:41)
        slots.append(datetime(d.year, d.month, d.day, h, 41, 0, tzinfo=TZ))
    slots.sort()
    return slots


def draw_dt_for_run_slot(run_at: datetime) -> datetime:
    if run_at.minute == 11:
        return run_at.replace(minute=0, second=0, microsecond=0)
    if run_at.minute == 41:
        return run_at.replace(minute=30, second=0, microsecond=0)
    raise ValueError(f"Unexpected run slot minute: {run_at.minute}")


def next_run(now: datetime, last_run_at: datetime | None = None):
    grace = timedelta(seconds=GRACE_SECONDS)
    candidates = day_slots(now.date()) + day_slots((now + timedelta(days=1)).date())

    for t in candidates:
        if last_run_at is not None and t <= last_run_at:
            continue
        if now <= (t + grace):
            return t

    return day_slots((now + timedelta(days=1)).date())[0]


def read_latest():
    for p in LATEST_CANDIDATES:
        if p.exists():
            try:
                return p, json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                return p, f"read error: {e}"
    return None, "not found"


def is_final_digits(v: str, n: int) -> bool:
    return isinstance(v, str) and len(v) == n and v.isdigit()


def pick_best_entry_for_draw(data, expected_iso: str):
    matches = [
        e for e in data
        if isinstance(e, dict) and e.get("draw_id") == expected_iso
    ]
    if not matches:
        return None

    def sort_key(e):
        return (
            1 if e.get("status") == "final" else 0,
            str(e.get("captured_at", "")),
        )

    matches.sort(key=sort_key)
    return matches[-1]


def previous_draw_iso(expected_iso: str) -> str | None:
    try:
        draw_dt = datetime.fromisoformat(expected_iso)
    except ValueError:
        return None
    return (draw_dt - timedelta(minutes=30)).isoformat()


def same_pick_set(left: dict, right: dict) -> bool:
    return all((left.get(name, "") or "") == (right.get(name, "") or "") for name in PICKS)


def validate_latest_for_slot(expected_iso: str):
    path, data = read_latest()

    if isinstance(data, str):
        return False, f"latest.json @ {path}: {data}"

    if not (isinstance(data, list) and data):
        return False, f"latest.json missing or empty @ {path}"

    entry = pick_best_entry_for_draw(data, expected_iso)
    if not entry:
        return False, f"no entry for draw_id={expected_iso}"

    if entry.get("status") != "final":
        return False, f"entry not final (status={entry.get('status')})"

    ok = (
        is_final_digits(entry.get("pick2", ""), 2)
        and is_final_digits(entry.get("pick3", ""), 3)
        and is_final_digits(entry.get("pick4", ""), 4)
        and is_final_digits(entry.get("pick5", ""), 5)
    )
    if not ok:
        return False, "picks incomplete or wrong length"

    prev_iso = previous_draw_iso(expected_iso)
    if prev_iso:
        prev_entry = pick_best_entry_for_draw(data, prev_iso)
        if prev_entry and prev_entry.get("status") == "final" and same_pick_set(entry, prev_entry):
            return False, f"entry matches previous draw {prev_iso}"

    return (
        True,
        f"OK - picks={entry.get('pick2')} {entry.get('pick3')} {entry.get('pick4')} {entry.get('pick5')}",
    )


def run_ocr_once(expected_iso: str):
    cmd = [sys.executable, str(BOT), "--now", "--draw-id", expected_iso]
    log(f"Running OCR: {' '.join(cmd)} (cwd={BOT.parent})")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(BOT.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=240,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        captured = e.stdout or ""
        if captured:
            for line in captured.splitlines():
                line = line.strip()
                if line:
                    log(f"OCR> {line}")
        return False, "OCR timeout after 240s", -998
    except Exception as e:
        return False, f"start/run error: {e}", -999

    output = result.stdout or ""
    for line in output.splitlines():
        line = line.strip()
        if line:
            log(f"OCR> {line}")

    exitcode = result.returncode
    log(f"OCR exit={exitcode}")

    ok, note = validate_latest_for_slot(expected_iso)
    if exitcode != 0 and not ok:
        note = f"OCR failed with exit={exitcode}; {note}"
    return ok, note, exitcode


def try_with_retries(run_at: datetime):
    expected_iso = draw_dt_for_run_slot(run_at).isoformat()
    delays = [0, 45, 90]

    last_note = ""
    last_exit = None

    log(
        f"Scheduled slot={run_at.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"-> expected draw_id={expected_iso}"
    )

    for attempt, delay in enumerate(delays, start=1):
        if delay > 0:
            log(f"Retry policy: sleeping {delay}s before attempt {attempt}")
            time.sleep(delay)

        log(f"Attempt {attempt}/3 for draw_id={expected_iso}")
        ok, note, exitcode = run_ocr_once(expected_iso)
        last_note, last_exit = note, exitcode

        if ok:
            log(f"SUCCESS draw_id={expected_iso} - {note}")
            return

        log(f"Attempt {attempt} FAILED - {note} - exit={exitcode}")

    log(
        f"FAIL draw_id={expected_iso} after 3 attempts "
        f"- last_note='{last_note}' - last_exit={last_exit}"
    )


def sleep_until(target: datetime):
    while True:
        now = datetime.now(TZ)
        if now >= target:
            return
        remaining = (target - now).total_seconds()
        time.sleep(min(SLEEP, max(0.05, remaining)))


def main():
    if "--now" in sys.argv:
        slot = next_run(datetime.now(TZ), last_run_at=None)
        now = datetime.now(TZ)
        if now < slot:
            log(f"--now mode: waiting until {slot.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            sleep_until(slot)
        try_with_retries(slot)
        return

    if "--debug" in sys.argv:
        now = datetime.now(TZ)
        upcoming = []
        probe = now
        last = None
        for _ in range(8):
            nxt = next_run(probe, last_run_at=last)
            upcoming.append(nxt)
            last = nxt
            probe = nxt + timedelta(seconds=1)

        for t in upcoming:
            draw = draw_dt_for_run_slot(t)
            print(
                "DEBUG next slot:",
                t.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "-> draw:",
                draw.strftime("%Y-%m-%d %H:%M:%S %Z"),
            )
        return

    log(f"Scheduler up (:11:00, :41:00) - BOT={BOT}")
    last_run_at = None

    while True:
        now = datetime.now(TZ)
        run_at = next_run(now, last_run_at=last_run_at)

        wait_secs = (run_at - now).total_seconds()
        if wait_secs > 0:
            log(f"Next run at {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')} (in {int(wait_secs)}s)")
            sleep_until(run_at)
        else:
            log(f"Within grace window for {run_at.strftime('%Y-%m-%d %H:%M:%S %Z')} -> running now")

        try_with_retries(run_at)
        last_run_at = run_at


if __name__ == "__main__":
    main()
