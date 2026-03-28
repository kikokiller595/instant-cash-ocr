import json
import os
from playwright.sync_api import sync_playwright

CONFIG_PATH = "config.json"
PAGE_URL = "https://instantcash.bet/"

def main():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    vp = cfg.get("viewport", {})
    width  = int(vp.get("width", 1366))
    height = int(vp.get("height", 768))
    dpf    = float(vp.get("deviceScaleFactor", 1))
    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "0").strip().lower() in {"1", "true", "yes", "on"}

    with sync_playwright() as p:
        browser  = p.chromium.launch(headless=headless)
        context  = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=dpf
        )
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="networkidle")  # wait for all requests to settle
        page.wait_for_timeout(2000)                    # extra 2s for animations/numbers
        page.screenshot(path="page.png", full_page=True)  # KEEP page.png
        browser.close()

if __name__ == "__main__":
    main()
