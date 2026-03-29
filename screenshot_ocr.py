#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time as _t
import argparse
from pathlib import Path
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import cv2
import pytesseract
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

TZ = ZoneInfo("America/New_York")
PICKS = ["pick2", "pick3", "pick4", "pick5"]
EXPECTED = {"pick2": 2, "pick3": 3, "pick4": 4, "pick5": 5}

def env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def default_tesseract_cmd():
    if os.name == "nt":
        return r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return "tesseract"


pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD", default_tesseract_cmd())

BASE = Path(__file__).resolve().parent
CONFIG_PATH = (BASE / "config.json").resolve()

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
LATEST_JSON = (DATA_DIR / "latest.json").resolve()
PAGE_SCREENSHOT = (DATA_DIR / "page.png").resolve()

FORCED_WAIT_MS = 10000  # minimum wait before screenshot


def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _ensure_stable_viewport(page, width, height, target_dpr=1.0, max_wait_ms=2000):
    deadline = _t.time() + (max_wait_ms / 1000.0)
    reapplied = False
    while _t.time() < deadline:
        iw, ih, dpr = page.evaluate("[window.innerWidth, window.innerHeight, window.devicePixelRatio]")
        if iw == width and ih == height and abs(dpr - target_dpr) < 0.01:
            return
        if not reapplied:
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("document.body.style.zoom='100%'")
            reapplied = True
        page.wait_for_timeout(100)


def screenshot_page(path, wait_ms=None):
    cfg = load_cfg()
    page_url = str(cfg.get("page_url", "https://instantcash.bet/")).strip()
    vp = cfg.get("viewport", {})
    width = int(vp.get("width", 1366))
    height = int(vp.get("height", 768))
    full_page = bool(cfg.get("full_page", True))
    cfg_headless = cfg.get("headless")
    default_headless = bool(cfg_headless) if isinstance(cfg_headless, bool) else env_flag("RAILWAY_ENVIRONMENT", False)
    headless = env_flag("PLAYWRIGHT_HEADLESS", default_headless)
    browser_channel = (
        os.getenv("PLAYWRIGHT_BROWSER_CHANNEL")
        or cfg.get("browser_channel")
        or ("msedge" if os.name == "nt" and not headless else "")
    )

    cfg_wait_ms = int(cfg.get("settle_ms", FORCED_WAIT_MS))
    effective_wait_ms = max(FORCED_WAIT_MS, cfg_wait_ms)
    if wait_ms is not None:
        effective_wait_ms = max(FORCED_WAIT_MS, int(wait_ms))

    with sync_playwright() as p:
        launch_args = [
            f"--window-size={width},{height}",
            "--high-dpi-support=1",
            "--force-device-scale-factor=1",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ]
        launch_kwargs = {
            "headless": headless,
            "args": launch_args,
        }
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        try:
            browser = p.chromium.launch(**launch_kwargs)
        except Exception:
            launch_kwargs.pop("channel", None)
            browser = p.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            screen={"width": width, "height": height},
            timezone_id="America/New_York",
            locale="en-US",
            ignore_https_errors=True,
        )

        page = context.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        page.add_style_tag(content="""
            html, body { margin:0 !important; padding:0 !important; overflow:auto !important; }
            * { animation:none !important; transition:none !important; }
        """)

        page.set_viewport_size({"width": width, "height": height})
        page.evaluate("document.body.style.zoom='100%'")
        _ensure_stable_viewport(page, width, height, target_dpr=1.0, max_wait_ms=2000)

        page.wait_for_timeout(effective_wait_ms)

        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(path), full_page=full_page)

        context.close()
        browser.close()


def crop_with_pad(pil_img, roi, pad=10):
    x, y, w, h = [int(v) for v in roi]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(pil_img.size[0], x + w + pad)
    y1 = min(pil_img.size[1], y + h + pad)
    return pil_img.crop((x0, y0, x1, y1))


def upscale(pil_img, scale=3):
    arr = np.array(pil_img)
    return cv2.resize(arr, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def yellow_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    lower = np.array([12, 65, 120], np.uint8)
    upper = np.array([48, 255, 255], np.uint8)

    mask = cv2.inRange(hsv, lower, upper)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return mask


def dedupe_circles(circles, min_dx):
    out = []
    for c in sorted(circles, key=lambda t: t[0]):
        if not out:
            out.append(c)
            continue
        if abs(c[0] - out[-1][0]) < min_dx:
            if c[2] > out[-1][2]:
                out[-1] = c
        else:
            out.append(c)
    return out


def detect_ball_circles(mask, want_n):
    h, w = mask.shape[:2]
    min_r = max(16, int(h * 0.18))
    max_r = max(min_r + 6, int(h * 0.52))
    circles = []

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in cnts:
        area = cv2.contourArea(c)
        if area < 80:
            continue

        (cx, cy), r = cv2.minEnclosingCircle(c)
        cx = int(round(cx))
        cy = int(round(cy))
        r = int(round(r))

        if r < min_r or r > max_r:
            continue

        circ_area = np.pi * (r ** 2)
        fill = area / max(circ_area, 1.0)
        if fill < 0.35:
            continue

        circles.append((cx, cy, r, area))

    if len(circles) < want_n:
        gray = cv2.GaussianBlur(mask, (9, 9), 1.5)
        hc = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(24, int(h * 0.45)),
            param1=100,
            param2=16,
            minRadius=min_r,
            maxRadius=max_r,
        )
        if hc is not None:
            for c in np.round(hc[0]).astype(int):
                circles.append((int(c[0]), int(c[1]), int(c[2]), int(np.pi * c[2] * c[2])))

    circles = dedupe_circles(circles, min_dx=max(20, int(h * 0.35)))

    if len(circles) > want_n:
        circles = sorted(circles, key=lambda t: t[3], reverse=True)[:want_n]

    circles = sorted(circles, key=lambda t: t[0])
    return [(cx, cy, r) for cx, cy, r, _ in circles]


