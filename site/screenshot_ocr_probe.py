#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import shutil
import time as _t
from pathlib import Path
from datetime import datetime

import cv2
import pytesseract
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

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
CONFIG_PATH = BASE / "config.json"
PAGE_SCREENSHOT = BASE / "page.png"

PICKS = ["pick2", "pick3", "pick4", "pick5"]
EXPECTED = {"pick2": 2, "pick3": 3, "pick4": 4, "pick5": 5}


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


def screenshot_page(path, settle_ms):
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

        if settle_ms > 0:
            page.wait_for_timeout(settle_ms)

        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(path), full_page=full_page)
        browser.close()


def crop_with_pad(pil_img, roi, pad=12):
    x, y, w, h = [int(v) for v in roi]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = x + w + pad
    y1 = y + h + pad
    return pil_img.crop((x0, y0, x1, y1))


def upscale(pil_img, scale=3):
    arr = np.array(pil_img)
    return cv2.resize(arr, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def yellow_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([12, 70, 120], np.uint8)
    upper = np.array([45, 255, 255], np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    return mask


def pick_digit_boxes(mask, want_n):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []

    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        area = cv2.contourArea(c)

        if w < 14 or h < 14 or area < 120:
            continue

        per = cv2.arcLength(c, True) + 1e-6
        circ = 4 * np.pi * area / (per * per)

        if circ < 0.55:
            continue

        candidates.append((x, y, w, h, area))

    if not candidates:
        return []

    candidates.sort(key=lambda b: b[4], reverse=True)
    picked = [(x, y, w, h) for x, y, w, h, _ in candidates[:want_n]]
    picked.sort(key=lambda b: b[0])
    return picked


def dark_digit_mask(ball_bgr):
    gray = cv2.cvtColor(ball_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, otsu_inv = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    hsv = cv2.cvtColor(ball_bgr, cv2.COLOR_BGR2HSV)
    dark_hsv = cv2.inRange(hsv, np.array([0, 0, 0], np.uint8), np.array([180, 255, 140], np.uint8))

    h, w = gray.shape
    core = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(
        core,
        (w // 2, h // 2),
        (max(1, int(w * 0.36)), max(1, int(h * 0.36))),
        0,
        0,
        360,
        255,
        -1,
    )

    mask = cv2.bitwise_and(otsu_inv, dark_hsv)
    if cv2.countNonZero(mask) < 25:
        mask = otsu_inv

    mask = cv2.bitwise_and(mask, core)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    return mask


def prepare_digit_for_ocr(mask):
    pts = cv2.findNonZero(mask)
    if pts is None:
        return None

    x, y, w, h = cv2.boundingRect(pts)
    if w < 2 or h < 2:
        return None

    crop = mask[y:y + h, x:x + w]

    target = 84
    inner = 60
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

    txt = pytesseract.image_to_string(
        Image.fromarray(img_bin),
        config="--oem 3 --psm 10 -c tessedit_char_whitelist=0123456789",
    )
    m = re.search(r"\d", txt or "")
    return m.group(0) if m else ""


def save_rois_preview(full_img_bgr, rois, out_path):
    preview = full_img_bgr.copy()
    for label, roi in rois.items():
        if not roi:
            continue
        x, y, w, h = [int(v) for v in roi]
        cv2.rectangle(preview, (x, y), (x + w, y + h), (255, 0, 0), 2)
        cv2.putText(
            preview,
            label,
            (x, max(20, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 0, 0),
            2,
            cv2.LINE_AA,
        )
    cv2.imwrite(str(out_path), preview)


def run_parse(image_path, outdir, tag):
    cfg = load_cfg()
    rois = cfg.get("roi", {})
    base = Image.open(image_path).convert("RGB")
    results = {}

    for label in PICKS:
        roi = rois.get(label)
        if not roi:
            results[label] = ""
            continue

        want_n = EXPECTED[label]
        row = crop_with_pad(base, roi, pad=12)
        row_path = outdir / f"{tag}_{label}_crop.png"
        row.save(row_path)

        up_rgb = upscale(row, scale=3)
        up_bgr = cv2.cvtColor(up_rgb, cv2.COLOR_RGB2BGR)

        ymask = yellow_mask(up_bgr)
        boxes = pick_digit_boxes(ymask, want_n)

        dbg = up_bgr.copy()
        for (x, y, w, h) in boxes:
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.imwrite(str(outdir / f"{tag}_{label}_balls.png"), dbg)
        cv2.imwrite(str(outdir / f"{tag}_{label}_ymask.png"), ymask)

        digits = []
        for i, (x, y, w, h) in enumerate(boxes):
            ball = up_bgr[y:y + h, x:x + w]
            cv2.imwrite(str(outdir / f"{tag}_{label}_ball_{i + 1}.png"), ball)

            mask = dark_digit_mask(ball)
            ocr_img = prepare_digit_for_ocr(mask)

            cv2.imwrite(str(outdir / f"{tag}_{label}_digit_mask_{i + 1}.png"), mask)
            if ocr_img is not None:
                cv2.imwrite(str(outdir / f"{tag}_{label}_digit_ocr_{i + 1}.png"), ocr_img)

            d = ocr_single_digit(ocr_img)
            digits.append(d if d else "?")

        results[label] = "".join(digits)

    print(f"[{tag}] " + "  ".join(f"{k}:{results[k]}" for k in PICKS))
    return results


def main():
    cfg = load_cfg()
    settle_ms = int(cfg.get("settle_ms", 3000))

    run_id = datetime.now().strftime("run-%Y%m%d-%H%M%S")
    outdir = BASE / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    shot_a = outdir / "page_A.png"
    screenshot_page(shot_a, settle_ms=0)

    shot_b = outdir / "page_B.png"
    screenshot_page(shot_b, settle_ms=settle_ms)

    shutil.copy2(shot_b, PAGE_SCREENSHOT)

    full_bgr = cv2.imread(str(shot_b))
    if full_bgr is not None:
        save_rois_preview(full_bgr, cfg.get("roi", {}), outdir / "rois_preview.png")

    run_parse(shot_a, outdir, "A")
    run_parse(shot_b, outdir, "B")

    print(f"Saved probe output to: {outdir}")
    print(f"Updated calibration image: {PAGE_SCREENSHOT}")


if __name__ == "__main__":
    main()
