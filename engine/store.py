"""
Storage + change-detection layer (SQLite).

Holds the full tender history so the app can show, and alert on, the five
event types from the doc:
    NEW • CORRIGENDUM • RETENDER • DATE_EXTENSION • CANCELLATION

Design for the app designer:
  tenders  -> current state of every tender (one row per tender_id)
  events   -> append-only log of everything that happened (drives alerts)

The app's "daily alerts" screen is just: SELECT * FROM events
WHERE created_at >= <since> ORDER BY created_at DESC.

Change detection compares a freshly scraped tender against the stored row:
  - not seen before                    -> NEW
  - closing date moved later           -> DATE_EXTENSION
  - status/text says corrigendum       -> CORRIGENDUM
  - status/text says cancelled/retender-> CANCELLATION / RETENDER
  - amount or key fields changed        -> CORRIGENDUM (generic amendment)
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tenders.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    tender_id       TEXT PRIMARY KEY,
    ref_number      TEXT,
    department      TEXT,
    work_categories TEXT,          -- comma-separated tags
    district        TEXT,
    local_body      TEXT,          -- town / municipality / panchayat
    org_chain       TEXT,          -- full GePNIC organisation chain
    circle          TEXT,          -- middle of the chain (division/circle)
    location        TEXT,          -- detail-page Location field
    pincode         TEXT,
    title           TEXT,
    est_amount      REAL,          -- rupees, numeric
    est_amount_raw  TEXT,          -- original string as shown
    published_date  TEXT,
    closing_date    TEXT,          -- ISO where possible
    opening_date    TEXT,
    emd             TEXT,
    tender_fee      TEXT,
    eligibility     TEXT,
    doc_link        TEXT,
    apply_link      TEXT,
    status          TEXT,          -- active / cancelled / retender / etc.
    first_seen      TEXT,
    last_seen       TEXT,
    last_hash       TEXT           -- to detect any field change cheaply
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id   TEXT,
    event_type  TEXT,              -- NEW/CORRIGENDUM/RETENDER/DATE_EXTENSION/CANCELLATION
    detail      TEXT,
    created_at  TEXT,
    FOREIGN KEY (tender_id) REFERENCES tenders(tender_id)
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_tenders_dept   ON tenders(department);
CREATE INDEX IF NOT EXISTS idx_tenders_district ON tenders(district);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # WAL lets the web app read while a scrape run is writing. Without it
    # the app throws "database is locked" during the nightly run.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _table_columns(conn, table: str = "tenders") -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn) -> None:
    """Add any columns a database created by an older version is missing."""
    have = set(_table_columns(conn))
    for col in ("org_chain", "circle", "location", "pincode", "opening_date"):
        if col not in have:
            conn.execute(f"ALTER TABLE tenders ADD COLUMN {col} TEXT")
    conn.commit()


def _hash_fields(t: dict) -> str:
    """
    Stable fingerprint of the fields we treat as "did this tender change?".

    Must be sha256, NOT Python's hash(): string hashing is salted per
    process, so hash() returns a different value every run. Stored hashes
    would never match on the next day's run and every tender would raise a
    phantom CORRIGENDUM. (The old self-test missed this because it ran
    everything inside one process.)
    """
    # Only fields the PORTAL controls belong here. work_categories is our
    # own derived tag: including it meant that every time the keyword lists
    # were improved, every re-tagged tender raised a fake "corrected" alert.
    # The five event types describe what the portal did, not what we relabelled.
    key = "|".join(str(t.get(f, "")) for f in (
        "title", "est_amount_raw", "closing_date", "status", "emd",
        "tender_fee",
    ))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _parse_amount(raw: str) -> float | None:
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


# The fields whose change we can describe to a human. Keep in step with
# _hash_fields — these are exactly the portal-controlled fields.
DIFF_FIELDS = ("title", "est_amount_raw", "closing_date", "status", "emd",
               "tender_fee")

DIFF_LABELS = {
    "title": "title", "est_amount_raw": "estimated amount",
    "closing_date": "closing date", "status": "status",
    "emd": "EMD", "tender_fee": "tender fee",
}


def _describe_change(old, new: dict) -> str:
    """
    Say WHICH field moved and from what to what.

    "field(s) amended" told us nothing — not in the alerts feed the bidder
    reads, and not when debugging why an alert fired at all.
    """
    parts = []
    for field in DIFF_FIELDS:
        before = str(old[field] if old[field] is not None else "")
        after = str(new.get(field, "") or "")
        if before != after:
            label = DIFF_LABELS.get(field, field)
            if field == "title":
                parts.append("title changed")
            else:
                parts.append(f"{label}: {before or '—'} -> {after or '—'}")
    return "; ".join(parts) if parts else "amended"


def _log(conn, tender_id: str, event_type: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO events (tender_id, event_type, detail, created_at) "
        "VALUES (?, ?, ?, ?)",
        (tender_id, event_type, detail, _now()),
    )


def upsert(conn, t: dict) -> list[str]:
    """
    Insert or update one scraped tender. Returns the list of event types
    raised for it this run (may be empty if nothing changed).
    """
    t = dict(t)
    t.setdefault("est_amount", _parse_amount(t.get("est_amount_raw", "")))
    if isinstance(t.get("work_categories"), list):
        t["work_categories"] = ",".join(t["work_categories"])

    # The scraper carries working fields the table doesn't have (_raw,
    # _detail_url, org, ...). Keep only real columns or the INSERT explodes
    # with "table tenders has no column named ...".
    allowed = set(_table_columns(conn))
    t = {k: v for k, v in t.items() if k in allowed}
    new_hash = _hash_fields(t)
    now = _now()
    events: list[str] = []

    row = conn.execute(
        "SELECT * FROM tenders WHERE tender_id = ?", (t["tender_id"],)
    ).fetchone()

    status_text = f"{t.get('status','')} {t.get('title','')}".lower()

    if row is None:
        # brand new tender
        t["first_seen"] = now
        t["last_seen"] = now
        t["last_hash"] = new_hash
        cols = ",".join(t.keys())
        qs = ",".join("?" for _ in t)
        conn.execute(f"INSERT INTO tenders ({cols}) VALUES ({qs})",
                     list(t.values()))
        _log(conn, t["tender_id"], "NEW", t.get("title", ""))
        events.append("NEW")
        conn.commit()
        return events

    # existing tender — detect what changed
    if new_hash != row["last_hash"]:
        # 1. cancellation / retender by keyword
        if any(w in status_text for w in ("cancel", "cancelled")):
            _log(conn, t["tender_id"], "CANCELLATION", t.get("status", ""))
            events.append("CANCELLATION")
        elif "retender" in status_text or "re-tender" in status_text:
            _log(conn, t["tender_id"], "RETENDER", t.get("status", ""))
            events.append("RETENDER")

        # 2. date extension — closing date moved later
        old_close = row["closing_date"] or ""
        new_close = t.get("closing_date", "") or ""
        if new_close and old_close and new_close > old_close:
            _log(conn, t["tender_id"], "DATE_EXTENSION",
                 f"{old_close} -> {new_close}")
            events.append("DATE_EXTENSION")

        # 3. corrigendum — explicit keyword, or any other field change
        if "corrigend" in status_text:
            _log(conn, t["tender_id"], "CORRIGENDUM", t.get("status", ""))
            events.append("CORRIGENDUM")
        elif not events:
            # An amendment we can't label as one of the other four types —
            # so spell out exactly which field moved.
            _log(conn, t["tender_id"], "CORRIGENDUM",
                 _describe_change(row, t))
            events.append("CORRIGENDUM")

        conn.execute(
            "UPDATE tenders SET title=?, est_amount=?, est_amount_raw=?, "
            "closing_date=?, opening_date=?, emd=?, tender_fee=?, "
            "eligibility=?, status=?, work_categories=?, district=?, "
            "local_body=?, org_chain=?, location=?, pincode=?, "
            "last_seen=?, last_hash=? WHERE tender_id=?",
            (t.get("title"), t.get("est_amount"), t.get("est_amount_raw"),
             t.get("closing_date"), t.get("opening_date"), t.get("emd"),
             t.get("tender_fee"), t.get("eligibility"), t.get("status"),
             t.get("work_categories"), t.get("district"), t.get("local_body"),
             t.get("org_chain"), t.get("location"), t.get("pincode"),
             now, new_hash, t["tender_id"]),
        )
    else:
        conn.execute("UPDATE tenders SET last_seen=? WHERE tender_id=?",
                     (now, t["tender_id"]))

    conn.commit()
    return events


def rehash_all(conn) -> int:
    """
    Recompute last_hash for every stored tender using the CURRENT
    _hash_fields definition.

    Run this once whenever _hash_fields changes. Old rows carry a hash
    computed by the old definition, so without a re-hash the next run sees
    every single tender as amended and fires a phantom CORRIGENDUM for each.
    Nothing about the tenders themselves changes — only our fingerprint of
    them.

        python3 -c "import sys; sys.path.insert(0,'engine'); \
                    import store; print(store.rehash_all(store.connect()))"
    """
    rows = conn.execute("SELECT * FROM tenders").fetchall()
    for row in rows:
        conn.execute("UPDATE tenders SET last_hash=? WHERE tender_id=?",
                     (_hash_fields(dict(row)), row["tender_id"]))
    conn.commit()
    return len(rows)


def recent_events(conn, since_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT e.*, t.title, t.department, t.district, t.local_body, "
        "t.est_amount_raw, t.closing_date, t.doc_link "
        "FROM events e JOIN tenders t ON t.tender_id = e.tender_id "
        "WHERE e.created_at >= ? ORDER BY e.created_at DESC",
        (since_iso,),
    ).fetchall()


if __name__ == "__main__":
    # Self-test the change detection with synthetic tenders (no live data).
    import os
    test_db = Path(__file__).resolve().parent.parent / "data" / "_test.db"

    def _cleanup():
        # WAL mode leaves -wal and -shm sidecars; removing only the .db
        # leaves litter in data/.
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(test_db) + suffix)
            if f.exists():
                os.remove(f)

    _cleanup()
    c = connect(test_db)

    t = {
        "tender_id": "2026_MAWS_111_1", "title": "CC road Kumbakonam",
        "department": "MAWS", "work_categories": ["Roads (CC)"],
        "district": "Thanjavur", "local_body": "Kumbakonam",
        "est_amount_raw": "3177000", "closing_date": "2026-02-10",
        "status": "active",
    }
    print("run1:", upsert(c, t))                       # NEW
    print("run2:", upsert(c, t))                       # (nothing)
    t["closing_date"] = "2026-02-20"
    print("run3:", upsert(c, t))                       # DATE_EXTENSION
    t["status"] = "Corrigendum published"
    print("run4:", upsert(c, t))                       # CORRIGENDUM
    t["status"] = "Cancelled"
    print("run5:", upsert(c, t))                       # CANCELLATION
    c.close()
    _cleanup()
