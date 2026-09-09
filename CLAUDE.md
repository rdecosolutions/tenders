# CLAUDE.md — Project context (auto-loaded every session)

## What this project is
The backend engine for a **Tamil Nadu-wide government tender monitoring app**.
It scrapes tntenders.gov.in (GePNIC), classifies each tender by department
and work category, stores full history in SQLite, and detects five event
types. The end goal is a filterable app with daily alerts.

## The person you're helping
Runs a digital agency; **not a developer**. Explain choices in plain
language. They want a working product, built in steady steps, verified as
you go. Do not dump large amounts of code without explaining what it does.

## Coverage (do not narrow this)
Statewide, five departments: **MAWS, RD&PR, DTP, ADW, HR&CE** (use HR&CE,
never HRNC). Source: the Proposed Flow doc, captured in
`docs/TN_Tender_App_Build_Spec.docx`.

## Current state of the code  (Phase 1 + 2 done, 09-Sep-2026)
- `engine/scrape.py` — REWRITTEN against the live portal. Walks the
  CAPTCHA-free "Tenders by Closing Date / within 14 days" listing statewide
  and opens detail pages on demand. Working on real data.
- `engine/classify.py` — dept + work-category tagging. Tuned on 311 real
  tenders: department 100%, work-category 87%. Working.
- `engine/store.py` — SQLite history + 5 event types. Two real bugs fixed
  (see below). Working.
- `engine/main.py` — scrape→classify→store runner, with detail-fetch
  gating and the five-department filter. Working.
- `*.py.orig` files are the pre-live-fix originals, kept for reference.

## The portal fights back: CAPTCHA (important)
As of Sep 2026 tntenders.gov.in puts a CAPTCHA on nearly every browse
route — Active Tenders, Tenders by Location, by Organisation, Advanced
Search, Corrigendum, Cancelled/Retendered, and "Closing by Date".
**We do not solve CAPTCHAs.** Three routes are still open and that is what
the scraper uses:
  - Tenders by Closing Date → Closing Today / within 7 days / within 14 days
  - every individual tender detail page
The 14-day tab carries the whole state, every department: roughly 500
pages / ~5,000 tenders, of which ~45% belong to our five departments. (An
earlier note said 47 pages — that came from a sweep that stalled early and
was wrong.) A first backfill takes 2-3 hours; a daily run is ~30 min,
because detail pages are fetched only for new or changed tenders.
Consequence to remember: a tender enters view when its closing date comes
within 14 days, not when it is published.

## Two real bugs that were fixed (do not reintroduce)
1. `store.py` used Python's `hash()` for change detection. String hashing
   is salted per process, so stored hashes never matched on the next run
   and EVERY tender raised a phantom CORRIGENDUM daily. Now sha256.
2. `upsert()` inserted every key the scraper produced, so any extra field
   crashed the INSERT with "no such column". Now filtered to real columns.
Both only ever appear across separate runs on live data, which is why the
in-process self-tests passed.

Verify the tested parts yourself anytime, offline:
`python3 engine/classify.py` and `python3 engine/store.py`.

## Ground rules
- **Be polite to the portal:** 1–2 full runs/day, throttled. Aggressive
  scraping risks CAPTCHA and IP bans.
- **Mayiladuthurai gotcha:** became a district in 2020; its tenders often
  still sit under Thanjavur/Nagapattinam circles. Keep `district` and
  `local_body` as separate fields (schema already does).
- Don't rewrite the tested modules unless there's a real bug; refine keyword
  lists as you see real data.
- Keep secrets (any email/API creds) out of git.

## If you ever change `_hash_fields`, re-hash afterwards
Stored rows carry a fingerprint computed by the OLD definition, so the next
run sees every tender as amended and fires a phantom CORRIGENDUM for each.
After any change to `_hash_fields`, run once:
```
python3 -c "import sys; sys.path.insert(0,'engine'); import store; \
            print(store.rehash_all(store.connect()))"
```

## Change detection: what counts as a change
`_hash_fields` in store.py must only contain fields THE PORTAL controls
(title, amount, closing date, status, EMD, tender fee). Do not add
`work_categories` or any other field we derive ourselves — it was in there
originally, and it meant every improvement to the keyword lists re-tagged
old tenders and fired a fake "corrected" alert for each one.

## Decisions Kim has made
- Audience is Kim plus a few people, NOT a distributed product. So the
  mobile route is a **PWA** (installable web app + Web Push), not a native
  app — no app store, no Apple Developer fee, reuses `app/` entirely.
- Hosting comes before notifications, because push cannot fire from a
  sleeping laptop.

## Where to go next
Phases 1-3 done: engine works, `app/` is a working web front-end
(`python3 app/server.py`). `deploy/` holds a complete server kit
(systemd units, Caddy, setup.sh, guide) — Kim creates the VPS and domain
himself, then runs `deploy/setup.sh`. After that: PWA manifest + service
worker + Web Push, and an email digest.
