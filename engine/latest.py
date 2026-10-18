"""
Fast poller: catch tenders within minutes of publication.

WHY THIS EXISTS
  The nightly sweep walks "Tenders by Closing Date / within 14 days", which
  means a tender only becomes visible once its DEADLINE comes within 14
  days -- not when it is published. Measured over 11,297 tenders, that made
  us a median of 3.8 days late, and only 17% were seen on the day they
  appeared. Since 58% of tenders allow a bid window longer than 14 days,
  anyone refreshing the portal daily was beating us on most of them. For an
  alerting tool that is the whole ballgame.

  The portal's homepage, alone among its pages, carries the newest tenders
  with no CAPTCHA. Polling it costs ONE request and surfaces publications
  within minutes. It only shows about a dozen at a time, so this does not
  replace the nightly sweep -- anything published in a burst between polls
  is still picked up later -- it just means the common case is fast.

Run:
    python3 engine/latest.py            # poll once, store anything new
    python3 engine/latest.py --dry-run  # look, store nothing
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from classify import DEPARTMENTS, classify, classify_department
from playwright.sync_api import sync_playwright
from scrape import (NAV_TIMEOUT, PORTAL, THROTTLE_SECONDS, USER_AGENT,
                    _apply_detail, _parse_detail, split_org_chain)
from store import connect, upsert

# Homepage link text is the tender title, but TRUNCATED, so it never
# matches what we store. Comparing homepage labels with homepage labels
# does work, so we keep a small cache of the ones we have already resolved.
# Without it every poll opens all 24 detail pages -- about 1,150 requests a
# day at a two-hourly cadence -- which is the hammering the project rules
# warn against. With it, a steady-state poll opens only the handful that
# are genuinely new.
SEEN_PATH = Path(__file__).resolve().parent.parent / "data" / "seen_links.json"
SEEN_KEEP = 300


def _load_seen() -> list[str]:
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_seen(labels: list[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(labels[-SEEN_KEEP:], indent=0),
                         encoding="utf-8")

HOME = PORTAL
# The homepage lists the latest tenders and the latest corrigenda together.
# A dozen each is plenty; fetching every link would be wasteful.
MAX_LINKS = 24


def poll(dry_run: bool = False, all_depts: bool = False) -> dict:
    conn = connect()
    known = {r[0] for r in conn.execute("SELECT tender_id FROM tenders")}
    # The tender id only appears on the detail page, so without this we
    # would open all 24 every poll -- about 1,150 requests a day, which is
    # exactly the hammering the project rules warn against. The homepage
    # link text is the title, and we already store titles, so we can skip
    # the ones we have seen and open only genuinely new links: a handful
    # per poll instead of two dozen.
    seen_labels = _load_seen()
    seen_set = set(seen_labels)
    counts = {"seen": 0, "new": 0, "skipped_known": 0, "out_of_scope": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        page = ctx.new_page()
        page.set_default_timeout(NAV_TIMEOUT)
        page.goto(HOME, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        hrefs = []
        for el in page.query_selector_all("a[id*='DirectLink']"):
            href = el.get_attribute("href")
            if not href:
                continue
            # Link text is "3. <title>" -- drop the numbering before
            # comparing with what we have stored.
            label = " ".join(el.inner_text().split())
            label = label.split(". ", 1)[-1].strip().lower()
            if label and label in seen_set:
                counts["skipped_known"] += 1
                continue
            if label:
                seen_labels.append(label)
                seen_set.add(label)
            if href.startswith("/"):
                href = "https://tntenders.gov.in" + href
            hrefs.append(href)
        hrefs = hrefs[:MAX_LINKS]
        print(f"  {len(hrefs)} link(s) look new; opening those only")

        detail = ctx.new_page()
        detail.set_default_timeout(NAV_TIMEOUT)
        for href in hrefs:
            counts["seen"] += 1
            try:
                detail.goto(href, wait_until="domcontentloaded")
                detail.wait_for_timeout(400)
                d = _parse_detail(detail)
            except Exception:
                continue
            finally:
                time.sleep(THROTTLE_SECONDS)

            tid = d.get("tender_id_detail", "")
            if not tid:
                continue
            if tid in known:
                counts["skipped_known"] += 1
                continue

            org, circle, local_body = split_org_chain(d.get("org_chain", ""))
            row = {
                "tender_id": tid,
                "ref_number": d.get("ref_number", ""),
                "title": d.get("work_description") or d.get("org_chain", ""),
                "org": org, "org_chain": d.get("org_chain", ""),
                "circle": circle, "local_body": local_body, "district": "",
                "published_date": d.get("published_date_detail", ""),
                "closing_date": d.get("closing_date_detail", ""),
                "opening_date": d.get("opening_date_detail", ""),
                "est_amount_raw": "", "emd": "", "tender_fee": "",
                "eligibility": "", "status": "active",
                "doc_link": HOME, "apply_link": HOME, "_raw": "",
            }
            # Reuse the detail-page mapping the nightly sweep uses, so a
            # tender caught here is stored identically to one caught there.
            _apply_detail(row, d)
            if not row.get("title"):
                row["title"] = d.get("work_description", "") or tid

            dept = classify_department(row["title"], row["org"], tid)
            if not all_depts and dept not in DEPARTMENTS:
                counts["out_of_scope"] += 1
                known.add(tid)
                continue

            tags = classify(row["title"], row["org"], row["_raw"], tid)
            row["department"] = tags["department"]
            row["work_categories"] = tags["work_categories"]

            counts["new"] += 1
            known.add(tid)
            print(f"  NEW  {tid:24} {row['published_date']:22} "
                  f"{(row['title'] or '')[:46]}")
            if not dry_run:
                upsert(conn, row)

        browser.close()
    conn.close()
    if not dry_run:
        _save_seen(seen_labels)
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all-depts", action="store_true")
    args = ap.parse_args()
    result = poll(dry_run=args.dry_run, all_depts=args.all_depts)
    print(f"\nPoll: {result}")
    sys.exit(0)
