# Berlin Kinoprogramm

A free, ad-free, non-commercial showtimes board for every cinema in Berlin —
no ticket links, just what's playing, where, and in which language.

## How it's built

- **`index.html`** — the whole frontend. One file, no build step, no
  framework. Opens directly in a browser. Ships with real demo data for
  *Spider-Man: Brand New Day* (01.08.2026) so it works the moment you open
  it, and automatically switches to live data if it finds `data/latest.json`
  next to it.
- **`scraper.py`** — scrapes `kino.veranstaltungen-in-berlin.de`, a site that
  already aggregates ~50+ Berlin/Potsdam cinemas into one feed (it's the
  closest thing to "the one communal source" — just poorly presented).
  Writes `data/<date>.json` and `data/latest.json`.
- **`.github/workflows/update-showtimes.yml`** — runs the scraper twice a
  day automatically and commits the fresh data. This is what makes the app
  "live" without you ever touching it.

## Get it fully live (10 minutes, free, one-time setup)

1. Create a new **public** GitHub repo and push these files to it.
2. In the repo, go to **Settings → Pages**, set source to the `main` branch
   (root), save. GitHub gives you a URL like
   `https://yourname.github.io/berlin-kino/`.
3. Go to **Settings → Actions → General → Workflow permissions**, select
   "Read and write permissions" (so the scheduled job can commit its own
   data updates).
4. That's it. The Action in `.github/workflows/update-showtimes.yml` runs
   automatically at 08:00 and 16:00 Berlin time every day, scrapes the
   current showtimes for every film in Berlin, and commits `data/latest.json`.
   Your GitHub Pages site picks it up on the next page load — no manual
   step, ever.

You can also trigger it manually any time from the **Actions** tab →
"Update Berlin showtimes" → **Run workflow**, e.g. to test it right after
setup instead of waiting for the schedule.

## Coverage: what's actually in here

Two scrapers, two confidence levels:

**`scraper.py`** → `kino.veranstaltungen-in-berlin.de`. Covers the big
chains and most independents: CineStar, Cineplex, UCI, CinemaxX, Zoo
Palast, Kulturbrauerei, Astra, and dozens more — this is the bulk of
Berlin's screens. I read this site's actual HTML structure through a
text-mode fetch and wrote the scraper against it directly, so I'm fairly
confident in it. Run it once to confirm; if `data/latest.json` comes back
empty, run `python scraper.py --debug` and send me the output — it dumps
exactly what the scraper is seeing so I can fix the selectors in one pass.

**`scraper_yorck.py`** → `yorck.de`, covering the **Yorck Kinogruppe**:
delphi LUX, Odeon, Passage, Rollberg, Kant Kino, Kino International,
Capitol Dahlem, Casablanca-adjacent arthouse venues, and more — roughly 14
cinemas, and the ones most likely to show OV/OmU. The main aggregator
doesn't include them at all, which is a real gap given what you're using
this for. But Yorck's site is a JavaScript app, so this scraper needs a
real headless browser (Playwright) rather than a simple HTML fetch — and
I could not execute or render JavaScript from where I built this, so I
could not verify this one against the live, rendered page. It's a
reasonable first attempt (built to be structure-agnostic rather than
relying on exact CSS classes), but budget one round of "run `--debug
<cinema-slug>`, paste me what it prints, I fix it" before trusting it.

**Still not covered by either:** a handful of very small independents and
seasonal open-air cinemas (Sinema Transtopia, Moviemento, Wolf Kino,
Freiluftkino Kreuzberg, and similar). Same pattern as Yorck — each is a
small, separate addition rather than a blocker to using the app now.

## Other things worth knowing

- Both scrapers depend on their source site's HTML shape staying roughly
  stable. If a site redesigns, the affected scraper needs small tweaks —
  the `--debug` mode in each is built for exactly that.
- Respectful scraping: `scraper.py` waits 1 second between requests and
  identifies itself with a real User-Agent; both scrapers only run twice a
  day via the schedule. Please keep it that way — these are small sites
  doing you a favor, not commercial APIs built to take load.
