# TN Tender Monitoring — Engine

The backend engine for a Tamil Nadu-wide tender monitoring app. Scrapes
tntenders.gov.in, classifies each tender by department and work category,
stores full history in SQLite, and detects five event types (new,
corrigendum, retender, date-extension, cancellation).

The scraper now runs against the live portal. Remaining work is a
front-end and hosting. See `docs/TN_Tender_App_Build_Spec.docx` for the
full plan and `CLAUDE.md` for the live-site findings.

## How it gets the data (read this before changing scrape.py)
The portal CAPTCHA-gates almost every browse route. We do not solve
CAPTCHAs. The open route is **Tenders by Closing Date → "Closing within
14 days"**, which lists the whole state (~470 tenders, 47 pages), plus the
individual tender detail pages, which are also open.

So a tender becomes visible to us when its closing date comes within 14
days — usually within a few days of publication, but later for tenders
with long bid windows. Nothing is missed; some things are seen late.

Detail pages hold the money fields (tender value, EMD, tender fee) and the
Location that pins down the district. They are fetched only for tenders
that are new or whose dates moved, and only for the five departments — a
first run opens a few hundred, a daily run a few dozen.

## The app
```
python3 app/server.py        then open http://localhost:8000
```
Standard-library Python only — nothing to install. It opens the database
read-only, so it can never corrupt what the engine wrote, and it reads
happily while a scrape run is in progress (the DB is in WAL mode).

Two screens:
- **Tenders** — filter by Department, District, Local body, Work type,
  amount range and closing window. Defaults to open tenders, soonest
  closing first; tick "Include closed tenders" to see the rest.
- **Alerts** — the last 7 days of changes, badged new / corrected /
  retendered / extended / cancelled.

## Running it on a server
See `deploy/README.md`. Short version: a €4/mo Ubuntu box, `bash setup.sh
<domain> <email>`, and the engine runs nightly at 02:30 behind HTTPS with a
password. Needed before notifications can work at all — push cannot come
from a sleeping laptop.

## Files
```
deploy/       server setup: systemd units, Caddy config, setup.sh, guide
app/
  server.py     read-only JSON API + static page (stdlib only)
  index.html    the whole UI, one file
engine/
  classify.py   department + work-category rules   (tested, working)
  store.py      SQLite + 5-type change detection    (tested, working)
  main.py       scrape -> classify -> store runner  (working)
  scrape.py     portal scraper                      (needs live-site fix)
data/
  tenders.db    created on first run (tenders + events tables)
docs/
  TN_Tender_App_Build_Spec.docx   the handoff spec
```

## Quick start (on a machine with internet)
```
pip3 install playwright
python3 -m playwright install chromium

# Scraper alone (prints real tender rows, stores nothing):
python3 engine/scrape.py --window today

# Full run: scrape + classify + store. ~20-25 min the first time,
# ~5-8 min after that. Statewide, the five departments.
python3 engine/main.py

# Everything, not just the five departments:
python3 engine/main.py --all-depts

# Fast pass with no detail pages (money fields stay empty):
python3 engine/main.py --no-detail

# See recent alerts:
python3 engine/main.py --since-hours 24

# Re-check the live markup if the portal changes:
python3 engine/scrape.py --probe      # writes data/page.html + page.png
```

## Verify without the live site
The two hard parts are tested offline:
```
python3 engine/classify.py    # shows dept + work-tag classification
python3 engine/store.py       # shows all 5 event types firing
```

## Known limitation: no deep link to a single tender
Every per-tender URL the portal hands out carries a session-bound token
that expires with the session, and there is no public permalink. So
`doc_link` / `apply_link` store the stable listing page, and `tender_id`
(e.g. `2026_MAWS_695413_1`) is the thing to show and search on. Worth
revisiting in Phase 3 — it is the one field the app cannot make clickable.

## Notes
- Statewide by default. Use `--district Thanjavur` to narrow.
- Be polite: run once a day, not constantly. 1.5s between every fetch.
- The portal's result ordering is unstable, so one sweep sees roughly
  two-thirds of the window and rows repeat across pages. Duplicates are
  harmless (upsert by tender_id) and running daily closes the gap, since
  each tender sits in the 14-day window for many consecutive days.
- Move SQLite -> Postgres when more than one device reads concurrently;
  the schema ports directly.
