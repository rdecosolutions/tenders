"""
Measure how accurate this tool's own derived numbers are.

Every bug this project has shipped has been one of three kinds: a claim
nobody checked, a check run where the bug could not appear, or a failure
that never said anything. The last two are covered elsewhere -- the phantom
test runs across processes, check_layout.py guards the page. This file
covers the first, which has been the commonest by far.

The rule it enforces: anything we DERIVE -- an estimated value, a work
category, a district, a price benchmark -- has to carry a measured accuracy,
not an assumed one. The estimate rule is a fair example. It assumed every
EMD is 1% of the contract value. Nobody tested that for weeks, and when it
was finally tested it took thirty seconds, because 6,355 tenders publish
BOTH a value and an EMD and had been sitting in the database the whole
time. It was wrong 14% of the time, and badly wrong 6% of the time.

So: find the ground truth we already hold, and check ourselves against it
every night. Where there is no ground truth -- recall, mainly -- say so
plainly rather than quietly assuming the answer is fine.

Runs offline against the committed NDJSON. Writes site/quality.json for the
page to display, and a markdown summary for the Actions run.

    python3 engine/audit.py              # print the summary
    python3 engine/audit.py --write      # also write site/quality.json
"""

from __future__ import annotations

import json
import pathlib
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
TENDERS_NDJSON = ROOT / "data" / "tenders.ndjson"
EVENTS_NDJSON = ROOT / "data" / "events.ndjson"
SEEN_LINKS = ROOT / "data" / "seen_links.json"
RUNS_NDJSON = ROOT / "data" / "runs.ndjson"
SITE_QUALITY = ROOT / "site" / "quality.json"

# Below this, a filter is more misleading than useful and the page disables
# it. Matches the threshold the contract-type filter already uses.
USABLE_COVERAGE = 60.0

# An EMD more than this far from 1% of the published value means the 1%
# rule did not apply to that tender.
EMD_TOLERANCE_PCT = 3.0


# --------------------------------------------------------------- helpers

def _money(raw) -> float | None:
    digits = "".join(c for c in str(raw or "") if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _parse_closing(raw: str):
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt)
        except ValueError:
            continue
    return None


def _is_open(t: dict, now: datetime) -> bool:
    c = _parse_closing(t.get("closing_date") or "")
    return bool(c and c >= now)


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


# ------------------------------------------------------------- measures

