#!/usr/bin/env python3
# States API for remote controller + static site hosting.
import atexit
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, time as d_time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from waitress import serve as waitress_serve
except Exception:
    waitress_serve = None

TZ = ZoneInfo("America/New_York")
BASE = Path(__file__).resolve().parent
SITE = (BASE / "site").resolve()
SITE.mkdir(parents=True, exist_ok=True)

def resolve_data_dir() -> Path:
    raw = (os.getenv("DATA_DIR") or "").strip()
    if not raw:
        return SITE
    path = Path(raw)
    if not path.is_absolute():
        path = (BASE / path).resolve()
    return path

DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATES_JSON = (DATA_DIR / "states.json").resolve()
LATEST_JSON = (DATA_DIR / "latest.json").resolve()
SCHEDULE_LOG = (DATA_DIR / "schedule.log").resolve()
SCHEDULER_PID_FILE = (DATA_DIR / "scheduler.pid").resolve()
SCHEDULER_PY = (BASE / "scheduler.py").resolve()
PAGE_SCREENSHOT = (DATA_DIR / "page.png").resolve()
DAILY_RESET_MARKER = (DATA_DIR / ".daily_reset_date").resolve()

_SCHEDULER_PROC = None
_SCHEDULER_LOG_HANDLE = None
_SCHEDULER_MONITOR = None
_SCHEDULER_STOP = threading.Event()
_DAILY_RESET_THREAD = None
_DAILY_RESET_STOP = threading.Event()

ALLOWED_STATES = [
    "Georgia Morning","Georgia Evening","Georgia Night",
    "New Jersey Day","New Jersey Night",
    "Connecticut Day","Connecticut Night",
    "Florida Day","Florida Night",
    "Pennsylvania Day","Pennsylvania Night",
    "New York Day","New York Night",
]
PICK_LENGTHS = {
    "pick2": 2,
    "pick3": 3,
    "pick4": 4,
    "pick5": 5,
}

def now_et():
    return datetime.now(TZ)

def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}

def ensure_runtime_files():
    defaults = {
        STATES_JSON: [],
        LATEST_JSON: [],
    }
    for path, payload in defaults.items():
        if path.exists():
            continue
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

