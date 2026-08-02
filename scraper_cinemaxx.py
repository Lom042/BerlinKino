#!/usr/bin/env python3
"""
CinemaxX Berlin Scraper (NEW — needs a debug pass before going live)
----------------------------------------------------------------------
cinemaxx.de is a Vue.js app — showtimes are rendered client-side, not
present in the raw HTML (confirmed: fetching the page statically returns
no showtimes, just a loading placeholder). So, like the Yorck scraper,
this needs a real headless browser (Playwright).

CLAUDE'S HONESTY NOTE: I have not been able to render this page myself —
same limitation as Yorck. I did confirm two things directly though: (1)
the page is Vue-rendered (it says so in its own meta tags), and (2) their
OV tag is written differently than the main site's — "[Originalfassung]"
as a suffix, rather than "(OV)". The extraction logic below is built the
same defensive, structure-agnostic way as the Yorck scraper (scanning
rendered text for title-then-times patterns) rather than guessing exact
CSS classes I can't see. Run `python scraper_cinemaxx.py --debug` first
and send me the output before trusting this one.

Currently only handles TODAY — the site likely has a date picker for
future days, but I don't know how it works (URL param? click-to-load?)
without seeing it render, so this is scoped narrower on purpose rather
than guessing. Multi-day support can follow once today's version is
confirmed working.

Usage:
    pip install playwright --break-system-packages
    playwright install chromium
    python scraper_cinemaxx.py --debug     # dump rendered page text
    python scraper_cinemaxx.py             # scrape today, merge into data/<today>.json
"""

import re
import sys
import json
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://www.cinemaxx.de/kinoprogramm/berlin/jetzt-im-kino"
CINEMA_NAME = "CinemaxX Berlin"
CINEMA_ADDRESS = "Potsdamer Straße 5, 10785 Berlin"

FORMAT_WORDS = {"OV", "OmU", "3D", "IMAX", "Atmos", "D-Box", "OmenglU"}
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")


def split_tags(title_line: str):
    """
    Handles both suffix styles seen so far:
        "Film Title (OmU)"
        "Film Title [Originalfassung]"   -> normalized to tag "OV"
    """
    tags = []
    text = title_line.strip()

    m = re.search(r"\[Originalfassung\]\s*$", text, re.IGNORECASE)
    if m:
        tags.append("OV")
        text = text[: m.start()].strip()
        return text, tags

    m = re.search(r"\(([A-Za-z]+)\)\s*$", text)
    if m and m.group(1) in FORMAT_WORDS:
        tags.append(m.group(1))
        text = text[: m.start()].strip()

    return text, tags


def extract_screenings_from_text(page_text: str) -> list[dict]:
    """
    Same defensive approach as the Yorck scraper: walk the rendered text,
    treat a non-time line followed shortly by a run of HH:MM times as one
    screening. Loose on purpose since the real DOM structure is unverified.
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    screenings = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not TIME_RE.fullmatch(line) and re.search(r"[A-Za-zÀ-ÿ]{3,}", line):
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
                    "cinema": CINEMA_NAME,
                    "address": CINEMA_ADDRESS,
                    "film": title,
                    "format_tags": tags,
                    "times": sorted(set(times)),
                    "film_url": "",
                })
        i += 1
    return screenings


def debug_dump():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        print(f"--- Rendered text for {URL} ---\n")
        print(page.inner_text("body")[:4000])
        browser.close()


def main():
    if "--debug" in sys.argv:
        debug_dump()
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        browser.close()

    screenings = extract_screenings_from_text(text)
    print(f"Found {len(screenings)} screenings at {CINEMA_NAME}")
    for s in screenings:
        print(f"  {s['film']} {s['format_tags']} -> {s['times']}")

    today = date.today().isoformat()
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{today}.json"

    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = {"date": today, "generated_at": None, "screenings": []}

    payload["screenings"].extend(screenings)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merged into {path}")


if __name__ == "__main__":
    main()