def estimate_rule(tenders: list[dict]) -> dict:
    """
    How often is the EMD really 1% of the contract value?

    Ground truth: every tender that publishes both. This is the check that
    should have existed before the estimate feature shipped.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_exp", ROOT / "engine" / "export.py")
    exp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exp)

    def off_by_more_than_tolerance(emd: float, value: float) -> bool:
        return emd / value * 100.0 > EMD_TOLERANCE_PCT

    both = wrong = 0            # the rule, applied blindly
    pub_total = pub_wrong = 0   # the rule as we actually gate it
    for t in tenders:
        v, e = _money(t.get("est_amount_raw")), _money(t.get("emd"))
        if not (v and e and v > 0 and e > 0):
            continue
        bad = off_by_more_than_tolerance(e, v)
        both += 1
        wrong += bad
        if not exp._emd_is_not_one_percent(e, t):
            pub_total += 1
            pub_wrong += bad

    candidates = sum(1 for t in tenders
                     if not _money(t.get("est_amount_raw"))
                     and _money(t.get("emd")))
    return {
        "ground_truth_rows": both,
        "raw_rule_error_pct": _pct(wrong, both),
        "published_error_pct": _pct(pub_wrong, pub_total),
        "estimates_published": pub_total,
        "candidates_without_value": candidates,
        "note": "EMD x 100, checked against tenders that publish both",
    }


def field_coverage(tenders: list[dict], now: datetime) -> dict:
    """
    What share of OPEN tenders actually carry each field a filter needs?

    The contract-type filter ran on 1% of the data for weeks and said
    nothing. Coverage is the number that makes that impossible to miss.
    """
    open_rows = [t for t in tenders if _is_open(t, now)]
    watched = ("tender_category", "tender_type", "est_amount_raw", "emd",
               "district", "work_categories", "closing_date", "published_date",
               "documents", "eligibility", "work_description")
    out = {}
    for f in watched:
        have = sum(1 for t in open_rows if (t.get(f) or "").strip())
        out[f] = {"pct": _pct(have, len(open_rows)),
                  "usable": _pct(have, len(open_rows)) >= USABLE_COVERAGE}
    return {"open_tenders": len(open_rows), "fields": out}


def classification(tenders: list[dict], now: datetime) -> dict:
    """
    Coverage of the tagging, split out honestly.

    Coverage is NOT accuracy -- confusing the two is exactly how this
    project once reported 86% when the real precision was about 75%.
    Accuracy needs hand-judging, so it is read from a stored sample rather
    than invented here.
    """
    open_rows = [t for t in tenders if _is_open(t, now)]
    dept = sum(1 for t in open_rows if (t.get("department") or "").strip())
    work = sum(1 for t in open_rows if (t.get("work_categories") or "").strip())
    dist = sum(1 for t in open_rows if (t.get("district") or "").strip())
    sample_path = ROOT / "data" / "classification_sample.json"
    judged = None
    if sample_path.exists():
        try:
            s = json.loads(sample_path.read_text())
            judged = {"n": s.get("n"), "correct_pct": s.get("correct_pct"),
                      "judged_on": s.get("judged_on")}
        except (json.JSONDecodeError, OSError):
            judged = None
    return {
        "department_coverage_pct": _pct(dept, len(open_rows)),
        "work_category_coverage_pct": _pct(work, len(open_rows)),
        "district_coverage_pct": _pct(dist, len(open_rows)),
        "accuracy": judged,
        "accuracy_note": (
            "coverage is not accuracy; accuracy is hand-judged on a sample"
            if judged is None else None),
    }


def benchmarks(tenders: list[dict], now: datetime) -> dict:
    """
    How much of the price-comparison claim rests on real published values.

    Benchmarks were being built from our own EMD-derived guesses, so a made
    up number was compared against a median of made up numbers. They now use
    published values only; this measures what that leaves.
    """
    cells = defaultdict(list)
    for t in tenders:
        v = _money(t.get("est_amount_raw"))
        d = (t.get("district") or "").strip()
        if not v or not d:
            continue
        for w in (t.get("work_categories") or "").split(";"):
            w = w.strip()
            if w:
                cells[f"{w}|{d}"].append(v)
    usable = {k: v for k, v in cells.items() if len(v) >= 5}
    open_rows = [t for t in tenders if _is_open(t, now)]
    can_compare = 0
    for t in open_rows:
        if not _money(t.get("est_amount_raw")):
            continue
        d = (t.get("district") or "").strip()
        if any(f"{w.strip()}|{d}" in usable
               for w in (t.get("work_categories") or "").split(";") if w.strip()):
            can_compare += 1
    return {
        "cells_with_enough_history": len(usable),
        "cells_total": len(cells),
        "open_tenders_we_can_price_check_pct": _pct(can_compare, len(open_rows)),
        "basis": "published values only, never our own estimates",
    }


def event_sanity(events: list[dict]) -> dict:
    """
    Phantom-alert detector.

    Twice now a change-detection bug has made EVERY tender look amended on
    the next run. Both times it was invisible in testing and obvious in the
    event counts. A corrigendum share far above normal means it is back.
    """
    by_day = defaultdict(Counter)
    for e in events:
        day = (e.get("created_at") or "")[:10]
        if day:
            by_day[day][e.get("event_type") or "?"] += 1
    days = sorted(by_day)[-14:]
    rows = []
    for d in days:
        c = by_day[d]
        total = sum(c.values())
        rows.append({"day": d, "total": total,
                     "corrigendum": c.get("CORRIGENDUM", 0),
                     "new": c.get("NEW", 0),
                     "corrigendum_share_pct": _pct(c.get("CORRIGENDUM", 0), total)})
    shares = [r["corrigendum_share_pct"] for r in rows if r["total"] >= 20]
    typical = round(statistics.median(shares), 1) if shares else None
    latest = rows[-1] if rows else None
    suspicious = bool(
        latest and typical is not None and latest["total"] >= 20
        and latest["corrigendum_share_pct"] > max(3 * typical, typical + 25))
    return {"recent_days": rows, "typical_corrigendum_share_pct": typical,
            "phantom_suspected": suspicious}


# --------------------------------------------------------------- recall

LOT_RE = re.compile(r"^(\d{4}_[A-Za-z&]+_\d+)_(\d+)$")


def recall_lot_gaps(tenders: list[dict]) -> dict:
    """
    Recall signal 1: missing lots inside a package we already hold.

    A tender id is YEAR_ORG_PACKAGE_LOT, and lots inside a package run
    1..n. So if we hold lots 3, 4 and 7 of a package, lots 1, 2, 5 and 6
    exist on the portal and we do not have them.

    This is a SIGNAL, not a verdict: lots can carry different closing dates,
    and the listing we walk only shows tenders closing within 14 days, so
    some gaps are legitimately out of view. Treat a rising number as a
    reason to look, not as a count of failures.
    """
    seen = defaultdict(set)
    for t in tenders:
        m = LOT_RE.match((t.get("tender_id") or "").strip())
        if m:
            seen[m.group(1)].add(int(m.group(2)))
    partial, missing = 0, 0
    examples = []
    for pkg, lots in seen.items():
        top = max(lots)
        gap = [n for n in range(1, top + 1) if n not in lots]
        if gap:
            partial += 1
            missing += len(gap)
            if len(examples) < 5:
                examples.append({"package": pkg, "have": sorted(lots)[:12],
                                 "missing": gap[:12]})
    return {"packages_seen": len(seen), "packages_with_gaps": partial,
            "lots_missing": missing,
            "gap_rate_pct": _pct(partial, len(seen)), "examples": examples}


def recall_homepage(tenders: list[dict]) -> dict:
    """
    Recall signal 2: an independent sample from a different portal route.

    The 30-minute poller reads the homepage, which is a different listing
    from the one the nightly sweep walks. Anything the homepage advertised
    that never reached our database is a candidate miss -- the only outside
    check we have that does not need a captcha.

    Caveat worth keeping in view: the homepage carries ALL departments, and
    we store only five, so most non-matches are correct exclusions rather
    than misses. The number to watch is the trend, not the level.
    """
    if not SEEN_LINKS.exists():
        return {"available": False,
                "why": "data/seen_links.json not present yet"}
    try:
        labels = json.loads(SEEN_LINKS.read_text())
    except (json.JSONDecodeError, OSError):
        return {"available": False, "why": "seen_links.json unreadable"}
    if not isinstance(labels, list):
        return {"available": False, "why": "unexpected seen_links.json shape"}

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    titles = [norm(t.get("title")) for t in tenders]
    blob = "\n".join(titles)
    matched = 0
    misses = []
    for lab in labels:
        n = norm(lab)
        if len(n) < 25:          # too short to match safely
            continue
        if n[:60] in blob:
            matched += 1
        elif len(misses) < 8:
            misses.append(lab[:90])
    checked = sum(1 for lab in labels if len(norm(lab)) >= 25)
    return {"available": True, "sample": checked, "matched": matched,
            "matched_pct": _pct(matched, checked),
            "unmatched_examples": misses,
            "note": "homepage covers all departments; we store five, so "
                    "unmatched is an upper bound on misses, not a miss count"}


def sweep_completeness() -> dict:
    """
    Recall signal 3, and the only direct one: did the sweep walk the whole
    listing, or stop early and say nothing?

    This is the measure that matters most, because it is the one failure we
    have actually seen in the wild -- runs covering a fifth of the pages
    while reporting success, which hid two days of new tenders. main.py now
    writes a row per run, so this reads a fact rather than inferring one.
    """
    runs = _load(RUNS_NDJSON)
    if not runs:
        return {"available": False,
                "why": "no runs recorded yet; the next nightly will write one"}
    recent = runs[-14:]
    scored = [r for r in recent if r.get("coverage_pct") is not None]
    incomplete = [r for r in recent if r.get("complete") is False]
    last = recent[-1]
    return {
        "available": True,
        "runs_recorded": len(runs),
        "last_run": {"at": last.get("at"),
                     "coverage_pct": last.get("coverage_pct"),
                     "pages": f"{last.get('pages_walked')}/{last.get('pages_total')}",
                     "complete": last.get("complete"),
                     "scanned": last.get("scanned"),
                     "in_scope": last.get("in_scope")},
        "incomplete_in_last_14": len(incomplete),
        "worst_coverage_pct": min((r["coverage_pct"] for r in scored),
                                  default=None),
    }


# ---------------------------------------------------------------- report

def run() -> dict:
    tenders = _load(TENDERS_NDJSON)
    events = _load(EVENTS_NDJSON)
    now = datetime.now()
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tenders_held": len(tenders),
        "estimate_rule": estimate_rule(tenders),
        "coverage": field_coverage(tenders, now),
        "classification": classification(tenders, now),
        "benchmarks": benchmarks(tenders, now),
        "events": event_sanity(events),
        "recall_lot_gaps": recall_lot_gaps(tenders),
        "recall_homepage": recall_homepage(tenders),
        "sweep": sweep_completeness(),
    }


def summarise(q: dict) -> str:
    L = ["## Data quality", ""]
    e = q["estimate_rule"]
    L.append(f"**Estimated values** — the EMD x 100 rule is wrong "
             f"{e['raw_rule_error_pct']}% of the time across "
             f"{e['ground_truth_rows']:,} tenders that publish both. After "
             f"excluding the cases we know it breaks on, the estimates we "
             f"actually publish are wrong **{e['published_error_pct']}%** of "
             f"the time ({e['estimates_published']:,} checked).")
    c = q["classification"]
    acc = c.get("accuracy")
    L.append(f"\n**Tagging** — department {c['department_coverage_pct']}%, "
             f"work category {c['work_category_coverage_pct']}%, district "
             f"{c['district_coverage_pct']}% of open tenders. "
             + (f"Hand-judged accuracy {acc['correct_pct']}% "
                f"(n={acc['n']}, {acc['judged_on']})."
                if acc else "Accuracy not measured — coverage is not accuracy."))
    b = q["benchmarks"]
    L.append(f"\n**Price comparisons** — {b['cells_with_enough_history']:,} of "
             f"{b['cells_total']:,} category/district pairs have enough "
             f"published history to compare against; "
             f"{b['open_tenders_we_can_price_check_pct']}% of open tenders can "
             f"be price-checked. Built from published values only.")
    cov = q["coverage"]
    weak = [f for f, v in cov["fields"].items() if not v["usable"]]
    L.append(f"\n**Field coverage** across {cov['open_tenders']:,} open "
             f"tenders — " + (", ".join(
                 f"{f} {cov['fields'][f]['pct']}%" for f in weak)
                 + " below the 60% a filter needs." if weak
                 else "every watched field above 60%."))
    ev = q["events"]
    L.append(f"\n**Alerts** — typical corrigendum share "
             f"{ev['typical_corrigendum_share_pct']}%. "
             + ("⚠️ **Today is far above that — check for phantom alerts.**"
                if ev["phantom_suspected"] else "No phantom spike."))
    g = q["recall_lot_gaps"]
    L.append(f"\n**Recall (lot gaps)** — {g['packages_with_gaps']:,} of "
             f"{g['packages_seen']:,} packages ({g['gap_rate_pct']}%) are "
             f"missing at least one lot; {g['lots_missing']:,} lots in total. "
             f"Some are legitimately outside the 14-day window, so watch the "
             f"trend rather than the level.")
    sw = q["sweep"]
    if sw.get("available"):
        lr = sw["last_run"]
        L.append(f"\n**Sweep completeness** — last run walked {lr['pages']} "
                 f"pages ({lr['coverage_pct']}%), scanning {lr['scanned']:,} "
                 f"tenders. "
                 + (f"⚠️ **{sw['incomplete_in_last_14']} of the last 14 runs "
                    f"stopped early.**" if sw["incomplete_in_last_14"]
                    else "No early stops in the last 14 runs."))
    else:
        L.append(f"\n**Sweep completeness** — {sw['why']}.")
    h = q["recall_homepage"]
    if h.get("available"):
        L.append(f"\n**Recall (homepage cross-check)** — {h['matched']} of "
                 f"{h['sample']} ({h['matched_pct']}%) tenders advertised on "
                 f"the portal homepage are in our database. The homepage "
                 f"carries all departments and we keep five, so the rest are "
                 f"mostly correct exclusions.")
    return "\n".join(L)


if __name__ == "__main__":
    q = run()
    print(summarise(q))
    if "--write" in sys.argv:
        SITE_QUALITY.parent.mkdir(parents=True, exist_ok=True)
        SITE_QUALITY.write_text(json.dumps(q, indent=1), encoding="utf-8")
        print(f"\nwrote {SITE_QUALITY.relative_to(ROOT)}")
