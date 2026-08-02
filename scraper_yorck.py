#!/usr/bin/env python3
"""
Yorck Kinos Scraper (EXPERIMENTAL — needs live testing/tuning)
----------------------------------------------------------------
kino.veranstaltungen-in-berlin.de (the main scraper's source) does not
cover the Yorck Kinogruppe — Berlin's best-known arthouse/OV chain, and
exactly the cinemas most likely to matter for language filtering. This
covers that gap separately, merging its results into the same per-date
files scraper.py produces (data/YYYY-MM-DD.json).

Why this one is different / riskier than scraper.py:
Yorck's site (yorck.de) is a JavaScript app (Next.js) — the showtimes are
not in the raw HTML, they're rendered client-side. That means this script
needs a real headless browser (Playwright), not just requests+BeautifulSoup.

UPDATE (confirmed against real --debug output for delphi-lux):
Each film's block on the real page has this fixed shape:
    Title
    Genre
    "|"
    "NNN min"
    "|"
    <rating, e.g. "FSK 12">
    <cast/synopsis blurb ending in "(More)">
    HH:MM               <- one line per showing
    <format tag>?        <- optional, e.g. "OmU" or "DF" — applies to that time only
    HH:MM
    <format tag>?
    ...
The blurb line ending in "(More)" is the one fully reliable anchor, so
parsing works backward from that (title is always exactly 6 lines above
it, with "|" always 2 and 4 lines above it).

Two further real bugs fixed after a second --debug pass:
  - "DF" (Deutsche Fassung / German dub) is a real per-showing tag on this
    site, distinct from OV/OmU/etc. Not recognizing it caused the scraper
    to stop reading a film's showtimes the moment it hit a "DF" line,
    silently dropping every showing after it. It's now recognized (so
    reading continues) but normalized to "no format tag", matching the
    convention the rest of this project uses elsewhere for German-dubbed
    screenings (untagged = German dub).
  - The page shows a row of date-tab labels near the top (Today, 02.08.,
    Mon, 03.08., Tue, 04.08. ...) — these are just clickable buttons, not
    sequential section headers. Treating them as headers (the previous
    version's approach) walked through all of them and tagged every real
    screening below with the LAST tab's date instead of today's. Since
    this scraper doesn't actually click through to other days yet (no
    tab-switching implemented), every screening it captures is for
    `default_date` — today — full stop. Multi-day support is a real
    future addition, not something to fake by misreading tab labels.

Usage:
    pip install playwright --break-system-packages
    playwright install chromium
    python scraper_yorck.py                 # scrapes all known Yorck cinemas,
                                              # for today only, into data/<today>.json
    python scraper_yorck.py --debug delphi-lux
"""

import re
import sys
import json
from datetime import date, datetime, timedelta
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

# Tags that genuinely describe the print's format/language, worth keeping.
FORMAT_WORDS = {"OV", "OmU", "3D", "IMAX", "Atmos", "D-Box", "OmenglU"}
# "Deutsche Fassung" (German dub) — a real per-showing tag line on this site,
# but normalized away to "no tag" (this project's convention elsewhere for
# German-dubbed screenings). Still needs recognizing so the parser doesn't
# stop early on it.
GERMAN_DUB_MARKERS = {"DF"}
TAG_LINES = FORMAT_WORDS | GERMAN_DUB_MARKERS

TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
BLURB_SUFFIX = "(More)"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build_date_lookup(today: date, days_ahead: int) -> dict:
    """
    Kept for when day-tab clicking is actually implemented (so a future
    version can map a clicked tab's label back to an ISO date). Not used
    to infer dates from page text anymore — see the module note above on
    why that was a real bug.
    """
    lookup = {}
    for i in range(days_ahead + 1):
        d = today + timedelta(days=i)
        candidates = [
            d.strftime("%d.%m."),
            d.strftime("%d.%m.%Y"),
            f"{WEEKDAYS[d.weekday()]} {d.day:02d} {MONTHS[d.month-1]}",
            f"{WEEKDAYS[d.weekday()]}, {d.day:02d} {MONTHS[d.month-1]}",
        ]
        if i == 0:
            candidates += ["Today", "TODAY"]
        if i == 1:
            candidates += ["Tomorrow", "TOMORROW"]
        for c in candidates:
            lookup[c] = d.isoformat()
    return lookup


