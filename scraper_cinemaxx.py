#!/usr/bin/env python3
"""
CinemaxX Berlin Scraper
--------------------------------------------------------------
cinemaxx.de is a Vue.js app — showtimes are rendered client-side, so this
needs a real headless browser (Playwright), not a simple fetch.

Parsing logic was rewritten against REAL rendered output (via --debug),
not guessed. Confirmed real quirks handled here:
  - Each showing's time is rendered as start+end concatenated with NO
    separator, e.g. "10:0012:51" (10:00 start, 12:51 = start + runtime).
    Only the first time is the actual start time.
  - There's a "ZEIGE ALLE FILMZEITEN" (show all showtimes) button per
    film — without clicking it, only a partial list of showings renders.
    This scraper clicks every one of those before extracting text.
  - Per-showing language isn't in the title; it's a separate line
    ("Englisch" / "Japanisch mit deutschen Untertiteln") that only
    appears for non-German showings. No line = German dub (default).
  - Some showings use a combined line like "dt. UT - Englisch - OmU"
    (English audio, German subtitles) rather than the plain "Englisch"
    line — confirmed from real output (see Die Odyssee's 22:15 showing).
    That one also means OmU, not "untagged".

Still scoped to TODAY only — the site's date tabs (Heute/Morgen/Di/...)
mechanism (URL param vs. click-to-reload) isn't confirmed yet. Multi-day
can follow once this is verified working.

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

TIME_PAIR_RE = re.compile(r"^(\d{1,2}:\d{2})(\d{1,2}:\d{2})$")
PRICE_RE = re.compile(r"^\d+,\d{2}\s?€$")
DAY_MARKER = "HEUTE"
SHOW_ALL_TEXT = "ZEIGE ALLE FILMZEITEN"


def classify_language_line(line: str) -> str | None:
    """Maps a language-marker line to a format tag. Handles both the plain
    'Englisch' / 'Japanisch mit deutschen Untertiteln' lines and the
    combined 'dt. UT - Englisch - OmU' style line (English audio with
    German subtitles) — confirmed from real output, both mean OmU."""
    if "OmU" in line:
        return "OmU"
    if line == "Englisch":
        return "OV"
    if line == "Japanisch mit deutschen Untertiteln":
        return "OmU"
    return None


def expand_and_get_text(page) -> str:
    """Clicks every 'show all showtimes' button so nothing is hidden, then returns the full rendered body text."""
    try:
        buttons = page.get_by_text(SHOW_ALL_TEXT, exact=True)
        count = buttons.count()
        for idx in range(count):
            try:
                buttons.nth(idx).click(timeout=3000)
                page.wait_for_timeout(300)
            except Exception:
                pass
    except Exception:
        pass
    return page.inner_text("body")


def parse_cinemaxx_text(text: str) -> list[dict]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    screenings = []
    i = 0
    while i < len(lines):
        if lines[i] == "Besetzung" and i >= 2:
            title = lines[i - 2]
            # Sanity check: title lines are ALL CAPS in the real output.
            if title != title.upper() or len(title) < 2:
                i += 1
                continue

            # Walk forward to find this film's "HEUTE" showings marker.
            j = i + 1
            while j < len(lines) and lines[j] != DAY_MARKER:
                # Don't wander into the next film's section looking for it.
                if lines[j] == "Besetzung":
                    break
                j += 1
            if j >= len(lines) or lines[j] != DAY_MARKER:
                i += 1
                continue

            k = j + 1
            while k < len(lines):
                m = TIME_PAIR_RE.match(lines[k].replace(" ", ""))
                if not m:
                    break  # end of this film's showings (hit "ZEIGE ALLE..." or next section)
                start_time = m.group(1)
                k += 1
                tag = None
                # Scan forward through this showing's attribute lines
                # (Kino NN, 2D/3D, Laser, Ab, price) until the price line,
                # then check the one line after it for a language marker.
                while k < len(lines) and not PRICE_RE.match(lines[k]):
                    if TIME_PAIR_RE.match(lines[k].replace(" ", "")):
                        break  # malformed block, bail out
                    k += 1
                if k < len(lines) and PRICE_RE.match(lines[k]):
                    k += 1
                    if k < len(lines):
                        classified = classify_language_line(lines[k])
                        if classified:
                            tag = classified
                            k += 1

                tags = [tag] if tag else []
                existing = next(
                    (s for s in screenings
                     if s["cinema"] == CINEMA_NAME and s["film"] == title
                     and s["format_tags"] == tags),
                    None,
                )
                if existing:
                    if start_time not in existing["times"]:
                        existing["times"].append(start_time)
                else:
                    screenings.append({
                        "cinema": CINEMA_NAME,
                        "address": CINEMA_ADDRESS,
                        "film": title.title(),
                        "format_tags": tags,
                        "times": [start_time],
                        "film_url": "",
                    })
            i = k
            continue
        i += 1

    for s in screenings:
        s["times"] = sorted(set(s["times"]))
    return screenings


def debug_dump():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        text = expand_and_get_text(page)
        browser.close()
    print(f"--- Rendered text for {URL} (after expanding 'show all') ---\n")
    print(text[:6000])
    print(f"\n\n--- Parsed result ---")
    screenings = parse_cinemaxx_text(text)
    for s in screenings:
        print(f"  {s['film']} {s['format_tags']} -> {s['times']}")
    print(f"\nTotal: {len(screenings)} screenings")


def main():
    if "--debug" in sys.argv:
        debug_dump()
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=30000)
        text = expand_and_get_text(page)
        browser.close()

    screenings = parse_cinemaxx_text(text)
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