def keep_center_components(mask):
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    h, w = mask.shape[:2]
    keep = np.zeros_like(mask)

    for i in range(1, num):
        x, y, ww, hh, area = stats[i]
        cx, cy = centroids[i]

        if area < max(10, int(h * w * 0.008)):
            continue

        if abs(cx - (w / 2)) > (w * 0.28):
            continue

        if abs(cy - (h / 2)) > (h * 0.28):
            continue

        keep[labels == i] = 255

    return keep


def dark_digit_mask(ball_bgr):
    gray = cv2.cvtColor(ball_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    _, otsu_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    hsv = cv2.cvtColor(ball_bgr, cv2.COLOR_BGR2HSV)
    dark_hsv = cv2.inRange(
        hsv,
        np.array([0, 0, 0], np.uint8),
        np.array([180, 255, 165], np.uint8),
    )

    mask = cv2.bitwise_and(otsu_inv, dark_hsv)
    if cv2.countNonZero(mask) < 20:
        mask = otsu_inv

    h, w = mask.shape[:2]
    core = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(
        core,
        (w // 2, h // 2),
        (max(1, int(w * 0.44)), max(1, int(h * 0.44))),
        0,
        0,
        360,
        255,
        -1,
    )
    mask = cv2.bitwise_and(mask, core)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    focused = keep_center_components(mask)
    if cv2.countNonZero(focused) > 0:
        mask = focused

    return mask


def prepare_digit_for_ocr(mask):
    pts = cv2.findNonZero(mask)
    if pts is None:
        return None

    x, y, w, h = cv2.boundingRect(pts)
    if w < 2 or h < 2:
        return None

    crop = mask[y:y + h, x:x + w]

    target = 96
    inner = 68
    scale = min(inner / max(w, 1), inner / max(h, 1))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))

    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    digit_black = 255 - resized

    canvas = np.full((target, target), 255, dtype=np.uint8)
    x0 = (target - new_w) // 2
    y0 = (target - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = digit_black
    return canvas


def ocr_single_digit(img_bin):
    if img_bin is None:
        return ""

    variants = [img_bin]

    k = np.ones((2, 2), np.uint8)
    variants.append(cv2.dilate(img_bin, k, iterations=1))
    variants.append(cv2.erode(img_bin, k, iterations=1))

    for candidate in variants:
        for psm in (10, 13):
            txt = pytesseract.image_to_string(
                Image.fromarray(candidate),
                config=f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789",
            )
            m = re.search(r"\d", txt or "")
            if m:
                return m.group(0)

    return ""


def read_row_rgb(row_rgb, want_n, label, debug=False, debug_dir=None):
    up_rgb = upscale(row_rgb, scale=3)
    up_bgr = cv2.cvtColor(up_rgb, cv2.COLOR_RGB2BGR)

    ymask = yellow_mask(up_bgr)
    circles = detect_ball_circles(ymask, want_n)
    digits = []

    if debug and debug_dir is not None:
        dbg = up_bgr.copy()
        for (cx, cy, r) in circles:
            cv2.circle(dbg, (cx, cy), r, (0, 255, 0), 2)
        cv2.imwrite(str(debug_dir / f"{label}_balls.png"), dbg)
        cv2.imwrite(str(debug_dir / f"{label}_ymask.png"), ymask)

    for i, (cx, cy, r) in enumerate(circles):
        pad = int(max(4, round(r * 1.10)))
        x0 = max(0, cx - pad)
        y0 = max(0, cy - pad)
        x1 = min(up_bgr.shape[1], cx + pad)
        y1 = min(up_bgr.shape[0], cy + pad)

        ball = up_bgr[y0:y1, x0:x1]
        mask = dark_digit_mask(ball)
        ocr_img = prepare_digit_for_ocr(mask)

        if debug and debug_dir is not None:
            cv2.imwrite(str(debug_dir / f"{label}_digit_mask_{i + 1}.png"), mask)
            if ocr_img is not None:
                cv2.imwrite(str(debug_dir / f"{label}_digit_ocr_{i + 1}.png"), ocr_img)

        d = ocr_single_digit(ocr_img)
        digits.append(d if d else "?")

    return "".join(digits)


def detect_from_roi(base_pil, roi, want_n, label, debug=False, debug_dir=None):
    row = crop_with_pad(base_pil, roi, pad=10)
    return read_row_rgb(np.array(row), want_n, label, debug=debug, debug_dir=debug_dir)


def load_previous_result():
    try:
        if not LATEST_JSON.exists():
            return []

        data = json.loads(LATEST_JSON.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        best = {}
        for e in data:
            k = e.get("draw_id", "")
            if not k:
                continue

            cur = best.get(k)
            if cur is None:
                best[k] = e
                continue

            cur_final = cur.get("status") == "final"
            e_final = e.get("status") == "final"

            if (not cur_final and e_final) or (e.get("captured_at", "") > cur.get("captured_at", "")):
                best[k] = e

        return sorted(best.values(), key=lambda x: x.get("draw_id", ""))
    except Exception:
        return []


def same_pick_set(entry, results) -> bool:
    return all((entry.get(k, "") or "") == (results.get(k, "") or "") for k in PICKS)


def previous_final_entry(entries, draw_id: str):
    prior = [
        e for e in entries
        if isinstance(e, dict)
        and (e.get("draw_id", "") or "") < draw_id
        and e.get("status") == "final"
    ]
    if not prior:
        return None
    prior.sort(key=lambda x: x.get("draw_id", ""))
    return prior[-1]


def compute_draw_time_et():
    now = datetime.now(TZ)

    if now.hour < 10:
        return datetime.combine(now.date(), dtime(10, 0), TZ)

    minute_slot = 0 if now.minute < 30 else 30
    dt = now.replace(minute=minute_slot, second=0, microsecond=0)

    if dt.hour < 10:
        dt = dt.replace(hour=10, minute=0)

    if dt.hour > 22 or (dt.hour == 22 and dt.minute > 0):
        dt = dt.replace(hour=22, minute=0)

    return dt


def is_final(res):
    for k, n in EXPECTED.items():
        if len(re.sub(r"\D", "", res.get(k, "") or "")) != n:
            return False
    return True


def write_latest_json(results, status):
    BASE.mkdir(parents=True, exist_ok=True)
    draw_time = compute_draw_time_et()
    draw_id = draw_time.isoformat()

    prev = load_previous_result()
    prev_final = previous_final_entry(prev, draw_id)

    if prev_final and same_pick_set(prev_final, results):
        return False, draw_id, f"same as previous draw {prev_final.get('draw_id', '')}"

    for e in prev:
        if e.get("draw_id") == draw_id:
            same_numbers = same_pick_set(e, results)
            if same_numbers and e.get("status") == status:
                return False, draw_id, "same result for this draw time"
            break

    payload = {
        "draw_id": draw_id,
        "captured_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "status": status,
        "pick2": results.get("pick2", ""),
        "pick3": results.get("pick3", ""),
        "pick4": results.get("pick4", ""),
        "pick5": results.get("pick5", ""),
    }

    new_data = [e for e in prev if e.get("draw_id") != draw_id] + [payload]
    new_data.sort(key=lambda x: x.get("draw_id", ""))

    tmp = str(LATEST_JSON) + ".tmp"
    try:
        os.remove(tmp)
    except Exception:
        pass

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)

    last_err = None
    for _ in range(12):
        try:
            os.replace(tmp, LATEST_JSON)
            return True, draw_id, "updated"
        except PermissionError as e:
            last_err = e
            _t.sleep(0.5)

    try:
        os.replace(tmp, str(LATEST_JSON) + ".next")
    except Exception:
        pass

    if last_err is not None:
        raise last_err

    return False, draw_id, "not updated"


def detect_all(base_img, cfg):
    rois = cfg.get("roi", {})
    debug = bool(cfg.get("debug", False))
    debug_dir = BASE / "debug"

    if debug:
        debug_dir.mkdir(parents=True, exist_ok=True)

    out = {}
    for label in PICKS:
        roi = rois.get(label)
        if not roi:
            out[label] = ""
            continue

        out[label] = detect_from_roi(
            base_img,
            roi,
            EXPECTED[label],
            label,
            debug=debug,
            debug_dir=debug_dir if debug else None,
        )

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--attempt", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--shot-only", action="store_true", help="Take page.png and exit")
    parser.add_argument("--wait-ms", type=int, default=None, help="Override wait before screenshot")
    args = parser.parse_args()

    screenshot_page(PAGE_SCREENSHOT, wait_ms=args.wait_ms)

    if args.shot_only:
        print(f"saved screenshot: {PAGE_SCREENSHOT}")
        return

    base = Image.open(PAGE_SCREENSHOT).convert("RGB")
    cfg = load_cfg()
    results = detect_all(base, cfg)

    for k in PICKS:
        print(f"{k}: {results.get(k, '')}")

    if not is_final(results):
        print("OCR incomplete -> not saving latest.json this run")
        return

    was_updated, _, note = write_latest_json(results, "final")
    draw_time = compute_draw_time_et()

    print(f"Draw: {draw_time.strftime('%Y-%m-%d %H:%M')} Numbers: {' '.join(results[k] for k in PICKS)}")
    print("latest.json updated" if was_updated else f"{note} - no update")


if __name__ == "__main__":
    main()
