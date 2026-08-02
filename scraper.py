#!/usr/bin/env python3
"""
Berlin Kino Scraper
--------------------
Scrapes kino.veranstaltungen-in-berlin.de — a site that already aggregates
showtimes for ~50+ cinemas across Berlin & Potsdam into one paginated feed,
per day. This is the "communal" source the frontend runs on.

Scrapes a *range* of days (today + the next few), not just today, so the
app can offer a date picker.

Output:
    data/YYYY-MM-DD.json   — one file per day:
        {
          "date": "2026-08-01",
          "generated_at": "2026-08-01T09:00:00",
          "screenings": [
            {
              "cinema": "Zoo Palast",
              "address": "Hardenbergstr. 29a, 10623 Berlin",
              "film": "Spider-Man: Brand New Day",
              "format_tags": ["OV"],
              "times": ["16:15"],
              "film_url": "http://kino.veranstaltungen-in-berlin.de/..."
            },
            ...
          ]
        }
    data/index.json        — which dates are available, for the frontend's
                              date picker:
        {
          "generated_at": "2026-08-01T09:00:00",
          "dates": ["2026-08-01", "2026-08-02", ..., "2026-08-07"]
        }

UPDATE FROM CLAUDE: the container structure below (div.row.kinorow /
.eventlist_row_header / .eventlist_row) was confirmed against real
diagnostic output pulled from the live site (via `--debug`), not guessed —
so this should be solid. Pagination is handled by reading whatever offset
the page's own "next" link actually contains, rather than assuming a fixed
page size, since that wasn't directly confirmed. If a future page redesign
breaks this again, `--debug` still works the same way.

Usage:
    pip install requests beautifulsoup4 --break-system-packages
    python scraper.py                  # scrapes today + next DAYS_AHEAD days
    python scraper.py 2026-08-02        # scrapes one specific date only
    python scraper.py --debug           # diagnostic dump for today
"""

import re
import sys
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://kino.veranstaltungen-in-berlin.de/region/"
HEADERS = {
    # A real UA string; be a polite, identifiable, low-volume scraper.
    "User-Agent": "BerlinKinoPersonalProject/1.0 (non-commercial, personal use; "
                  "contact: set-your-email-here)"
}
REQUEST_DELAY = 1.0  # seconds between requests — be gentle, this is someone's small site
DAYS_AHEAD = 6  # scrape today + this many future days (7 days total)

