#!/usr/bin/env python3
"""
TMDb poster + rating enrichment
--------------------------------
Adds three optional fields to every screening record in data/<date>.json:
    "poster_url": "https://image.tmdb.org/t/p/w185/xxxx.jpg"   (or null)
    "rating":     7.4                                          (or null)
    "tmdb_id":    950387                                       (or null, used for a "more info" link)

Why TMDb specifically: IMDb's data is enterprise-licensed (not free),
and Rotten Tomatoes' public API has been partner-only since ~2020.
TMDb (The Movie Database) is the one major film database that still
gives out a genuinely free API key for a hobby project like this one.
Sign-up (free): https://www.themoviedb.org/settings/api

Runs as its own step in the GitHub Action, after all three scrapers
have written today's (and the next few days') data files. Enriches
every date file currently listed in data/index.json in one pass.

Caching: results are cached by exact film title in data/tmdb_cache.json
(committed to the repo alongside the data files), so the same film is
only ever looked up once — not once per cinema showing it, not once per
day it's still running. Misses (no usable TMDb match) are cached too, as
{"poster_url": null, "rating": null}, rather than re-querying forever;
delete a specific entry from the cache file to force a retry (e.g. if a
title was too obscure/mistyped the first time round but might match now).

Same principle as the rest of this project: a wrong guess is worse than
no poster. This only trusts TMDb's #1 search result, and only accepts it
if it actually came back with a poster image — anything with zero
results or no poster is stored as a genuine miss, not filled in with a
plausible-looking wrong film.

If TMDB_API_KEY isn't set (e.g. the secret hasn't been configured yet),
this exits quietly without touching any data files — posters/ratings are
additive, the site works fine without them.

Usage:
    export TMDB_API_KEY=xxxxx
    python enrich_tmdb.py
"""

import os
import json
import time
from pathlib import Path

import requests

API_KEY = os.environ.get("TMDB_API_KEY", "")
SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
POSTER_BASE = "https://image.tmdb.org/t/p/w185"
REQUEST_DELAY = 0.3  # polite pacing — TMDb's free tier is generous, no need to hammer it


def load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


def lookup_tmdb(title: str) -> dict:
    """Returns {"poster_url": ... or None, "rating": ... or None,
    "tmdb_id": ... or None}. Only ever trusts TMDb's top search result,
    and only if it actually has a poster — no guessing past that."""
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"api_key": API_KEY, "query": title, "language": "de-DE"},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"  TMDb lookup failed for '{title}': {e}")
        return {"poster_url": None, "rating": None, "tmdb_id": None}

    if not results:
        return {"poster_url": None, "rating": None, "tmdb_id": None}

    top = results[0]
    poster_path = top.get("poster_path")
    rating = top.get("vote_average")
    return {
        "poster_url": f"{POSTER_BASE}{poster_path}" if poster_path else None,
        "rating": round(rating, 1) if isinstance(rating, (int, float)) and rating else None,
        "tmdb_id": top.get("id"),
    }


def main():
    if not API_KEY:
        print("TMDB_API_KEY not set — skipping poster/rating enrichment (data files unchanged).")
        return

    out_dir = Path(__file__).parent / "data"
    index_path = out_dir / "index.json"
    if not index_path.exists():
        print("No data/index.json found — nothing to enrich yet.")
        return
    dates = json.loads(index_path.read_text(encoding="utf-8"))["dates"]

    cache_path = out_dir / "tmdb_cache.json"
    cache = load_cache(cache_path)

    # Collect every unique film title across all currently-published dates.
    date_files = {}
    all_titles = set()
    for d in dates:
        path = out_dir / f"{d}.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        date_files[d] = (path, payload)
        for s in payload["screenings"]:
            all_titles.add(s["film"])

    new_lookups = 0
    for title in sorted(all_titles):
        if title in cache:
            continue
        print(f"Looking up: {title}")
        cache[title] = lookup_tmdb(title)
        new_lookups += 1
        time.sleep(REQUEST_DELAY)

    if new_lookups:
        save_cache(cache_path, cache)
    print(f"{new_lookups} new TMDb lookups this run, {len(cache)} total cached titles.")

    # Merge cached poster/rating into every screening across every date file.
    for d, (path, payload) in date_files.items():
        changed = False
        for s in payload["screenings"]:
            info = cache.get(s["film"], {"poster_url": None, "rating": None, "tmdb_id": None})
            if (s.get("poster_url") != info["poster_url"]
                    or s.get("rating") != info["rating"]
                    or s.get("tmdb_id") != info.get("tmdb_id")):
                s["poster_url"] = info["poster_url"]
                s["rating"] = info["rating"]
                s["tmdb_id"] = info.get("tmdb_id")
                changed = True
        if changed:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Updated {path} with poster/rating data")


if __name__ == "__main__":
    main()
