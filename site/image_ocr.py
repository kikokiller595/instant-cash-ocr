import os, sys, json, re
from datetime import datetime, time
from zoneinfo import ZoneInfo
import cv2, pytesseract
import numpy as np
from PIL import Image

# --- paths ---
HERE = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(HERE, "config.json")
SITE_DIR = os.path.abspath(os.environ.get("DATA_DIR", HERE))
LATEST_JSON = os.path.join(SITE_DIR, "latest.json")

def default_tesseract_cmd():
    if os.name == "nt":
        return r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return "tesseract"

# Use local Tesseract on Windows and PATH on Linux unless overridden.
pytesseract.pytesseract.tesseract_cmd = os.environ.get("TESSERACT_CMD", default_tesseract_cmd())

PICKS = ["pick2", "pick3", "pick4", "pick5"]
EXPECTED = {"pick2": 2, "pick3": 3, "pick4": 4, "pick5": 5}

def is_final(res: dict) -> bool:
    for k,n in EXPECTED.items():
        if len(re.findall(r"\d", res.get(k,""))) != n:
            return False
    return True

# ---------- helpers ----------
def load_cfg():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def crop_with_pad(pil_img, roi, pad=8):
    x, y, w, h = roi
    x0 = max(0, x - pad); y0 = max(0, y - pad)
    x1 = x + w + pad;     y1 = y + h + pad
    return pil_img.crop((x0, y0, x1, y1))

def upscale(pil_img, scale=3):
    arr = np.array(pil_img)
    up = cv2.resize(arr, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return up  # numpy RGB

def yellow_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([15, 90, 120], np.uint8)
    upper = np.array([40, 255, 255], np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((5,5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    return mask

def white_mask(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([0, 0, 200], np.uint8)
    upper = np.array([180, 80, 255], np.uint8)
    return cv2.inRange(hsv, lower, upper)

def order_row_major(boxes):
    if not boxes: return []
    boxes_y = sorted(boxes, key=lambda b: b[1])
    avg_h = sum(b[3] for b in boxes_y) / len(boxes_y)
    row_thresh = max(6, avg_h * 0.6)
    rows, current = [], [boxes_y[0]]
    for b in boxes_y[1:]:
        if abs(b[1] - current[-1][1]) <= row_thresh:
            current.append(b)
        else:
            rows.append(sorted(current, key=lambda r: r[0]))
            current = [b]
    rows.append(sorted(current, key=lambda r: r[0]))
    return [b for row in rows for b in row]

def pick_digit_boxes(mask, want_n):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x,y,w,h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if w < 14 or h < 14 or area < 120:  # remove noise
            continue
        per = cv2.arcLength(c, True) + 1e-6
        circ = 4*np.pi*area/(per*per)
        if circ < 0.55:  # prefer round circles
            continue
        boxes.append((x,y,w,h))
    if not boxes:
        return []
    ordered = order_row_major(boxes)
    return ordered[:want_n]

def ocr_single_digit(img_bin):
    pil = Image.fromarray(img_bin)
    txt = pytesseract.image_to_string(
        pil, config=r'-c tessedit_char_whitelist=0123456789 --psm 10'
    )
    m = re.search(r"\d", txt or "")
    return m.group(0) if m else ""

def detect_from_roi(base_pil, roi, want_n):
    base_np = np.array(base_pil)  # RGB
    H, W = base_np.shape[:2]

    def run_once(roi_local):
        row = crop_with_pad(base_pil, roi_local, pad=8)
        up_rgb = upscale(row, scale=3)
        up_bgr = cv2.cvtColor(up_rgb, cv2.COLOR_RGB2BGR)
        ymask = yellow_mask(up_bgr)
        boxes = pick_digit_boxes(ymask, want_n)
        digits = []
        for (x,y,w,h) in boxes:
            xi = x + int(w*0.18); yi = y + int(h*0.18)
            wi = max(1, int(w*0.64)); hi = max(1, int(h*0.64))
            ball = up_bgr[yi:yi+hi, xi:xi+wi]
            wmask = white_mask(ball)
            gray  = cv2.cvtColor(ball, cv2.COLOR_BGR2GRAY)
            glyph = cv2.bitwise_and(gray, gray, mask=wmask)
            _, bin_img = cv2.threshold(glyph, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
            bin_img = cv2.bitwise_not(bin_img)
            d = ocr_single_digit(bin_img)
            digits.append(d if d else "?")
        return "".join(digits), len(boxes)

    # try original ROI
    out, n = run_once(roi)
    if n >= want_n:
        return out
    # fallback: expand height for wrapped rows
    x, y, w, h = roi
    new_h = min(int(h * 1.9), H - y - 1)
    return run_once([x, y, w, new_h])[0]

def fix_len(s, n):
    if s is None: s = ""
    s = re.sub(r"\D", "", str(s))
    return (s + "0"*n)[:n]

def compute_draw_time_et():
    """Clamp to 10:00–22:00 ET and floor to :00 or :30."""
    tz = ZoneInfo("America/New_York")
    now = datetime.now(tz)
    if now.hour < 10:
        return datetime.combine(now.date(), time(10,0), tz)
    minute_slot = 0 if now.minute < 30 else 30
    dt = now.replace(minute=minute_slot, second=0, microsecond=0)
    if dt.hour < 10:
        dt = dt.replace(hour=10, minute=0)
    if dt.hour > 22 or (dt.hour == 22 and dt.minute > 0):
        dt = dt.replace(hour=22, minute=0)
    return dt

def load_previous_results():
    if not os.path.exists(LATEST_JSON):
        return []
    try:
        with open(LATEST_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return [data]
        return data if isinstance(data, list) else []
    except Exception:
        return []

def write_latest_json(p2, p3, p4, p5):
    os.makedirs(SITE_DIR, exist_ok=True)
    draw_dt = compute_draw_time_et()
    payload = {
        "draw_id": draw_dt.isoformat(),
        "captured_at": datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds"),
        "status": "final",
        "pick2": fix_len(p2, 2),
        "pick3": fix_len(p3, 3),
        "pick4": fix_len(p4, 4),
        "pick5": fix_len(p5, 5)
    }
    prev = [e for e in load_previous_results() if e.get("draw_id") != payload["draw_id"]]
    prev.append(payload)
    prev.sort(key=lambda x: x.get("draw_id", ""))
    tmp = LATEST_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(prev, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LATEST_JSON)

# ---------- main ----------
def main():
    img_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sample.png")
    if not os.path.exists(img_path):
        print(f"Image not found: {img_path}")
        sys.exit(1)

    base = Image.open(img_path).convert("RGB")
    cfg = load_cfg(); rois = cfg.get("roi", {})
    results = {}

    for label in PICKS:
        roi = rois.get(label)
        if not roi:
            results[label] = ""
            continue
        want = EXPECTED[label]
        results[label] = detect_from_roi(base, roi, want)

    # console print
    for k in PICKS:
        print(f"{k}: {results.get(k,'')}")

    # write latest.json for your page
    write_latest_json(results.get("pick2",""),
                      results.get("pick3",""),
                      results.get("pick4",""),
                      results.get("pick5",""))

if __name__ == "__main__":
    main()
