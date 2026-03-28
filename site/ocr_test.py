import json, os, re
import cv2, pytesseract
from PIL import Image
import numpy as np

def default_tesseract_cmd():
    if os.name == "nt":
        return r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    return "tesseract"

# Use Windows path locally and PATH on Linux unless overridden.
pytesseract.pytesseract.tesseract_cmd = os.environ.get("TESSERACT_CMD", default_tesseract_cmd())

CONFIG_PATH = "config.json"
SAMPLE_IMAGE = "sample.png"

PICKS = ["pick2", "pick3", "pick4", "pick5"]
EXPECTED = {"pick2": 2, "pick3": 3, "pick4": 4, "pick5": 5}

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
    up = cv2.resize(arr, (0,0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return up  # numpy RGB

# --- color masks ---
def yellow_mask(bgr):
    # HSV ranges for yellow circles
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([15, 90, 120], np.uint8)   # H,S,V
    upper = np.array([40, 255, 255], np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    kernel = np.ones((5,5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel, iterations=1)
    return mask

def white_mask(bgr):
    # keep white text (low saturation, high value)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower = np.array([0, 0, 200], np.uint8)
    upper = np.array([180, 80, 255], np.uint8)
    return cv2.inRange(hsv, lower, upper)

# --- helpers ---
def pick_digit_boxes(mask, want_n):
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x,y,w,h = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if w < 14 or h < 14 or area < 120:  # filter tiny noise
            continue
        per = cv2.arcLength(c, True) + 1e-6
        circ = 4*np.pi*area/(per*per)       # ~1.0 for circle
        if circ < 0.55:                      # weed out non-circles
            continue
        boxes.append((x,y,w,h))
    if not boxes:
        return []
    # Choose the left-most N (within a single row)
    boxes.sort(key=lambda b: b[0])
    return boxes[:want_n]

def ocr_single_digit(img_bin):
    pil = Image.fromarray(img_bin)
    txt = pytesseract.image_to_string(
        pil,
        config=r'-c tessedit_char_whitelist=0123456789 --psm 10'
    )
    m = re.search(r"\d", txt or "")
    return m.group(0) if m else ""

def main():
    if not os.path.exists(SAMPLE_IMAGE):
        print("sample.png not found.")
        return

    cfg = load_cfg()
    rois = cfg.get("roi", {})
    base = Image.open(SAMPLE_IMAGE).convert("RGB")

    for label in PICKS:
        roi = rois.get(label)
        if not roi:
            print(f"{label}: ROI missing")
            continue
        want_n = EXPECTED[label]

        # 1) crop row and upscale
        row = crop_with_pad(base, roi, pad=8)
        up_rgb = upscale(row, scale=3)
        up_bgr = cv2.cvtColor(up_rgb, cv2.COLOR_RGB2BGR)

        # 2) find yellow balls
        ymask = yellow_mask(up_bgr)
        boxes = pick_digit_boxes(ymask, want_n)

        # debug: show boxes
        dbg = up_bgr.copy()
        for (x,y,w,h) in boxes:
            cv2.rectangle(dbg, (x,y), (x+w,y+h), (0,255,0), 2)
        cv2.imwrite(f"crop_{label}_balls.png", dbg)
        cv2.imwrite(f"crop_{label}_ymask.png", ymask)

        digits = []
        for i,(x,y,w,h) in enumerate(boxes):
            # inside the circle (shrink to avoid border)
            xi = x + int(w*0.18)
            yi = y + int(h*0.18)
            wi = max(1, int(w*0.64))
            hi = max(1, int(h*0.64))
            ball = up_bgr[yi:yi+hi, xi:xi+wi]

            # 3) mask white and binarize
            wmask = white_mask(ball)
            gray  = cv2.cvtColor(ball, cv2.COLOR_BGR2GRAY)
            glyph = cv2.bitwise_and(gray, gray, mask=wmask)
            _, bin_img = cv2.threshold(glyph, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
            bin_img = cv2.bitwise_not(bin_img)  # black digit on white

            cv2.imwrite(f"crop_{label}_digit{i+1}.png", bin_img)

            d = ocr_single_digit(bin_img)
            digits.append(d if d else "?")

        print(f"{label}: {''.join(digits)}")

if __name__ == "__main__":
    main()