def load_states():
    if not STATES_JSON.exists():
        return []
    try:
        data = json.loads(STATES_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def load_latest_entries():
    if not LATEST_JSON.exists():
        return []
    try:
        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []


def previous_draw_id(draw_id: str) -> str | None:
    try:
        draw_dt = datetime.fromisoformat(draw_id)
    except Exception:
        return None
    return (draw_dt - timedelta(minutes=30)).isoformat()


def same_latest_picks(left: dict, right: dict) -> bool:
    return all((left.get(name, "") or "") == (right.get(name, "") or "") for name in PICK_LENGTHS)


def clean_duplicate_entries(entries: list[dict]) -> tuple[list[dict], bool]:
    clean_entries = [e for e in entries if isinstance(e, dict)]
    by_draw = {}
    for entry in clean_entries:
        draw_id = entry.get("draw_id")
        if not draw_id:
            continue
        current = by_draw.get(draw_id)
        if current is None:
            by_draw[draw_id] = entry
            continue
        current_final = current.get("status") == "final"
        entry_final = entry.get("status") == "final"
        if (not current_final and entry_final) or (entry.get("captured_at", "") > current.get("captured_at", "")):
            by_draw[draw_id] = entry

    cleaned = []
    removed = False
    for entry in clean_entries:
        if entry.get("status") != "final":
            cleaned.append(entry)
            continue

        prev_id = previous_draw_id(entry.get("draw_id", ""))
        prev_entry = by_draw.get(prev_id or "")
        if prev_entry and prev_entry.get("status") == "final" and same_latest_picks(entry, prev_entry):
            removed = True
            continue

        cleaned.append(entry)

    return cleaned, removed


def atomic_write(path: Path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""

def append_scheduler_log(message: str):
    ts = now_et().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] supervisor: {message}\n"
    try:
        SCHEDULE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SCHEDULE_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def read_scheduler_pid() -> int | None:
    try:
        raw = SCHEDULER_PID_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return int(raw)
    except Exception:
        return None

def pid_file_running() -> bool:
    pid = read_scheduler_pid()
    if pid is None:
        return False
    return process_is_alive(pid)

def remove_scheduler_pid_file():
    try:
        SCHEDULER_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass

def terminate_process(pid: int, timeout: float = 10.0):
    if pid <= 0 or pid == os.getpid():
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_is_alive(pid):
            return
        time.sleep(0.2)

    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass

def close_scheduler_log_handle():
    global _SCHEDULER_LOG_HANDLE
    if _SCHEDULER_LOG_HANDLE is None:
        return
    try:
        _SCHEDULER_LOG_HANDLE.close()
    except Exception:
        pass
    finally:
        _SCHEDULER_LOG_HANDLE = None

def digits_only(value) -> str:
    return re.sub(r"\D+", "", str(value or ""))

def sanitize_latest_pick(name: str, value) -> str:
    digits = digits_only(value)
    expected = PICK_LENGTHS[name]
    if digits and len(digits) != expected:
        raise ValueError(f"{name} must have exactly {expected} digits")
    return digits

def parse_manual_draw_dt(date_str: str, time_str: str) -> datetime:
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise ValueError("draw_date must use YYYY-MM-DD")
    if not re.fullmatch(r"\d{2}:\d{2}", time_str):
        raise ValueError("draw_time must use HH:MM")

    year, month, day = map(int, date_str.split("-"))
    hour, minute = map(int, time_str.split(":"))

    if minute not in {0, 30}:
        raise ValueError("draw_time must be on the half hour")
    if hour < 10 or hour > 22 or (hour == 22 and minute != 0):
        raise ValueError("draw_time must be between 10:00 and 22:00 ET")

    return datetime(year, month, day, hour, minute, 0, tzinfo=TZ)

def build_manual_latest_entry(payload: dict) -> dict:
    draw_dt = parse_manual_draw_dt(payload.get("draw_date"), payload.get("draw_time"))
    picks = {name: sanitize_latest_pick(name, payload.get(name)) for name in PICK_LENGTHS}

    if not any(picks.values()):
        raise ValueError("enter at least one result")

    status = (payload.get("status") or "final").strip().lower()
    if status not in {"final", "pending"}:
        raise ValueError("status must be final or pending")

    entry = {
        "draw_id": draw_dt.isoformat(),
        "captured_at": now_et().isoformat(timespec="seconds"),
        "status": status,
        "source": "manual",
        **picks,
    }
    return entry

def upsert_latest_entry(entry: dict) -> bool:
    data = [e for e in load_latest_entries() if isinstance(e, dict)]
    replaced = any((e.get("draw_id") or "") == entry["draw_id"] for e in data)
    data = [e for e in data if (e.get("draw_id") or "") != entry["draw_id"]]
    data.append(entry)
    data.sort(key=lambda e: (e.get("draw_id") or "", e.get("captured_at") or ""))
    atomic_write(LATEST_JSON, data)
    return replaced

def delete_latest_entry(draw_id: str) -> bool:
    draw_id = (draw_id or "").strip()
    if not draw_id:
        return False
    data = [e for e in load_latest_entries() if isinstance(e, dict)]
    new_data = [e for e in data if (e.get("draw_id") or "") != draw_id]
    if len(new_data) == len(data):
        return False
    atomic_write(LATEST_JSON, new_data)
    return True

def only_digits(s: str, n: int) -> str:
    if not s: return ""
    return re.sub(r"\D+", "", str(s))[:n]

def clamp_time_str(s: str) -> d_time:
    # expects "HH:MM"
    try:
        hh, mm = s.split(":")
        return d_time(hour=max(0,min(23,int(hh))), minute=max(0,min(59,int(mm))))
    except Exception:
        return d_time(10, 0)

def draw_dt_et(day: str, hhmm: str) -> datetime:
    # day: "today" | "yesterday"
    base = now_et().date()
    if (day or "").lower() == "yesterday":
        base = base - timedelta(days=1)
    tt = clamp_time_str(hhmm)
    return datetime.combine(base, tt, TZ)

def make_id(state: str, segment: str, when_iso: str) -> str:
    return f"{state}|{segment}|{when_iso}"

def sanitize_item_in(item: dict) -> dict:
    state = (item.get("state") or "").strip()
    segment = (item.get("segment") or state).strip()
    day = (item.get("day") or "today").strip().lower()
    time_str = (item.get("time") or "10:00").strip()

    pick3 = only_digits(item.get("pick3"), 3)
    pick4 = only_digits(item.get("pick4"), 4)

    # Enforce allowed states
    if state not in ALLOWED_STATES:
        raise ValueError(f"invalid state: {state}")

    if day not in ("today","yesterday"):
        day = "today"

    dt = draw_dt_et(day, time_str)
    when_iso = dt.isoformat()

    return {
        "id": make_id(state, segment, when_iso),
        "state": state,
        "segment": segment or state,
        "type": day,                       # today | yesterday
        "draw_time_et": when_iso,
        "pick3": pick3,                    # "" or 3 digits
        "pick4": pick4,                    # "" or 4 digits
    }

def upsert(items_in: list) -> int:
    data = load_states()
    index = { (e.get("id") or ""): i for i, e in enumerate(data) }
    changed = 0
    now_iso = now_et().isoformat(timespec="seconds")

    for raw in items_in:
        try:
            item = sanitize_item_in(raw)
        except ValueError:
            # skip invalid state
            continue

        rec = {
            "id": item["id"],
            "state": item["state"],
            "segment": item["segment"],
            "type": item["type"],
            "draw_time_et": item["draw_time_et"],
            "pick3": item["pick3"],
            "pick4": item["pick4"],
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        if item["id"] in index:
            i = index[item["id"]]
            # update existing
            old = data[i]
            old["pick3"] = rec["pick3"]
            old["pick4"] = rec["pick4"]
            old["segment"] = rec["segment"]
            old["type"] = rec["type"]
            old["updated_at"] = now_iso
            changed += 1
        else:
            data.append(rec)
            index[item["id"]] = len(data) - 1
            changed += 1

    # sort by draw time ascending, then state
    data.sort(key=lambda e: (e.get("draw_time_et",""), e.get("state","")))
    atomic_write(STATES_JSON, data)
    return changed

def update_one(rec_id: str, pick3: str, pick4: str):
    data = load_states()
    idx = { (e.get("id") or ""): i for i, e in enumerate(data) }
    if rec_id not in idx:
        return None
    i = idx[rec_id]
    e = data[i]
    p3 = only_digits(pick3, 3)
    p4 = only_digits(pick4, 4)
    e["pick3"] = p3
    e["pick4"] = p4
    e["updated_at"] = now_et().isoformat(timespec="seconds")
    atomic_write(STATES_JSON, data)
    return e

def delete_one(rec_id: str) -> bool:
    data = load_states()
    new_data = [e for e in data if (e.get("id") or "") != rec_id]
    if len(new_data) == len(data):
        return False
    atomic_write(STATES_JSON, new_data)
    return True

def launch_scheduler_process():
    global _SCHEDULER_LOG_HANDLE, _SCHEDULER_PROC

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    SCHEDULE_LOG.parent.mkdir(parents=True, exist_ok=True)
    close_scheduler_log_handle()
    append_scheduler_log(f"launching scheduler -> {SCHEDULER_PY}")
    _SCHEDULER_PROC = subprocess.Popen(
        [sys.executable, str(SCHEDULER_PY)],
        cwd=str(BASE),
        env=env,
        stdin=subprocess.DEVNULL,
    )
    try:
        SCHEDULER_PID_FILE.write_text(str(_SCHEDULER_PROC.pid), encoding="utf-8")
    except Exception:
        pass
    return _SCHEDULER_PROC

def scheduler_supervisor_loop():
    global _SCHEDULER_PROC
    last_exit = None

    while not _SCHEDULER_STOP.is_set():
        proc = _SCHEDULER_PROC
        if proc is None:
            try:
                launch_scheduler_process()
            except Exception as exc:
                append_scheduler_log(f"failed to launch scheduler: {exc}")
                _SCHEDULER_STOP.wait(10)
                continue
            _SCHEDULER_STOP.wait(2)
            continue

        exit_code = proc.poll()
        if exit_code is None:
            _SCHEDULER_STOP.wait(5)
            continue

        if exit_code != last_exit:
            append_scheduler_log(f"scheduler exited with code {exit_code}; restarting in 5s")
            last_exit = exit_code
        close_scheduler_log_handle()
        _SCHEDULER_PROC = None
        _SCHEDULER_STOP.wait(5)

def start_scheduler_if_enabled():
    global _SCHEDULER_MONITOR

    if not env_flag("RUN_OCR_SCHEDULER", default=False):
        return None
    if not SCHEDULER_PY.exists():
        return None
    if pid_file_running():
        return None
    if _SCHEDULER_PROC is not None and _SCHEDULER_PROC.poll() is None:
        return _SCHEDULER_PROC
    if _SCHEDULER_MONITOR is not None and _SCHEDULER_MONITOR.is_alive():
        if _SCHEDULER_STOP.is_set():
            _SCHEDULER_MONITOR.join(timeout=2)
        if _SCHEDULER_MONITOR.is_alive():
            return _SCHEDULER_PROC
        _SCHEDULER_MONITOR = None

    _SCHEDULER_STOP.clear()
    try:
        launch_scheduler_process()
    except Exception as exc:
        append_scheduler_log(f"initial scheduler launch failed: {exc}")

    _SCHEDULER_MONITOR = threading.Thread(
        target=scheduler_supervisor_loop,
        name="scheduler-supervisor",
        daemon=True,
    )
    _SCHEDULER_MONITOR.start()
    return _SCHEDULER_PROC

def stop_scheduler():
    global _SCHEDULER_PROC, _SCHEDULER_MONITOR
    _SCHEDULER_STOP.set()
    pid = read_scheduler_pid()

    if _SCHEDULER_PROC is not None and _SCHEDULER_PROC.poll() is None:
        try:
            _SCHEDULER_PROC.terminate()
            _SCHEDULER_PROC.wait(timeout=10)
        except Exception:
            try:
                _SCHEDULER_PROC.kill()
            except Exception:
                pass

    _SCHEDULER_PROC = None
    if pid is not None:
        terminate_process(pid)
    remove_scheduler_pid_file()
    close_scheduler_log_handle()

    monitor = _SCHEDULER_MONITOR
    if monitor is not None and monitor.is_alive() and threading.current_thread() is not monitor:
        monitor.join(timeout=6)
    if monitor is not None and not monitor.is_alive():
        _SCHEDULER_MONITOR = None

def daily_reset_enabled() -> bool:
    return env_flag("DAILY_RESET_ENABLED", default=True)

def read_daily_reset_marker() -> str:
    try:
        return DAILY_RESET_MARKER.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

def write_daily_reset_marker(day: str):
    try:
        DAILY_RESET_MARKER.write_text(day, encoding="utf-8")
    except Exception:
        pass

def next_midnight_et(ref: datetime | None = None) -> datetime:
    ref = ref or now_et()
    next_day = ref.date() + timedelta(days=1)
    return datetime.combine(next_day, d_time(0, 0), TZ)

def remove_runtime_artifacts():
    for path in (
        PAGE_SCREENSHOT,
        LATEST_JSON.with_suffix(LATEST_JSON.suffix + ".tmp"),
        LATEST_JSON.with_suffix(LATEST_JSON.suffix + ".next"),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

def reset_runtime_for_new_day(reason: str = "daily midnight reset"):
    day = now_et().date().isoformat()
    if read_daily_reset_marker() == day:
        return

    stop_scheduler()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    try:
        atomic_write(STATES_JSON, [])
    except Exception:
        pass
    try:
        atomic_write(LATEST_JSON, [])
    except Exception:
        pass

    remove_runtime_artifacts()

    try:
        SCHEDULE_LOG.write_text("", encoding="utf-8")
    except Exception:
        pass

    write_daily_reset_marker(day)
    append_scheduler_log(f"{reason} completed; runtime files cleared for {day}")

    if env_flag("RUN_OCR_SCHEDULER", default=False):
        start_scheduler_if_enabled()

def reset_for_new_day_if_needed():
    if not daily_reset_enabled():
        return

    now = now_et()
    day = now.date().isoformat()
    if read_daily_reset_marker() == day:
        return

    if now.hour < 10:
        reset_runtime_for_new_day("startup daily reset")

def daily_reset_loop():
    while not _DAILY_RESET_STOP.is_set():
        target = next_midnight_et()
        while not _DAILY_RESET_STOP.is_set():
            remaining = (target - now_et()).total_seconds()
            if remaining <= 0:
                break
            _DAILY_RESET_STOP.wait(min(60, max(1, remaining)))

        if _DAILY_RESET_STOP.is_set():
            break

        reset_runtime_for_new_day("daily midnight reset")
        _DAILY_RESET_STOP.wait(2)

def start_daily_reset_if_enabled():
    global _DAILY_RESET_THREAD
    if not daily_reset_enabled():
        return None

    reset_for_new_day_if_needed()

    if _DAILY_RESET_THREAD is not None and _DAILY_RESET_THREAD.is_alive():
        return _DAILY_RESET_THREAD

    _DAILY_RESET_STOP.clear()
    _DAILY_RESET_THREAD = threading.Thread(
        target=daily_reset_loop,
        name="daily-reset",
        daemon=True,
    )
    _DAILY_RESET_THREAD.start()
    return _DAILY_RESET_THREAD

def stop_daily_reset():
    _DAILY_RESET_STOP.set()

def send_site_file(filename: str):
    if filename.startswith("."):
        abort(404)
    path = SITE / filename
    if not path.exists():
        abort(404)
    return send_from_directory(SITE, filename)

def send_data_file(path: Path, mimetype: str | None = None):
    if not path.exists():
        abort(404)
    kwargs = {}
    if mimetype:
        kwargs["mimetype"] = mimetype
    return send_from_directory(path.parent, path.name, **kwargs)

ensure_runtime_files()
atexit.register(stop_daily_reset)
atexit.register(stop_scheduler)

app = Flask(__name__, static_folder=None)
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.get("/")
def serve_index():
    return send_site_file("index.html")

@app.get("/obs")
def serve_obs():
    return send_site_file("index.html")

@app.get("/admin")
def serve_admin():
    return send_site_file("admin.html")

@app.get("/remote")
def serve_remote():
    return send_site_file("remote_states.htm")

@app.get("/schedule.log")
def serve_log():
    return Response(read_text_file(SCHEDULE_LOG), mimetype="text/plain; charset=utf-8")

@app.get("/latest.json")
def serve_latest():
    ensure_runtime_files()
    entries, removed = clean_duplicate_entries(load_latest_entries())
    if removed:
        try:
            atomic_write(LATEST_JSON, entries)
            append_scheduler_log("removed duplicate latest entry matching the previous draw")
        except Exception:
            pass
    return Response(json.dumps(entries, ensure_ascii=False, indent=2), mimetype="application/json")

@app.get("/states.json")
def serve_states_json():
    ensure_runtime_files()
    return send_data_file(STATES_JSON, mimetype="application/json")

@app.get("/api/states/health")
def health():
    if env_flag("RUN_OCR_SCHEDULER", default=False):
        monitor_alive = _SCHEDULER_MONITOR is not None and _SCHEDULER_MONITOR.is_alive()
        proc_running = (_SCHEDULER_PROC is not None and _SCHEDULER_PROC.poll() is None) or pid_file_running()
        if not monitor_alive or not proc_running:
            start_scheduler_if_enabled()
    items = load_states()
    scheduler_running = (_SCHEDULER_PROC is not None and _SCHEDULER_PROC.poll() is None) or pid_file_running()
    scheduler_exit_code = None if _SCHEDULER_PROC is None else _SCHEDULER_PROC.poll()
    return jsonify({
        "ok": True,
        "now_et": now_et().isoformat(timespec="seconds"),
        "file": str(STATES_JSON),
        "count": len(items),
        "latest_file": str(LATEST_JSON),
        "data_dir": str(DATA_DIR),
        "scheduler_enabled": env_flag("RUN_OCR_SCHEDULER", default=False),
        "scheduler_running": scheduler_running,
        "scheduler_exit_code": scheduler_exit_code,
        "scheduler_monitor_alive": _SCHEDULER_MONITOR is not None and _SCHEDULER_MONITOR.is_alive(),
        "daily_reset_enabled": daily_reset_enabled(),
        "daily_reset_marker": read_daily_reset_marker(),
        "daily_reset_thread_alive": _DAILY_RESET_THREAD is not None and _DAILY_RESET_THREAD.is_alive(),
    })

@app.post("/api/states/batch")
def api_batch():
    try:
        payload = request.get_json(force=True) or {}
        items = payload.get("items") or []
        if not isinstance(items, list):
            return jsonify({"ok": False, "error": "items must be a list"}), 400
        count = upsert(items)
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400

@app.get("/api/states/list")
def api_list():
    items = load_states()
    return jsonify({"ok": True, "items": items})

@app.post("/api/latest/manual")
def api_latest_manual():
    try:
        payload = request.get_json(force=True) or {}
        entry = build_manual_latest_entry(payload)
        replaced = upsert_latest_entry(entry)
        append_scheduler_log(
            f"manual latest saved for {entry['draw_id']} "
            f"(status={entry['status']}, replaced={'yes' if replaced else 'no'})"
        )
        return jsonify({"ok": True, "entry": entry, "replaced": replaced})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.post("/api/latest/delete")
def api_latest_delete():
    try:
        payload = request.get_json(force=True) or {}
        draw_id = (payload.get("draw_id") or "").strip()
        if not draw_id:
            draw_dt = parse_manual_draw_dt(payload.get("draw_date"), payload.get("draw_time"))
            draw_id = draw_dt.isoformat()

        deleted = delete_latest_entry(draw_id)
        if not deleted:
            return jsonify({"ok": False, "error": "draw not found", "draw_id": draw_id}), 404

        append_scheduler_log(f"manual latest deleted for {draw_id}")
        return jsonify({"ok": True, "draw_id": draw_id})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.post("/api/states/update")
def api_update():
    payload = request.get_json(force=True) or {}
    rec_id = (payload.get("id") or "").strip()
    if not rec_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    entry = update_one(rec_id, payload.get("pick3",""), payload.get("pick4",""))
    if not entry:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "entry": entry})

@app.post("/api/states/delete")
def api_delete():
    payload = request.get_json(force=True) or {}
    rec_id = (payload.get("id") or "").strip()
    if not rec_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    ok = delete_one(rec_id)
    if not ok:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True})

@app.post("/api/states/clear_all")
def api_clear():
    payload = request.get_json(force=True) or {}
    if payload.get("confirm") != "YES":
        return jsonify({"ok": False, "error": "confirmation required"}), 400
    atomic_write(STATES_JSON, [])
    return jsonify({"ok": True})

@app.get("/<path:filename>")
def serve_assets(filename: str):
    if filename.startswith("api/") or filename.startswith("."):
        abort(404)
    return send_site_file(filename)

start_daily_reset_if_enabled()
start_scheduler_if_enabled()

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5100"))
    if waitress_serve is not None:
        waitress_serve(app, host=host, port=port, threads=8)
    else:
        app.run(host=host, port=port, debug=False, threaded=True)
