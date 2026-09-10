"""
Regression test: re-seeing an unchanged tender must raise NO event.

This is the failure mode that keeps coming back, in three flavours so far:
  1. hash() being salted per process, so stored hashes never matched;
  2. work_categories (a field we derive) sitting in the change hash, so
     improving the keyword lists re-tagged tenders and "amended" them;
  3. status coming back as "active" from the listing when the detail page
     had said "corrigendum", because the restore rule only filled BLANK
     fields and "active" is not blank.

Each one produced the same symptom — a nightly flood of fake "corrected"
alerts — and each was invisible to the old in-process self-tests. This runs
the real upsert path across a simulated second night, offline.

    python3 engine/test_no_phantoms.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from main import DETAIL_ONLY_FIELDS
from store import connect, upsert

DB = Path(__file__).resolve().parent.parent / "data" / "_regression.db"


def _clean() -> None:
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(DB) + suffix)
        if f.exists():
            os.remove(f)


def _listing_row() -> dict:
    """What the LISTING alone gives us — no detail page fetched."""
    return {
        "tender_id": "2026_MAWS_999_1",
        "title": "Providing UGD manhole in Ward 12",
        "closing_date": "20-Sep-2026 03:00 PM",
        "department": "MAWS",
        "district": "Thanjavur",
        "local_body": "Kumbakonam Municipality",
        "work_categories": ["UGSS"],
        # The listing has no idea about any of these:
        "est_amount_raw": "", "emd": "", "tender_fee": "",
        "eligibility": "", "location": "", "pincode": "",
        "status": "active",          # <-- always "active", even when it isn't
    }


def main() -> int:
    _clean()
    conn = connect(DB)
    failures = []

    # Night 1: detail page fetched. It reports a corrigendum and the money.
    first = _listing_row()
    first.update({"est_amount_raw": "7,30,000", "emd": "7,300",
                  "tender_fee": "0.00", "status": "corrigendum",
                  "location": "Kumbakonam", "pincode": "612001"})
    ev1 = upsert(conn, first)
    if ev1 != ["NEW"]:
        failures.append(f"night 1 should be exactly ['NEW'], got {ev1}")

    stored = dict(conn.execute(
        "SELECT * FROM tenders WHERE tender_id=?", (first["tender_id"],)
    ).fetchone())

    # Night 2: tender unchanged, so main.py skips the detail fetch and the
    # scraped row is listing-only. Apply the same restore rule main.py uses.
    second = _listing_row()
    for field in DETAIL_ONLY_FIELDS:
        second[field] = stored.get(field) or ""
    ev2 = upsert(conn, second)
    if ev2:
        failures.append(
            f"night 2 raised {ev2} for an unchanged tender — phantom alert. "
            f"status went {stored.get('status')!r} -> {second['status']!r}")

    # Night 3: a REAL change must still be caught.
    third = dict(second)
    third["closing_date"] = "27-Sep-2026 03:00 PM"
    ev3 = upsert(conn, third)
    if "DATE_EXTENSION" not in ev3:
        failures.append(f"night 3 should detect DATE_EXTENSION, got {ev3}")

    # Night 4: a real amendment must name the field that moved.
    fourth = dict(third)
    fourth["emd"] = "9,999"
    upsert(conn, fourth)
    detail = conn.execute(
        "SELECT detail FROM events WHERE event_type='CORRIGENDUM' "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if not detail or "EMD" not in detail[0]:
        failures.append(f"amendment should name the field, got {detail and detail[0]!r}")

    conn.close()
    _clean()

    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: unchanged tenders raise nothing; real changes still fire "
          "and name the field.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
