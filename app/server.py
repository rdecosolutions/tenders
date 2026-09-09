"""
TN Tender app — web front-end.

Reads data/tenders.db and serves a filterable view of it. It NEVER scrapes;
the engine (engine/main.py) is the only thing that touches the portal.

Run:
    python3 app/server.py            then open http://localhost:8000
    python3 app/server.py --port 9000

Deliberately standard-library only — no Flask, no npm, nothing to install
or keep up to date. It is one file plus one HTML page, which is also what
makes it trivial to move onto a small VPS in Phase 5.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT.parent / "data" / "tenders.db"

# The five event types, and the badge wording the spec asks for.
EVENT_LABELS = {
    "NEW": "new",
    "CORRIGENDUM": "corrected",
    "RETENDER": "retendered",
    "DATE_EXTENSION": "extended",
    "CANCELLATION": "cancelled",
}


def connect() -> sqlite3.Connection:
    # read-only: the app must never be able to corrupt what the engine wrote.
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_closing(raw: str) -> datetime | None:
    """Portal dates look like '15-Sep-2026 11:00 AM'."""
    if not raw:
        return None
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def filter_options() -> dict:
    """Everything the filter sidebar needs to populate itself."""
    conn = connect()
    def col(name):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {name} FROM tenders "
            f"WHERE {name} IS NOT NULL AND {name} != '' ORDER BY {name}")]

    work = set()
    for r in conn.execute("SELECT work_categories FROM tenders "
                          "WHERE work_categories != ''"):
        work.update(t for t in r[0].split(",") if t)

    amounts = conn.execute(
        "SELECT MIN(est_amount), MAX(est_amount) FROM tenders "
        "WHERE est_amount IS NOT NULL").fetchone()
    total = conn.execute("SELECT COUNT(*) FROM tenders").fetchone()[0]
    # Read every list BEFORE closing — col() is a closure over conn.
    departments, districts, local_bodies = (
        col("department"), col("district"), col("local_body"))
    conn.close()
    return {
        "departments": departments,
        "districts": districts,
        "local_bodies": local_bodies,
        "work_categories": sorted(work),
        "amount_min": amounts[0] or 0,
        "amount_max": amounts[1] or 0,
        "total": total,
    }


def search(q: dict) -> dict:
    """Apply the spec's filter chain: dept -> place -> work -> amount -> date."""
    where, args = [], []

    def eq(field, key):
        val = q.get(key, [""])[0]
        if val:
            where.append(f"{field} = ?")
            args.append(val)

    eq("department", "department")
    eq("district", "district")
    eq("local_body", "local_body")

    work = q.get("work", [""])[0]
    if work:
        # work_categories is a comma-joined list, so match it as a member.
        where.append("(',' || work_categories || ',') LIKE ?")
        args.append(f"%,{work},%")

    for key, op in (("amount_min", ">="), ("amount_max", "<=")):
        val = q.get(key, [""])[0]
        if val:
            try:
                where.append(f"est_amount {op} ?")
                args.append(float(val))
            except ValueError:
                where.pop()

    text = q.get("q", [""])[0].strip()
    if text:
        where.append("(title LIKE ? OR tender_id LIKE ? OR local_body LIKE ? "
                     "OR ref_number LIKE ?)")
        args.extend([f"%{text}%"] * 4)

    sql = "SELECT * FROM tenders"
    if where:
        sql += " WHERE " + " AND ".join(where)

    conn = connect()
    rows = [dict(r) for r in conn.execute(sql, args)]
    conn.close()

    # Closing-date window is applied here rather than in SQL because the
    # portal stores dates as '15-Sep-2026 11:00 AM', which does not sort.
    days = q.get("closing_days", [""])[0]
    if days:
        try:
            limit = datetime.now() + timedelta(days=int(days))
            rows = [r for r in rows
                    if (d := _parse_closing(r.get("closing_date", "")))
                    and d <= limit]
        except ValueError:
            pass

    now = datetime.now()
    for r in rows:
        d = _parse_closing(r.get("closing_date", ""))
        r["_closes"] = d
        r["closes_in_days"] = (d - now).days if d else None
        r["is_closed"] = bool(d and d < now)
        r["work_list"] = [t for t in (r.get("work_categories") or "").split(",") if t]

    # Default to live opportunities only. A bidder hunting work does not want
    # yesterday's closed tenders at the top of the list.
    total_closed = sum(1 for r in rows if r["is_closed"])
    if q.get("include_closed", [""])[0] not in ("1", "true", "yes"):
        rows = [r for r in rows if not r["is_closed"]]

    # Soonest-closing first; anything undated sinks to the bottom.
    rows.sort(key=lambda r: (r["is_closed"], r["_closes"] or datetime.max))
    for r in rows:
        r.pop("_closes", None)
    return {"count": len(rows), "closed_hidden": total_closed,
            "tenders": rows[:400]}


def alerts(since_hours: int = 24) -> dict:
    since = (datetime.now(timezone.utc) -
             timedelta(hours=since_hours)).isoformat()
    conn = connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT e.event_type, e.detail, e.created_at, t.tender_id, t.title, "
        "t.department, t.district, t.local_body, t.est_amount_raw, "
        "t.closing_date, t.work_categories "
        "FROM events e JOIN tenders t ON t.tender_id = e.tender_id "
        "WHERE e.created_at >= ? ORDER BY e.created_at DESC LIMIT 300",
        (since,))]
    conn.close()
    for r in rows:
        r["badge"] = EVENT_LABELS.get(r["event_type"], r["event_type"].lower())
        r["work_list"] = [t for t in (r.get("work_categories") or "").split(",") if t]
    return {"count": len(rows), "events": rows}


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                       # noqa: N802 (stdlib API)
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if parsed.path in ("/", "/index.html"):
                body = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/filters":
                self._send(filter_options())
            elif parsed.path == "/api/tenders":
                self._send(search(query))
            elif parsed.path == "/api/alerts":
                hours = int(query.get("hours", ["24"])[0])
                self._send(alerts(hours))
            else:
                self._send({"error": "not found"}, 404)
        except sqlite3.OperationalError as exc:
            # Most likely the engine has not run yet, so there is no database.
            self._send({"error": f"database not readable: {exc}. "
                                 f"Run: python3 engine/main.py"}, 503)
        except Exception as exc:                     # keep the server alive
            self._send({"error": str(exc)}, 500)

    def log_message(self, fmt, *a):                  # quieter console
        return


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="TN Tender app (reads tenders.db)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 for local use; 0.0.0.0 on a server "
                         "(put Caddy or nginx in front — this has no TLS "
                         "and no auth of its own)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}\n"
              f"Run the engine first:  python3 engine/main.py")
    where = "localhost" if args.host == "127.0.0.1" else args.host
    print(f"TN Tender app  ->  http://{where}:{args.port}")
    print("Ctrl-C to stop.")
    # Threading: the single-threaded server blocks every other request while
    # one query runs, which is noticeable even with a handful of users.
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