def extract_screenings_from_text(cinema_slug: str, page_text: str, date_lookup: dict, default_date: str) -> list[dict]:
    """
    Anchored on the one fully reliable marker in Yorck's real layout: each
    film's metadata block ends with a cast/synopsis line suffixed "(More)",
    immediately followed by its showtimes. Confirmed from real rendered
    output (see --debug): the fixed shape right before that line is
        Title / Genre / "|" / "NNN min" / "|" / <rating> / <blurb>(More)
    — title sits exactly 6 lines above the blurb, with "|" always at
    offsets -2 and -4. We check those two "|" markers before trusting the
    offset, so an unexpected block shape is skipped rather than mined for
    a wrong title.

    Showtimes follow the blurb as one HH:MM line per screening, each
    optionally followed by a format-tag line (OmU, OV, DF, 3D, ...) that
    applies to that one time only — not to the whole film. "DF" (German
    dub) is recognized so reading doesn't stop early, but normalized to
    "no tag" rather than kept as its own format_tags value.

    `date_lookup` is accepted for signature compatibility but unused —
    every screening found here is tagged with `default_date`, since this
    scraper only ever reads the default ("today") tab; see module note.
    """
    lines = [l.strip() for l in page_text.split("\n") if l.strip()]
    screenings = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if (line.endswith(BLURB_SUFFIX) and i - 6 >= 0
                and lines[i - 2] == "|" and lines[i - 4] == "|"):
            title = lines[i - 6]
            if not title or title == "|" or len(title) > 80:
                i += 1
                continue

            j = i + 1
            times_with_tags = []
            while j < len(lines) and TIME_RE.fullmatch(lines[j]):
                t = lines[j]
                j += 1
                tag = None
                if j < len(lines) and lines[j] in TAG_LINES:
                    line_tag = lines[j]
                    j += 1
                    if line_tag in FORMAT_WORDS:
                        tag = line_tag
                    # else: "DF" -> tag stays None (normalized to no-tag)
                times_with_tags.append((t, tag))

            by_tag = {}
            for t, tag in times_with_tags:
                by_tag.setdefault(tag or "", []).append(t)

            for key, times in by_tag.items():
                screenings.append({
                    "date": default_date,
                    "cinema": cinema_slug,
                    "address": "",
                    "film": title,
                    "format_tags": [key] if key else [],
                    "times": sorted(set(times)),
                    "film_url": "",
                })
            i = j
            continue

        i += 1
    return screenings


def debug_dump(slug: str):
    url = f"https://www.yorck.de/en/cinemas/{slug}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        text = page.inner_text("body")
        browser.close()
    print(f"--- Rendered text for {url} ---\n")
    print(text[:6000])
    today = date.today()
    date_lookup = build_date_lookup(today, 6)
    screenings = extract_screenings_from_text(slug, text, date_lookup, today.isoformat())
    print(f"\n\n--- Parsed result ---")
    for s in screenings:
        print(f"  {s['film']} {s['format_tags']} -> {s['times']} ({s['date']})")
    print(f"\nTotal: {len(screenings)} screenings")


def scrape_cinema(page, slug: str, date_lookup: dict, default_date: str) -> list[dict]:
    url = f"https://www.yorck.de/en/cinemas/{slug}"
    page.goto(url, wait_until="networkidle", timeout=30000)
    text = page.inner_text("body")
    return extract_screenings_from_text(slug, text, date_lookup, default_date)


def main():
    if "--debug" in sys.argv:
        idx = sys.argv.index("--debug")
        slug = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else YORCK_CINEMAS[0]
        debug_dump(slug)
        return

    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)

    # Figure out which dates we're targeting from index.json if scraper.py
    # has already run today; otherwise fall back to just today. (Yorck
    # itself only ever produces today's date right now — see module note.)
    index_path = out_dir / "index.json"
    if index_path.exists():
        target_dates = json.loads(index_path.read_text(encoding="utf-8"))["dates"]
    else:
        target_dates = [date.today().isoformat()]

    today = date.today()
    days_ahead = max((datetime.strptime(d, "%Y-%m-%d").date() - today).days for d in target_dates)
    date_lookup = build_date_lookup(today, days_ahead)
    default_date = today.isoformat()

    all_screenings = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for slug in YORCK_CINEMAS:
            try:
                found = scrape_cinema(page, slug, date_lookup, default_date)
                print(f"{slug}: {len(found)} screenings")
                all_screenings.extend(found)
            except Exception as e:
                print(f"{slug}: FAILED ({e})")
        browser.close()

    # Group by date, merge into each date's existing file.
    by_date = {}
    for s in all_screenings:
        by_date.setdefault(s["date"], []).append({k: v for k, v in s.items() if k != "date"})

    for d, screenings in by_date.items():
        if d not in target_dates:
            continue  # ignore dates outside scraper.py's range
        path = out_dir / f"{d}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {"date": d, "generated_at": None, "screenings": []}
        payload["screenings"].extend(screenings)
        payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Merged {len(screenings)} Yorck screenings into {path}")


if __name__ == "__main__":
    main()
