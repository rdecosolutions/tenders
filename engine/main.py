"""
Main runner — ties the engine together.

  scrape (raw rows) -> classify (dept + work tags) -> store (upsert + events)

Run:
  python3 engine/main.py                    # full statewide run, five depts
  python3 engine/main.py --all-depts        # keep every department
  python3 engine/main.py --district Thanjavur
  python3 engine/main.py --since-hours 24   # print alerts from last 24h
  python3 engine/main.py --dry-run          # scrape + classify, store nothing

HOW A RUN SPENDS ITS REQUESTS
  Walking the listing is cheap: ~47 pages for the whole state. Detail pages
  are the expensive part, so we open one ONLY when a tender is new to us or
  its closing date moved, and only for the five departments we track. A
  first run therefore costs a few hundred detail hits; every run after it
  costs a few dozen. That is what keeps us inside "polite".

This is the process a scheduler calls once a day. The app UI then reads
data/tenders.db directly (tenders + events tables) and never scrapes.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from classify import DEPARTMENTS, classify, classify_department
from scrape import scrape
from store import connect, recent_events, upsert

# Fields that only the detail page can fill. If we skip the detail fetch for
# a tender we already know, we must carry these forward from the stored row —
# otherwise they come back empty, the change-hash differs, and the tender
# raises a phantom CORRIGENDUM on every single run.
# Fields ONLY the detail page knows. When we skip the detail fetch these
# come back empty (or, for status, wrong), so they must be restored from
# the stored row UNCONDITIONALLY — not merely when the scraped value is
# blank.
#
# That distinction is the whole bug: the listing always reports
# status="active", which is truthy, so a fill-only-if-empty rule silently
# left it alone. A tender whose detail page showed a corrigendum flipped
# corrigendum -> active every night, the change-hash moved, and it raised a
# phantom CORRIGENDUM alert. Every night. Forever.
#
# org_chain is deliberately NOT here: the listing does supply it.
DETAIL_ONLY_FIELDS = ("est_amount_raw", "emd", "tender_fee", "eligibility",
                      "location", "pincode", "status")


def _load_known(conn) -> dict[str, dict]:
    """Snapshot what we already store, keyed by tender_id."""
    cols = "tender_id, closing_date, " + ", ".join(DETAIL_ONLY_FIELDS)
    return {r["tender_id"]: dict(r)
            for r in conn.execute(f"SELECT {cols} FROM tenders")}


def run(headless: bool = True,
        district_filter: str | None = None,
        window: str = "14",
        max_pages: int = 1000,
        all_depts: bool = False,
        fetch_detail: bool = True,
        dry_run: bool = False,
        verbose: bool = True) -> dict:
    conn = connect()
    known = _load_known(conn)
    counts = {"scanned": 0, "in_scope": 0, "detail_fetched": 0, "skipped": 0,
              "NEW": 0, "CORRIGENDUM": 0, "RETENDER": 0,
              "DATE_EXTENSION": 0, "CANCELLATION": 0}

    def in_scope(raw: dict) -> bool:
        dept = classify_department(raw.get("title", ""), raw.get("org", ""),
                                   raw.get("tender_id", ""))
        return all_depts or dept in DEPARTMENTS

    def needs_detail(raw: dict) -> bool:
        """Open the detail page only when it will tell us something new."""
        if not fetch_detail or not in_scope(raw):
            return False
        prev = known.get(raw["tender_id"])
        if prev is None:
            return True                                  # never seen it
        if (prev.get("closing_date") or "") != raw.get("closing_date", ""):
            return True                                  # dates moved
        return False

    say = (lambda m: print(f"  {m}", file=sys.stderr)) if verbose else None

    for raw in scrape(headless=headless, max_pages=max_pages, window=window,
                      needs_detail=needs_detail, progress=say):
        counts["scanned"] += 1

        if not in_scope(raw):
            counts["skipped"] += 1
            continue
        counts["in_scope"] += 1

        # Optional district narrowing (statewide by default).
        if district_filter:
            hay = (f"{raw.get('district','')} {raw.get('local_body','')} "
                   f"{raw.get('title','')} {raw.get('location','')}").lower()
            if district_filter.lower() not in hay:
                continue

        prev = known.get(raw["tender_id"])
        if raw.get("_detail_fetched"):
            counts["detail_fetched"] += 1
        elif prev is not None:
            # Detail was skipped this run, so nothing in the scraped row can
            # be trusted for these fields — restore all of them from what we
            # stored, or the change-hash shifts and we invent an amendment.
            for field in DETAIL_ONLY_FIELDS:
                raw[field] = prev.get(field) or ""

        tags = classify(raw.get("title", ""), raw.get("org", ""),
                        raw.get("_raw", ""), raw.get("tender_id", ""))
        raw["department"] = tags["department"]
        raw["work_categories"] = tags["work_categories"]

        if dry_run:
            continue
        for ev in upsert(conn, raw):
            counts[ev] = counts.get(ev, 0) + 1

    conn.close()
    return counts


def show_alerts(since_hours: int) -> None:
    conn = connect()
    since = (datetime.now(timezone.utc) -
             timedelta(hours=since_hours)).isoformat()
    rows = recent_events(conn, since)
    if not rows:
        print(f"No events in the last {since_hours}h.")
        conn.close()
        return
    print(f"{len(rows)} event(s) in last {since_hours}h:\n")
    for r in rows:
        print(f"[{r['event_type']}] {r['department']} | "
              f"{r['district'] or '?'}/{r['local_body'] or '?'} | "
              f"{r['title'][:60]}")
        print(f"    closing: {r['closing_date']}  amt: {r['est_amount_raw']}")
        print(f"    {r['doc_link']}\n")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TN tender engine — one full run.")
    ap.add_argument("--district", default=None)
    ap.add_argument("--since-hours", type=int, default=None)
    ap.add_argument("--show", action="store_true", help="visible browser")
    ap.add_argument("--window", default="14", choices=["today", "7", "14"],
                    help="closing-date window to walk (default 14 days)")
    ap.add_argument("--max-pages", type=int, default=1000)
    ap.add_argument("--all-depts", action="store_true",
                    help="keep every department, not just the five")
    ap.add_argument("--no-detail", action="store_true",
                    help="skip detail pages (fast; money fields stay empty)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scrape and classify but write nothing")
    args = ap.parse_args()

    if args.since_hours is not None:
        show_alerts(args.since_hours)
    else:
        result = run(headless=not args.show, district_filter=args.district,
                     window=args.window, max_pages=args.max_pages,
                     all_depts=args.all_depts, fetch_detail=not args.no_detail,
                     dry_run=args.dry_run)
        print("\nRun complete:", result)
        if result["scanned"] == 0:
            print("\n0 scanned — the portal layout may have changed. "
                  "Run: python3 engine/scrape.py --probe")
