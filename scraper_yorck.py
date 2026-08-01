#!/usr/bin/env python3
"""
Yorck Kinos Scraper (EXPERIMENTAL — needs live testing/tuning)
----------------------------------------------------------------
kino.veranstaltungen-in-berlin.de (the main scraper's source) does not
cover the Yorck Kinogruppe — Berlin's best-known arthouse/OV chain, and
exactly the cinemas most likely to matter for language filtering. This
covers that gap separately.

Why this one is different / riskier than scraper.py:
Yorck's site (yorck.de) is a JavaScript app (Next.js) — the showtimes are
not in the raw HTML, they're rendered client-side. That means this script
needs a real headless browser (Playwright), not just requests+BeautifulSoup.

CLAUDE'S HONESTY NOTE: I could not render or inspect this site's actual
JavaScript output — my sandbox can fetch static HTML but can't execute a
browser or run network calls to iterate against the real rendered page.
So unlike scraper.py (which I built against real page structure I could
read), this script is closer to an educated first draft: correct in
approach, unverified in the exact CSS selectors. Run `python
scraper_yorck.py --debug <cinema-slug>` first — it dumps the rendered
page's text so you (or I, if you paste it back to me) can fix the
selectors quickly. Budget one iteration on this one.

Usage:
    pip install playwright --break-system-packages
    playwright install chromium
    python scraper_yorck.py                 # scrapes all known Yorck cinemas, today
    python scraper_yorck.py --debug delphi-lux
"""

import re
import sys
import json
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# Known Yorck Kinogruppe venues and their yorck.de URL slugs.
# Source: Yorck's own app-store listing + site nav (~14 cinemas). If Yorck
# has opened/closed a venue since, add/remove a slug here — check
# https://www.yorck.de/en/cinemas for the current list.
YORCK_CINEMAS = [
    "babylon-kreuzberg",
    "blauer-stern",
    "capitol-dahlem",
    "cinema-paris",
    "delphi-filmpalast",
    "delphi-lux",
    "filmtheater-am-friedrichshain",
    "international",
    "kant-kino",
    "neues-off",
    "odeon",
    "passage",
    "rollberg",
    "yorck",
]

FORMAT_WORDS = {"OV", "OmU", "3D", "IMAX", "Atmos", "D-Box", "OmenglU"}
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def extract_screenings_from_text(cinema_slug: str, page_text: str) -> list[dict]:
    """
    Defensive, structure-agnostic extraction: look for a film-title-like
    line followed shortly by a run of HH:MM times. This is intentionally
    loose since the exact DOM structure is unverified — better to
    over-match slightly than crash on a selector that doesn't exist.
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    screenings = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # A "film line" is a line with letters that isn't just times/dates
        if not TIME_RE.fullmatch(line) and re.search(r"[A-Za-zÀ-ÿ]{3,}", line):
            # look ahead a few lines for a run of times
            times = []
            j = i + 1
            while j < len(lines) and j < i + 4:
                found = TIME_RE.findall(lines[j])
                if found:
                    times.extend(found)
                    j += 1
                    continue
                break
            if times:
                title, tags = split_tags(line)
                screenings.append({
                    "cinema": cinema_slug,
                    "address": "",
                    "film": title,
                    "format_tags": tags,
                    "times": sorted(set(times)),
                    "film_url": "",
                })
        i += 1
    return screenings


def split_tags(title_line: str):
    tags = []
    m = re.search(r"\(([A-Za-z]+)\)\s*$", title_line)
    if m and m.group(1) in FORMAT_WORDS:
        tags.append(m.group(1))
        title_line = title_line[: m.start()].strip()
    return title_line, tags


def debug_dump(slug: str):
    url = f"https://www.yorck.de/en/cinemas/{slug}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        print(f"--- Rendered text for {url} ---\n")
        print(page.inner_text("body")[:3000])
        browser.close()


def scrape_cinema(page, slug: str) -> list[dict]:
    url = f"https://www.yorck.de/en/cinemas/{slug}"
    page.goto(url, wait_until="networkidle", timeout=30000)
    text = page.inner_text("body")
    return extract_screenings_from_text(slug, text)


def main():
    if "--debug" in sys.argv:
        idx = sys.argv.index("--debug")
        slug = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else YORCK_CINEMAS[0]
        debug_dump(slug)
        return

    all_screenings = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for slug in YORCK_CINEMAS:
            try:
                found = scrape_cinema(page, slug)
                print(f"{slug}: {len(found)} screenings")
                all_screenings.extend(found)
            except Exception as e:
                print(f"{slug}: FAILED ({e})")
        browser.close()

    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    today = date.today().isoformat()

    # Merge into the existing latest.json from scraper.py, if present,
    # rather than overwrite it.
    latest_path = out_dir / "latest.json"
    if latest_path.exists():
        payload = json.loads(latest_path.read_text(encoding="utf-8"))
    else:
        payload = {"date": today, "generated_at": None, "screenings": []}

    payload["screenings"].extend(all_screenings)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nAdded {len(all_screenings)} Yorck screenings -> {latest_path}")


if __name__ == "__main__":
    main()
