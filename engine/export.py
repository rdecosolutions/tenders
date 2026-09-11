"""
Move the database in and out of plain text, so it can live in git.

WHY THIS EXISTS
  On GitHub Actions every run starts on a fresh machine with nothing on it.
  But change detection only works if we remember what a tender looked like
  yesterday — otherwise every tender is "new" every night and the alerts
  feed is meaningless.

  So the repository itself is the storage. Each run:
      restore  ->  NDJSON in the repo becomes a SQLite database
      scrape   ->  engine/main.py does its normal work
      dump     ->  the database is written back out as NDJSON and committed

  NDJSON (one JSON object per line, sorted by id) rather than the .db file
  because git stores text diffs efficiently. A day's changes add a few KB;
  committing the binary database would add a fresh ~2 MB copy every night
  and the repo would be enormous within months.

  It also gives us a free audit trail: `git log data/tenders.ndjson` shows
  exactly what the portal said on any past day.

Usage:
    python3 engine/export.py --restore    # NDJSON -> data/tenders.db
    python3 engine/export.py --dump       # data/tenders.db -> NDJSON + site
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from store import connect

ROOT = Path(__file__).resolve().parent.parent
TENDERS_NDJSON = ROOT / "data" / "tenders.ndjson"
EVENTS_NDJSON = ROOT / "data" / "events.ndjson"
SITE_DATA = ROOT / "site" / "data.json"

# Kept out of the published site: an internal fingerprint nobody needs.
SITE_SKIP = {"last_hash"}
# Alerts older than this stop being interesting and would grow forever.
SITE_EVENT_DAYS = 30

# The very first sweep recorded ~2,400 NEW events in one day — every tender
# that already existed. Publishing those as "changes" buries the handful
# that actually happened overnight. A day with more NEW events than this is
# a backfill, not news, so it is left out of the alerts feed. The tenders
# themselves are unaffected; only the feed is.
BACKFILL_NEW_PER_DAY = 300


def _rows(conn, table: str, order: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM {table} ORDER BY {order}")]


def dump(conn) -> dict:
    """Write the database out as sorted NDJSON, plus the site's data file."""
    tenders = _rows(conn, "tenders", "tender_id")
    events = _rows(conn, "events", "id")

    for path, rows in ((TENDERS_NDJSON, tenders), (EVENTS_NDJSON, events)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                # sort_keys so an unchanged tender produces a byte-identical
                # line and git sees no diff for it.
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    cutoff = (datetime.now(timezone.utc)
              .replace(microsecond=0).isoformat())[:10]
    recent = [e for e in events
              if (e.get("created_at") or "")[:10] >= _days_ago(SITE_EVENT_DAYS)]

    from collections import Counter
    new_per_day = Counter(e["created_at"][:10] for e in recent
                          if e.get("event_type") == "NEW" and e.get("created_at"))
    backfill_days = {d for d, n in new_per_day.items()
                     if n >= BACKFILL_NEW_PER_DAY}
    if backfill_days:
        recent = [e for e in recent
                  if not (e.get("event_type") == "NEW"
                          and (e.get("created_at") or "")[:10] in backfill_days)]

    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps({
        "generated_at": cutoff,
        "tenders": [{k: v for k, v in t.items() if k not in SITE_SKIP}
                    for t in tenders],
        "events": recent,
    }, default=str, separators=(",", ":")), encoding="utf-8")

    return {"tenders": len(tenders), "events": len(events),
            "site_events": len(recent)}


def _days_ago(days: int) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def restore(conn) -> dict:
    """Rebuild the database from the NDJSON committed in the repo."""
    counts = {"tenders": 0, "events": 0}
    if TENDERS_NDJSON.exists():
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tenders)")}
        for line in TENDERS_NDJSON.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = {k: v for k, v in json.loads(line).items() if k in cols}
            names = ",".join(row)
            conn.execute(
                f"INSERT OR REPLACE INTO tenders ({names}) "
                f"VALUES ({','.join('?' for _ in row)})", list(row.values()))
            counts["tenders"] += 1
    if EVENTS_NDJSON.exists():
        for line in EVENTS_NDJSON.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            conn.execute(
                "INSERT OR REPLACE INTO events (id, tender_id, event_type, "
                "detail, created_at) VALUES (?,?,?,?,?)",
                (row.get("id"), row.get("tender_id"), row.get("event_type"),
                 row.get("detail"), row.get("created_at")))
            counts["events"] += 1
    conn.commit()
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    conn = connect()
    if args.restore:
        print("restored:", restore(conn))
    elif args.dump:
        print("dumped:", dump(conn))
    else:
        ap.error("choose --dump or --restore")
    conn.close()
