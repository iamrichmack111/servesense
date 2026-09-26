from pathlib import Path
import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = os.getenv("SERVESENSE_URL", "http://127.0.0.1:8003")
USERNAME = os.getenv("ADMIN_USERNAME", os.getenv("OWNER_USERNAME", "owner"))
PASSWORD = os.getenv("ADMIN_PASSWORD", os.getenv("OWNER_PASSWORD", "ServeSenseDemo123!"))
OUT = Path("docs/screenshots/playwright")
OUT.mkdir(parents=True, exist_ok=True)


def safe_name(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "page"


def shot(page, name: str):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


def try_fill(page, names, value):
    for selector in names:
        loc = page.locator(selector).first
        try:
            if loc.count() and loc.is_visible():
                loc.fill(value)
                return True
        except Exception:
            pass
    return False


def login(page):
    page.goto(BASE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    user_ok = try_fill(page, [
        'input[name="username"]', 'input[name="email"]',
        'input[type="text"]', 'input[type="email"]'
    ], USERNAME)
    pass_ok = try_fill(page, ['input[name="password"]', 'input[type="password"]'], PASSWORD)
    if user_ok and pass_ok:
        for selector in ['button[type="submit"]', 'input[type="submit"]']:
            btn = page.locator(selector).first
            if btn.count() and btn.is_visible():
                btn.click()
                break
        else:
            page.get_by_role("button", name=re.compile("sign in|log in|login", re.I)).first.click()
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass


def click_if_present(page, pattern):
    loc = page.get_by_role("button", name=pattern).first
    try:
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    return False


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    login(page)
    click_if_present(page, re.compile(r"load demo data", re.I))
    shot(page, "01-dashboard")

    wanted = [
        "Staff", "Sales", "Availability", "Predict", "Schedules",
        "Reservations", "Payroll", "Reports", "Settings"
    ]

    seen = set()
    index = 2
    for label in wanted:
        link = page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
        try:
            if not link.count() or not link.is_visible():
                link = page.get_by_role("link", name=re.compile(re.escape(label), re.I)).first
            if not link.count() or not link.is_visible():
                continue
            href = link.get_attribute("href")
            if href and href in seen:
                continue
            seen.add(href or label)
            link.click()
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(350)
            shot(page, f"{index:02d}-{safe_name(label)}")
            index += 1
        except Exception as exc:
            print(f"WARN: {label}: {exc}")

    browser.close()

print(f"Wrote screenshots to {OUT}")
