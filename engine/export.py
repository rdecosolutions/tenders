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
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from store import connect

ROOT = Path(__file__).resolve().parent.parent
TENDERS_NDJSON = ROOT / "data" / "tenders.ndjson"
EVENTS_NDJSON = ROOT / "data" / "events.ndjson"
SITE_DATA = ROOT / "site" / "data.json"
SITE_ARCHIVE = ROOT / "site" / "archive.json"

# Open tenders stay at roughly the size of the scraper's 14-day window;
# closed ones pile up forever. Within a year they would be ~80% of what
# every visitor downloads, none of it biddable. So recently-closed stays in
# the main payload and the rest moves to an archive the page fetches only
# if someone asks for it. Nothing is discarded -- the full history is in
# data/tenders.ndjson either way.
ARCHIVE_AFTER_DAYS = 30

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


# ---------------------------------------------------------------------------
# Work that has been tendered before.
#
# A body re-publishing the same work weeks later usually means the first
# attempt failed -- too few bidders, or no qualifying one. That is a signal
# worth having: it says competition was thin, and when the price comes back
# unchanged it says the body has not reconsidered it. No competitor can
# offer this, because the portal deletes closed tenders and only someone
# who has been recording for months can see the repeat.
#
# Only titles with real descriptive wording are compared. Many titles are
# bare reference numbers like "R.D.C.No.B1/234/2026", and matching those
# groups unrelated tenders together.
MIN_TITLE_WORDS = 6
MIN_REPEAT_DAYS = 21


def _title_key(title: str) -> str | None:
    words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", title or "")]
    return " ".join(words) if len(words) >= MIN_TITLE_WORDS else None


def _mark_repeats(rows: list[dict]) -> int:
    groups: dict[tuple[str, str], list[dict]] = {}
    for t in rows:
        key = _title_key(t.get("title", ""))
        if key and t.get("local_body"):
            groups.setdefault((t["local_body"], key), []).append(t)

    marked = 0
    for members in groups.values():
        dated = [(d, t) for t in members
                 if (d := _parse_closing(t.get("published_date", "")))]
        if len(dated) < 2:
            continue
        dated.sort(key=lambda pair: pair[0])
        for i, (when, t) in enumerate(dated):
            if i == 0:
                continue
            prev_when, prev = dated[i - 1]
            gap = (when - prev_when).days
            if gap < MIN_REPEAT_DAYS:
                continue
            t["repeat_of"] = {
                "tender_id": prev["tender_id"],
                "published": prev.get("published_date", ""),
                "amount": prev.get("est_amount_raw", ""),
                "days": gap,
                "same_price": bool(
                    prev.get("est_amount_raw")
                    and prev.get("est_amount_raw") == t.get("est_amount_raw")),
            }
            marked += 1
    return marked


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

    slim = [{k: v for k, v in t.items() if k not in SITE_SKIP} for t in tenders]
    for t in slim:
        _add_estimated_value(t)
    repeats = _mark_repeats(slim)
    horizon = datetime.now() - timedelta(days=ARCHIVE_AFTER_DAYS)
    live, archive = [], []
    for t in slim:
        closing = _parse_closing(t.get("closing_date", ""))
        (archive if (closing and closing < horizon) else live).append(t)

    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    SITE_DATA.write_text(json.dumps({
        "generated_at": cutoff,
        # Set by the workflow to the commit being published. The page
        # compares it against its own and reloads if it is behind.
        "build": os.environ.get("BUILD_ID", ""),
        "archived": len(archive),
        "tenders": live,
        "events": recent,
    }, default=str, separators=(",", ":")), encoding="utf-8")
    SITE_ARCHIVE.write_text(json.dumps({
        "generated_at": cutoff,
        "tenders": archive,
    }, default=str, separators=(",", ":")), encoding="utf-8")

    return {"tenders": len(tenders), "events": len(events),
            "site_events": len(recent), "live": len(live),
            "archived": len(archive), "repeats": repeats}


# The portal leaves the tender value blank on about a quarter of tenders,
# but it almost always publishes the EMD, and the EMD is set at 1% of the
# value: across 8,298 tenders where both are known the median error of
# value = EMD x 100 is 0.0%, and 88% land within 20%. So for the ~2,570
# tenders with an EMD and no value we can state a figure the portal never
# published. It is written to a SEPARATE field, never to est_amount_raw --
# that is portal data and sits in the change-detection hash, so writing a
# derived number into it would raise a phantom corrigendum for every one.
EMD_PERCENT_OF_VALUE = 1.0

# Not every "EMD" is 1% -- a few are flat fees or mis-entered, and those
# produce nonsense (an EMD of 10 crore implied a 1,000 crore tender). Real
# published values reach 211 crore at the very top, with the 99.9th
# percentile at 17.5 crore, so anything implying more than 20 crore is
# rejected rather than shown. That drops 23 of 2,102 estimates. Blank beats
# wrong, the same rule used for districts.
ESTIMATE_CEILING = 200_000_000


def _money(raw) -> float | None:
    digits = "".join(c for c in str(raw or "") if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _emd_is_not_one_percent(emd: float, t: dict) -> bool:
    """
    Cases where the EMD is a deposit, not 1% of a contract value.

    Measured against the 6,355 tenders that publish BOTH a value and an EMD:
    the 1% rule holds for 86% of them, but it fails badly for two groups, and
    both are identifiable without knowing the value.

      - A round lakh (n x 100,000). 96.5% of those are not 1% of anything;
        at 5 lakh and above it was wrong in all 111 cases. These are flat
        deposits set by a schedule, so EMD x 100 invents a figure 10 to 100
        times the real one.
      - Rentals, leases and licences. The deposit secures the premises, not
        a contract value, so 78-93% break depending on how they are spotted.

    Applying both takes the error rate of the estimates we publish from 6.2%
    to 0.66%, catching 90% of the bad ones and withdrawing 4.5% of the good.
    Blank beats wrong, the same rule used for districts.
    """
    if emd >= 100_000 and emd % 100_000 == 0:
        return True
    blob = f"{t.get('title') or ''} {t.get('work_categories') or ''}".lower()
    return any(k in blob for k in ("rental", "rent ", "lease", "licen"))


def _add_estimated_value(t: dict) -> None:
    """Fill est_from_emd when the portal published an EMD but no value."""
    if _money(t.get("est_amount_raw")):
        return
    emd = _money(t.get("emd"))
    if not emd or emd <= 0:
        return
    if _emd_is_not_one_percent(emd, t):
        return
    estimate = round(emd * 100.0 / EMD_PERCENT_OF_VALUE)
    if estimate > ESTIMATE_CEILING:
        return
    t["est_from_emd"] = estimate


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def _parse_closing(raw: str):
    """Portal dates look like '15-Sep-2026 11:00 AM'."""
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt)
        except ValueError:
            continue
    return None


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