FORMAT_WORDS = {"OV", "OmU", "3D", "IMAX", "Atmos", "D-Box", "UkrF", "OmenglU"}


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def debug_dump(target_date: date):
    """
    Diagnostic helper: fetches page 1 for a date and prints several views
    of the real structure so we can see how cinema name / address / film
    listings actually relate to each other. Run this
    (`python scraper.py --debug`) if the real scrape comes back empty —
    paste the output back so the selectors can be corrected fast.
    """
    date_str = target_date.strftime("%d.%m.%Y")
    url = f"{BASE}?kinos&date={date_str}"
    print(f"Fetching {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    print(f"HTTP {resp.status_code}, {len(resp.text)} bytes")
    soup = BeautifulSoup(resp.text, "html.parser")
    h3s = soup.find_all("h3")
    print(f"Found {len(h3s)} <h3> tags")
    film_links = soup.find_all("a", href=re.compile(r"-f\d+"))
    print(f"Found {len(film_links)} film-like links (href containing '-f<digits>')")

    if not h3s:
        print("\nNo <h3> tags found at all. First 1500 chars of body:")
        body = soup.find("body")
        print(str(body)[:1500] if body else resp.text[:1500])
        return

    first_h3 = h3s[0]

    # View 1: how many ancestor levels up is the nearest element that also
    # contains a film link? That tells us the real "cinema block" container.
    print("\n--- Ancestor chain from the first <h3> up to <body> ---")
    node = first_h3
    depth = 0
    common_ancestor = None
    while node and node.name != "body" and depth < 8:
        node = node.parent
        depth += 1
        if node is None:
            break
        has_film_link = bool(node.find("a", href=re.compile(r"-f\d+")))
        tag_desc = f"<{node.name} class={node.get('class')}>" if node.name else str(node)[:40]
        print(f"  level {depth}: {tag_desc}  (contains a film link: {has_film_link})")
        if has_film_link and common_ancestor is None:
            common_ancestor = node

    if common_ancestor is not None:
        print(f"\n--- Full HTML of the first container that holds both the "
              f"cinema heading AND a film link (first ~3000 chars) ---")
        print(str(common_ancestor)[:3000])
    else:
        print("\nNo ancestor within 8 levels contains both the heading and a "
              "film link — structure is unusual, printing first 3000 chars "
              "of <body> instead:")
        body = soup.find("body")
        print(str(body)[:3000] if body else resp.text[:3000])


def parse_film_title(raw_title: str):
    """
    The <a> tag's title="" attribute holds the full, untruncated name, e.g.:
        "Spider-Man: Brand New Day"
        "Glennkill: Ein Schafskrimi - The Sheep Detectives (OmU)"
    (link *text* is sometimes truncated with ".." for long titles, so we
    use the title attribute instead — see parse_day.)
    Split into (clean_title, [format_tags]).
    """
    text = raw_title.strip()
    tags = []

    # Leading "TAG, TAG: " prefix (seen for formats like "3D:", "Atmos, D-Box:")
    prefix_match = re.match(r"^([A-Za-z0-9\-,\s]+):\s*(.+)$", text)
    if prefix_match:
        possible_tags = [t.strip() for t in prefix_match.group(1).split(",")]
        if all(t in FORMAT_WORDS for t in possible_tags):
            tags.extend(possible_tags)
            text = prefix_match.group(2)

    # Trailing " (TAG)" suffix, e.g. "(OmU)", "(OV)"
    suffix_match = re.search(r"\(([A-Za-z]+)\)\s*$", text)
    if suffix_match and suffix_match.group(1) in FORMAT_WORDS:
        tags.append(suffix_match.group(1))
        text = text[: suffix_match.start()].strip()

    return text, tags


def parse_page(soup: BeautifulSoup, screenings: list) -> None:
    """Parses one page's worth of kinorow blocks into `screenings` (mutated in place, merging by cinema+film+tags)."""
    kinorows = soup.select("div.row.kinorow")
    for kinorow in kinorows:
        header = kinorow.select_one(".eventlist_row_header")
        if not header:
            continue
        h3 = header.find("h3")
        if not h3:
            continue
        cinema_name = h3.get_text(strip=True)
        small = header.find("small")
        address = small.get_text(strip=True) if small else ""

        for row in kinorow.select(".eventlist_row"):
            film_link = row.find("a", href=re.compile(r"-f\d+"))
            if not film_link:
                continue
            raw_title = film_link.get("title") or film_link.get_text(strip=True)
            title, tags = parse_film_title(raw_title)

            time_span = row.find("span", class_="label")
            if not time_span:
                continue
            time_match = re.match(r"(\d{1,2}:\d{2})", time_span.get_text(strip=True))
            if not time_match:
                continue
            time_val = time_match.group(1)

            existing = next(
                (s for s in screenings
                 if s["cinema"] == cinema_name and s["film"] == title
                 and s["format_tags"] == tags),
                None,
            )
            if existing:
                if time_val not in existing["times"]:
                    existing["times"].append(time_val)
            else:
                screenings.append({
                    "cinema": cinema_name,
                    "address": address,
                    "film": title,
                    "format_tags": tags,
                    "times": [time_val],
                    "film_url": film_link.get("href", ""),
                })


def parse_day(target_date: date) -> list[dict]:
    date_str = target_date.strftime("%d.%m.%Y")
    screenings = []
    seen_offsets = {0}
    offset = 0

    while True:
        url = f"{BASE}?kinos&date={date_str}"
        if offset:
            url += f"&os={offset}"
        soup = fetch(url)

        if not soup.select("div.row.kinorow"):
            break
        parse_page(soup, screenings)

        # Pagination: read whatever offset value the page's own "next"
        # link actually contains, rather than assuming a fixed page size.
        next_offset = None
        for a in soup.find_all("a", href=re.compile(r"[?&]os=\d+")):
            m = re.search(r"[?&]os=(\d+)", a.get("href", ""))
            if m:
                candidate = int(m.group(1))
                if candidate not in seen_offsets and (next_offset is None or candidate < next_offset):
                    next_offset = candidate
        if next_offset is None:
            break
        seen_offsets.add(next_offset)
        offset = next_offset
        time.sleep(REQUEST_DELAY)

    for s in screenings:
        s["times"] = sorted(set(s["times"]))

    return screenings


def scrape_and_write_day(target: date, out_dir: Path) -> int:
    screenings = parse_day(target)
    out_path = out_dir / f"{target.isoformat()}.json"
    payload = {
        "date": target.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "screenings": screenings,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(screenings)} screenings for {target.isoformat()} -> {out_path}")
    return len(screenings)


def main():
    out_dir = Path(__file__).parent / "data"
    out_dir.mkdir(exist_ok=True)

    if "--debug" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--debug"]
        target = datetime.strptime(args[0], "%Y-%m-%d").date() if args else date.today()
        debug_dump(target)
        return

    args = sys.argv[1:]
    if args:
        # Single specific date requested — scrape just that one day.
        target = datetime.strptime(args[0], "%Y-%m-%d").date()
        scrape_and_write_day(target, out_dir)
        return

    # Default: today + the next DAYS_AHEAD days.
    dates = [date.today() + timedelta(days=i) for i in range(DAYS_AHEAD + 1)]
    for i, d in enumerate(dates):
        scrape_and_write_day(d, out_dir)
        if i < len(dates) - 1:
            time.sleep(REQUEST_DELAY)

    index_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dates": [d.isoformat() for d in dates],
    }
    (out_dir / "index.json").write_text(
        json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote index.json covering {dates[0].isoformat()} .. {dates[-1].isoformat()}")


if __name__ == "__main__":
    main()
