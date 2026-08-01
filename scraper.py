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

NOTE FROM CLAUDE: I built this against the page structure I could see through
a text-mode fetch (I don't have live network access in the sandbox I'm
running in, so I couldn't execute and debug this against the real page).
The site is server-rendered HTML (no JS needed), and the structure was
consistent across every page I inspected, so this should work close to
as-is — but if the CSS selectors below don't match on your first run,
open the page's HTML source, find the real class names for the cinema
block / film link / showtime list, and swap them in. Tell me what you see
and I can fix it in seconds.

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
PAGE_SIZE = 100  # the site's own pagination offset step (?os=0, 100, 200, ...)
DAYS_AHEAD = 6  # scrape today + this many future days (7 days total)

FORMAT_WORDS = {"OV", "OmU", "3D", "IMAX", "Atmos", "D-Box", "UkrF", "OmenglU"}


def fetch(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def debug_dump(target_date: date):
    """
    Diagnostic helper: fetches page 1 for a date and prints the raw HTML of
    the first cinema block it can find, plus counts of key elements. Run
    this first (`python scraper.py --debug`) if the real scrape comes back
    empty — paste the output back so the selectors can be corrected fast.
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
    if h3s:
        print("\n--- First <h3> block, raw HTML (first ~1500 chars) ---")
        start = h3s[0]
        chunk = str(start)
        sib = start.find_next_sibling()
        count = 0
        while sib and count < 6:
            chunk += str(sib)
            sib = sib.find_next_sibling()
            count += 1
        print(chunk[:1500])
    else:
        print("\nNo <h3> tags found at all — the site's markup has likely "
              "changed structure. Printing the first 1500 chars of the body:")
        body = soup.find("body")
        print(str(body)[:1500] if body else resp.text[:1500])


def parse_film_title(link_text: str):
    """
    Link text looks like:
        "Spider-Man: Brand New Day"
        "3D: Spider-Man: Brand New Day"
        "Spider-Man: Brand New Day (OmU)"
        "3D: Spider-Man: Brand New Day (OV)"
        "Atmos, D-Box: Toy Story 5 - Spielzeug war gestern"
    Split into (clean_title, [format_tags]).
    """
    text = link_text.strip()
    tags = []

    # Leading "TAG, TAG: " prefix
    prefix_match = re.match(r"^([A-Za-z0-9\-,\s]+):\s*(.+)$", text)
    if prefix_match:
        possible_tags = [t.strip() for t in prefix_match.group(1).split(",")]
        if all(t in FORMAT_WORDS for t in possible_tags):
            tags.extend(possible_tags)
            text = prefix_match.group(2)

    # Trailing " (TAG)" suffix
    suffix_match = re.search(r"\(([A-Za-z]+)\)\s*$", text)
    if suffix_match and suffix_match.group(1) in FORMAT_WORDS:
        tags.append(suffix_match.group(1))
        text = text[: suffix_match.start()].strip()

    return text, tags


def parse_day(target_date: date) -> list[dict]:
    date_str = target_date.strftime("%d.%m.%Y")
    screenings = []
    offset = 0

    while True:
        url = f"{BASE}?kinos&date={date_str}"
        if offset:
            url += f"&os={offset}"
        soup = fetch(url)

        # Each cinema's block: heading (h3) with cinema name + link, then an
        # address line, then repeating (film link, time-list) pairs until the
        # next h3. Adjust these selectors if the real markup differs.
        blocks = soup.select("h3")
        if not blocks:
            break

        found_any = False
        for h3 in blocks:
            cinema_link = h3.find("a")
            if not cinema_link:
                continue
            cinema_name = cinema_link.get_text(strip=True)
            if not cinema_name:
                continue
            found_any = True

            # Address is usually the next text node / <p> after the h3
            address = ""
            addr_el = h3.find_next_sibling(["p", "div"])
            if addr_el:
                address = addr_el.get_text(strip=True)

            # Walk forward through siblings until the next h3, collecting
            # (film link, times) pairs.
            node = h3.find_next_sibling()
            while node and node.name != "h3":
                film_link = node.find("a", href=re.compile(r"-f\d+"))
                if film_link:
                    title, tags = parse_film_title(film_link.get_text(strip=True))
                    times_text = node.get_text(" ", strip=True)
                    times = re.findall(r"\b\d{1,2}:\d{2}\b", times_text)
                    if times:
                        screenings.append({
                            "cinema": cinema_name,
                            "address": address,
                            "film": title,
                            "format_tags": tags,
                            "times": times,
                            "film_url": film_link.get("href", ""),
                        })
                node = node.find_next_sibling()

        if not found_any:
            break

        # Stop once we've seen a "next page" link that no longer advances,
        # or once a page returns no new offset link.
        next_link = soup.find("a", string=re.compile("Weiter|nächste Seite", re.I))
        if not next_link:
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

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
