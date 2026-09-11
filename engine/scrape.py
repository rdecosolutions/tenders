"""
Statewide scraper for tntenders.gov.in (GePNIC).

WHAT THIS DOES
  Walks the portal's "Tenders by Closing Date" listing, yields raw tender
  dicts, and (optionally) opens each tender's detail page to fill in the
  money fields. It does NOT classify or store — main.py wires those in.

WHY THIS ROUTE (important, read before "improving" it)
  As of Sep 2026 the TN portal puts a CAPTCHA in front of almost every
  browse route: Active Tenders, Tenders by Location, Tenders by
  Organisation, Advanced Search, Corrigendum, Cancelled/Retendered and
  "Closing by Date" all refuse to list anything without one.

  We do NOT solve CAPTCHAs. The three tabs on FrontEndListTendersbyDate —
  "Closing Today", "Closing within 7 days", "Closing within 14 days" —
  are open, and so are the individual tender detail pages. The 14-day tab
  is the widest open window and carries the whole state, every department,
  so that is what we walk.

  Coverage consequence, stated honestly: a tender enters our view when its
  closing date comes within 14 days, not on the day it is published. For
  the five departments we track, bid windows are usually 7-21 days, so most
  tenders are seen within a few days of publication — but a tender with a
  long lead time is seen late. Nothing is missed, some things are late.

SESSION NOTE
  Detail links carry an encrypted, session-bound `sp=` token. They expire
  with the session, so a detail page must be fetched during the same run
  that listed it. That is why `scrape()` takes a `needs_detail` callback
  instead of returning links for someone else to fetch later.

POLITENESS
  One full run a day. THROTTLE_SECONDS between every page fetch, and
  detail pages are fetched only for tenders the caller says it needs
  (new or changed), never for the whole list.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

PORTAL = "https://tntenders.gov.in/nicgep/app"
LIST_URL = f"{PORTAL}?page=FrontEndListTendersbyDate&service=page"
NAV_TIMEOUT = 60_000
THROTTLE_SECONDS = 1.5      # polite pause between page fetches
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/145.0.0.0 Safari/537.36")

# --- SELECTORS: verified against the live DOM on 09-Sep-2026 ---------------
SELECTORS = {
    # The three CAPTCHA-free closing-date tabs.
    "tab_today": "#tabByClosingToday",
    "tab_7day": "#LinkSubmit_0",
    "tab_14day": "#LinkSubmit_1",
    # Results table: <table id="table" class="list_table">, data rows are the
    # ones whose first cell is "1.", "2.", ... (header/footer rows are not).
    "results_table": "table#table",
    "row_link": "a[id^='DirectLink']",
    "next_link": "#linkFwd",
    # Detail page renders as <td class="td_caption">label</td>
    #                        <td class="td_field">value</td>
    "detail_cells": "td.td_caption, td.td_field",
}

WINDOWS = {"today": "tab_today", "7": "tab_7day", "14": "tab_14day"}

TENDER_ID_RE = re.compile(r"\d{4}_[A-Za-z&]+_\d+_\d+")
SNO_RE = re.compile(r"^\d+\.$")

# ---------------------------------------------------------------------------
# Tamil Nadu districts, with the spelling variants the portal actually uses.
# Canonical name on the left; everything on the right maps onto it.
# ---------------------------------------------------------------------------
DISTRICT_ALIASES: dict[str, list[str]] = {
    "Ariyalur": ["ariyalur"],
    "Chengalpattu": ["chengalpattu", "chengalpet", "chenglepet"],
    "Chennai": ["chennai", "madras"],
    "Coimbatore": ["coimbatore", "kovai"],
    "Cuddalore": ["cuddalore", "kadalur"],
    "Dharmapuri": ["dharmapuri"],
    "Dindigul": ["dindigul"],
    "Erode": ["erode"],
    "Kallakurichi": ["kallakurichi"],
    "Kancheepuram": ["kancheepuram", "kanchipuram", "kanchee"],
    "Kanniyakumari": ["kanniyakumari", "kanyakumari", "nagercoil"],
    "Karur": ["karur"],
    "Krishnagiri": ["krishnagiri"],
    "Madurai": ["madurai"],
    "Mayiladuthurai": ["mayiladuthurai", "mayiladuturai", "mayiladythurai",
                       "mayavaram"],
    "Nagapattinam": ["nagapattinam", "nagai"],
    "Namakkal": ["namakkal"],
    "Nilgiris": ["nilgiris", "nilgiri", "ooty", "udhagamandalam"],
    "Perambalur": ["perambalur"],
    "Pudukkottai": ["pudukkottai", "pudukottai"],
    "Ramanathapuram": ["ramanathapuram", "ramnad"],
    "Ranipet": ["ranipet"],
    "Salem": ["salem"],
    "Sivaganga": ["sivaganga", "sivagangai"],
    "Tenkasi": ["tenkasi"],
    "Thanjavur": ["thanjavur", "tanjore", "tanjavur"],
    "Theni": ["theni"],
    "Thoothukudi": ["thoothukudi", "tuticorin", "thoothukkudi"],
    "Tiruchirappalli": ["tiruchirappalli", "tiruchirapalli", "trichirapalli",
                        "thiruchirapalli", "tiruchy", "trichy",
                        "tiruchirappali"],
    "Tirunelveli": ["tirunelveli", "nellai"],
    "Tirupathur": ["tirupathur", "tirupattur"],
    "Tiruppur": ["tiruppur", "tirupur"],
    "Tiruvallur": ["tiruvallur", "thiruvallur"],
    "Tiruvannamalai": ["tiruvannamalai", "thiruvannamalai"],
    "Tiruvarur": ["tiruvarur", "thiruvarur"],
    "Vellore": ["vellore"],
    "Viluppuram": ["viluppuram", "villupuram"],
    "Virudhunagar": ["virudhunagar", "virudunagar"],
}

# Longest aliases first so "tiruchirappalli" wins over a shorter substring.
_ALIAS_INDEX: list[tuple[str, str]] = sorted(
    ((alias, canon) for canon, aliases in DISTRICT_ALIASES.items()
     for alias in aliases),
    key=lambda pair: len(pair[0]), reverse=True,
)



# ---------------------------------------------------------------------------
# Town -> district. Half of MAWS tenders had no district because the local
# body is a town whose name is not a district name: Hosur is in Krishnagiri,
# Palani in Dindigul, Tambaram in Chengalpattu. Without this the district
# filter silently missed them.
#
# Only towns whose district is unambiguous are listed. A wrong district is
# worse than a blank one — someone filtering for Thanjavur must not be shown
# work in Salem — so genuinely ambiguous names are deliberately left out and
# simply stay blank.
# ---------------------------------------------------------------------------
TOWN_DISTRICT: dict[str, str] = {
    "adirampattinam": "Thanjavur", "arakkonam": "Ranipet",
    "aranthangi": "Pudukkottai", "arruppukottai": "Virudhunagar",
    "aruppukottai": "Virudhunagar", "attur": "Salem", "avadi": "Tiruvallur",
    "avinashi": "Tiruppur", "chengam": "Tiruvannamalai",
    "chidambaram": "Cuddalore", "colachal": "Kanniyakumari",
    "colachel": "Kanniyakumari", "devakottai": "Sivaganga",
    "gobichettipalam": "Erode", "gobichettipalayam": "Erode",
    "harur": "Dharmapuri", "hosur": "Krishnagiri", "idapadi": "Salem",
    "edappadi": "Salem", "jayankondam": "Ariyalur",
    "jolarpet": "Tirupathur", "jolarpettai": "Tirupathur",
    "kadayanallur": "Tenkasi", "karaikudi": "Sivaganga",
    "karamadai": "Coimbatore", "kayalpattinam": "Thoothukudi",
    "komarapalayam": "Namakkal", "kotagiri": "Nilgiris",
    "kottakuppam": "Viluppuram", "kudiyatham": "Vellore",
    "gudiyatham": "Vellore", "kulithalai": "Karur",
    "kumbakonam": "Thanjavur", "kundrathur": "Kancheepuram",
    "lalgudi": "Tiruchirappalli", "madhuranthagam": "Chengalpattu",
    "madurantakam": "Chengalpattu", "mamallapuram": "Chengalpattu",
    "manaparai": "Tiruchirappalli", "mangadu": "Kancheepuram",
    "melur": "Madurai", "melvisharam": "Ranipet",
    "mettupalayam": "Coimbatore", "mettur": "Salem",
    "musiri": "Tiruchirappalli", "nandivaram": "Chengalpattu",
    "guduvancheri": "Chengalpattu", "nellikuppam": "Cuddalore",
    "padmanabapuram": "Kanniyakumari", "palani": "Dindigul",
    "palladam": "Tiruppur", "pallipalayam": "Namakkal",
    "pattukottai": "Thanjavur", "periyakulam": "Theni",
    "pernampattu": "Tirupathur", "perundurai": "Erode",
    "pollachi": "Coimbatore", "polur": "Tiruvannamalai",
    "ponneri": "Tiruvallur", "poonamallee": "Tiruvallur",
    "pugalur": "Karur", "punjaipuliampatti": "Erode",
    "rameswaram": "Ramanathapuram", "rasipuram": "Namakkal",
    "sankagiri": "Salem", "sankari": "Salem",
    "sankarankoil": "Tenkasi", "sattur": "Virudhunagar",
    "sirkazhi": "Mayiladuthurai", "sivakasi": "Virudhunagar",
    "surandai": "Tenkasi", "tambaram": "Chengalpattu",
    "thuraiyur": "Tiruchirappalli", "tiruchendur": "Thoothukudi",
    "tiruchengode": "Namakkal", "tirukovilur": "Kallakurichi",
    "tirumangalam": "Madurai", "tiruthuraipoondi": "Tiruvarur",
    "tiruttani": "Tiruvallur", "usilampatti": "Madurai",
    "vaniyambadi": "Tirupathur", "vellakoil": "Tiruppur",
    "virudhachalam": "Cuddalore", "pammal": "Chengalpattu",
    "perungalathur": "Chengalpattu",
}

_TOWN_INDEX: list[tuple[str, str]] = sorted(
    TOWN_DISTRICT.items(), key=lambda kv: len(kv[0]), reverse=True)

def find_district(*texts: str) -> str:
    """
    Return the canonical TN district named anywhere in the given text, or "".

    Mayiladuthurai gotcha: it became a district in 2020 but many works there
    still sit under Thanjavur/Nagapattinam circles. We match Mayiladuthurai
    ahead of nothing else — if the chain says Nagapattinam and the location
    says Mayiladuthurai, the caller passes location first and wins.
    """
    for text in texts:
        if not text:
            continue
        low = text.lower()
        for alias, canon in _ALIAS_INDEX:
            if alias in low:
                return canon
    # No district name anywhere — fall back to the town it names.
    for text in texts:
        if not text:
            continue
        low = text.lower()
        for town, canon in _TOWN_INDEX:
            if town in low:
                return canon
    return ""


def split_org_chain(chain: str) -> tuple[str, str, str]:
    """
    Split a GePNIC organisation chain into (org, circle, local_body).

    Real examples:
      "Rural Development and Panchayat Raj Department||Trichirapalli,RD,TN||
       MANIKANDAM - VP, THIRUCHIRAPALLI,RD,TN"
      "Municipal Administration and Water Supply Department||Commissioner
       of Municipal Administration||Kumbakonam Municipality"
    """
    parts = [p.strip() for p in (chain or "").split("||") if p.strip()]
    if not parts:
        return "", "", ""
    org = parts[0]
    local_body = parts[-1] if len(parts) > 1 else ""
    circle = parts[1] if len(parts) > 2 else ""
    return org, circle, local_body


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def _money(raw: str) -> str:
    """Normalise a portal money string; 'NA'/'0.00' become ''."""
    v = _clean(raw)
    if v.upper() in ("", "NA", "N/A", "NIL", "-"):
        return ""
    return v


# ---------------------------------------------------------------------------
# Listing page
# ---------------------------------------------------------------------------

def _parse_list_rows(page) -> list[dict]:
    """
    Parse the visible results table into raw listing dicts.

    Columns (verified live):
      0 S.No | 1 e-Published Date | 2 Bid Submission Closing Date
      3 Tender Opening Date | 4 Title and Ref.No./Tender ID
      5 Organisation Chain

    Cell 4 renders as:  <a>[Title]</a> [RefNumber][TenderId]
    """
    rows: list[dict] = []
    for tr in page.query_selector_all(f"{SELECTORS['results_table']} tr"):
        cells = tr.query_selector_all("td")
        if len(cells) < 6 or not SNO_RE.match(_clean(cells[0].inner_text())):
            continue

        title_cell = cells[4]
        link = title_cell.query_selector(SELECTORS["row_link"])
        anchor_text = _clean(link.inner_text()) if link else ""
        title = anchor_text.strip("[]").strip()

        full = _clean(title_cell.inner_text())
        tail = full[len(anchor_text):] if anchor_text else full
        bracketed = re.findall(r"\[([^\]]*)\]", tail)
        tender_id = next((b for b in bracketed if TENDER_ID_RE.fullmatch(b)), "")
        if not tender_id:
            m = TENDER_ID_RE.search(full)
            tender_id = m.group(0) if m else ""
        if not tender_id:
            continue                      # nothing we can key on — skip
        ref_number = next((b for b in bracketed if b != tender_id), "")

        chain = _clean(cells[5].inner_text())
        org, circle, local_body = split_org_chain(chain)

        href = link.get_attribute("href") if link else ""
        if href and href.startswith("/"):
            href = "https://tntenders.gov.in" + href

        rows.append({
            "tender_id": tender_id,
            "ref_number": ref_number,
            "title": title,
            "org": org,
            "org_chain": chain,
            "circle": circle,
            "district": find_district(chain),
            "local_body": local_body,
            "published_date": _clean(cells[1].inner_text()),
            "closing_date": _clean(cells[2].inner_text()),
            "opening_date": _clean(cells[3].inner_text()),
            "est_amount_raw": "",
            "emd": "",
            "tender_fee": "",
            "eligibility": "",
            # NOT href: the row's link carries a session-bound `sp=` token
            # that dies with the session, so storing it would give the app a
            # dead link within the hour. The portal exposes no public
            # permalink for a tender, so we store the stable listing page and
            # rely on tender_id, which is the portal's own identifier and is
            # what you search on. (Open question for the app: how best to
            # deep-link — see README.)
            "doc_link": LIST_URL,
            "apply_link": LIST_URL,
            "status": "active",
            "_detail_fetched": False,
            "_detail_url": href,
            "_raw": f"{title} | {chain}",
        })
    return rows


# GePNIC is a Tapestry app: tabs and pagination are form POSTs, not links.
# Clicking one starts a navigation, so we must wait for it to land AND for
# the results table to repopulate before parsing — otherwise we parse the
# outgoing page and silently re-read the rows we just processed.
ROWS_READY_JS = """() => {
    const t = document.querySelector('table#table');
    if (!t) return false;
    return [...t.querySelectorAll('tr')].some(r => {
        const c = r.querySelectorAll('td');
        return c.length >= 6 && /^[0-9]+\\.$/.test(c[0].innerText.trim());
    });
}"""


def _wait_for_rows(page, timeout: int = 25_000) -> bool:
    """Block until the results table holds at least one numbered data row."""
    try:
        page.wait_for_function(ROWS_READY_JS, timeout=timeout)
        return True
    except Exception:
        return False


def _click_and_wait(page, element) -> bool:
    """Click a Tapestry control and wait for the new results page to settle."""
    try:
        with page.expect_navigation(wait_until="domcontentloaded",
                                    timeout=NAV_TIMEOUT):
            element.click()
    except PWTimeout:
        pass                      # some clicks re-render without a full nav
    except Exception:
        return False              # network dropped / laptop slept mid-click
    try:
        return _wait_for_rows(page)
    except Exception:
        return False


def _goto_with_retry(page, url: str, attempts: int = 3) -> bool:
    """
    Navigate, retrying transient network faults.

    A home wifi blip (ERR_NETWORK_CHANGED) or a slow portal response should
    cost us one tender, not the rest of the run.
    """
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return True
        except Exception:
            if attempt == attempts:
                return False
            time.sleep(THROTTLE_SECONDS * attempt * 2)
    return False


def _open_listing(page, window: str) -> None:
    """Load the listing and switch to the requested closing-date window."""
    # The portal intermittently answers with "Problem in accessing the
    # portal, It is suggested that you may close the Browser window and
    # reopen the browser" instead of the listing. It is transient — a fresh
    # load a few seconds later works — so retry rather than fail the run.
    for attempt in range(1, 4):
        _goto_with_retry(page, LIST_URL)
        if _wait_for_rows(page, timeout=20_000):
            break
        if attempt < 3:
            time.sleep(THROTTLE_SECONDS * attempt * 4)

    key = WINDOWS.get(window, "tab_14day")
    if key != "tab_today":
        tab = page.query_selector(SELECTORS[key])
        if tab is None:
            raise RuntimeError(
                f"Could not find the '{window} day' tab ({SELECTORS[key]}). "
                "The portal layout may have changed — re-run with --probe."
            )
        ok = _click_and_wait(page, tab)
        if not ok:
            # Same transient failure can hit the tab switch. One clean retry
            # from the top before giving up.
            time.sleep(THROTTLE_SECONDS * 6)
            _goto_with_retry(page, LIST_URL)
            _wait_for_rows(page, timeout=20_000)
            tab = page.query_selector(SELECTORS[key])
            ok = tab is not None and _click_and_wait(page, tab)
        if not ok:
            raise RuntimeError(
                f"The '{window} day' tab loaded no rows after retries. Either "
                "the portal is refusing this IP, or it has added a CAPTCHA "
                "here too. Run engine/diagnose.py to see which."
            )


# ---------------------------------------------------------------------------
# Detail page
# ---------------------------------------------------------------------------

DETAIL_FIELDS = {
    "Tender Value in": "est_amount_raw",
    "EMD Amount in": "emd",
    "Tender Fee in": "tender_fee",
    "Location": "location",
    "Pincode": "pincode",
    "Product Category": "product_category",
    "Sub category": "sub_category",
    "Work Description": "work_description",
    "NDA/Pre Qualification": "eligibility",
    "Tenderer Class": "tenderer_class",
    "Period Of Work(Days)": "period_of_work",
    "Organisation Chain": "org_chain",
    "Tender Reference Number": "ref_number",
    "Tender ID": "tender_id_detail",
    "Bid Submission End Date": "closing_date_detail",
    "Published Date": "published_date_detail",
    "Bid Opening Date": "opening_date_detail",
    "Tender Type": "tender_type",
    "Tender Category": "tender_category",
}


def _parse_detail(page) -> dict:
    """
    Read the label/value grid on a tender detail page.

    The page is a flat run of <td class="td_caption">label</td> followed by
    <td class="td_field">value</td>, so we walk cells in DOM order and pair
    each caption with the next field.
    """
    out: dict = {}
    cells = page.query_selector_all(SELECTORS["detail_cells"])
    pending: Optional[str] = None
    for cell in cells:
        cls = cell.get_attribute("class") or ""
        text = _clean(cell.inner_text())
        if "td_caption" in cls:
            pending = text.rstrip("*").strip()
        elif pending is not None:
            for label, key in DETAIL_FIELDS.items():
                if pending.startswith(label):
                    out.setdefault(key, text)
                    break
            pending = None

    # Corrigendum table on the detail page, if any.
    body = _clean(page.inner_text("body"))
    out["_has_corrigendum"] = "Corrigendum Title" in body and bool(
        re.search(r"Corrigendum Title.*?\d+\s", body)
    )
    return out


def _apply_detail(row: dict, detail: dict) -> None:
    """Fold detail-page values into a listing row, without clobbering good data."""
    row["est_amount_raw"] = _money(detail.get("est_amount_raw", ""))
    row["emd"] = _money(detail.get("emd", ""))
    row["tender_fee"] = _money(detail.get("tender_fee", ""))
    row["eligibility"] = detail.get("eligibility", "") or row.get("eligibility", "")

    if detail.get("ref_number"):
        row["ref_number"] = detail["ref_number"]
    if detail.get("org_chain"):
        row["org_chain"] = detail["org_chain"]
        org, circle, local_body = split_org_chain(detail["org_chain"])
        row["org"] = org or row["org"]
        row["circle"] = circle or row["circle"]
        row["local_body"] = local_body or row["local_body"]

    # District: the detail page's Location field is the most specific signal,
    # so it is checked first — this is what rescues Mayiladuthurai works that
    # are filed under a Thanjavur/Nagapattinam circle.
    row["district"] = find_district(
        detail.get("location", ""), row.get("org_chain", ""), row.get("title", "")
    ) or row.get("district", "")
    row["location"] = detail.get("location", "")
    row["pincode"] = detail.get("pincode", "")

    if detail.get("_has_corrigendum"):
        row["status"] = "corrigendum"

    # Give the classifier more to chew on than the title alone.
    row["_raw"] = " | ".join(filter(None, (
        row.get("title", ""), row.get("org_chain", ""),
        detail.get("work_description", ""), detail.get("product_category", ""),
        detail.get("sub_category", ""), detail.get("location", ""),
    )))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def scrape(
    headless: bool = True,
    max_pages: int = 1000,
    window: str = "14",
    needs_detail: Optional[Callable[[dict], bool]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> Iterator[dict]:
    """
    Yield raw tender dicts from every page of the closing-date listing.

    window        "today" | "7" | "14"  (14 is the widest CAPTCHA-free view)
    needs_detail  callback returning True if this row's detail page should be
                  fetched. main.py answers "yes" for tenders that are new or
                  whose listing fields changed, so a steady-state daily run
                  opens only a few dozen detail pages instead of thousands.
                  Pass None to skip detail fetching entirely (fast, but the
                  money fields stay empty).
    """
    say = progress or (lambda _m: None)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)
        detail_page = None

        try:
            _open_listing(page, window)

            page_num = 1
            while page_num <= max_pages:
                try:
                    rows = _parse_list_rows(page)
                except Exception as exc:
                    say(f"could not read page {page_num} ({exc}) — stopping. "
                        f"Everything stored so far is kept; the next run "
                        f"resumes from the start of the window.")
                    break
                if not rows:
                    say(f"page {page_num}: no rows — stopping")
                    break
                say(f"page {page_num}: {len(rows)} tenders")

                for row in rows:
                    if needs_detail is not None and row["_detail_url"] \
                            and needs_detail(row):
                        if detail_page is None:
                            detail_page = context.new_page()
                            detail_page.set_default_timeout(NAV_TIMEOUT)
                        if _goto_with_retry(detail_page, row["_detail_url"]):
                            try:
                                detail_page.wait_for_timeout(400)
                                _apply_detail(row, _parse_detail(detail_page))
                                row["_detail_fetched"] = True
                            except Exception as exc:  # one bad page ≠ dead run
                                say(f"detail parse failed "
                                    f"{row['tender_id']}: {exc}")
                        else:
                            say(f"detail unreachable {row['tender_id']} "
                                f"(will retry on a later run)")
                        time.sleep(THROTTLE_SECONDS)
                    yield row

                nxt = page.query_selector(SELECTORS["next_link"])
                if nxt is None:
                    say(f"reached the last page ({page_num})")
                    break
                if not _click_and_wait(page, nxt):
                    # One retry: a blip on the "next" click would otherwise
                    # silently truncate the run at whatever page we reached.
                    say(f"page {page_num + 1} did not load — retrying")
                    time.sleep(THROTTLE_SECONDS * 4)
                    nxt = page.query_selector(SELECTORS["next_link"])
                    if nxt is None or not _click_and_wait(page, nxt):
                        say(f"pagination stalled after page {page_num} — "
                            f"stopping early, next run will pick up the rest")
                        break
                page_num += 1
                time.sleep(THROTTLE_SECONDS)
        finally:
            browser.close()


def probe(headless: bool = True) -> None:
    """Dump the live listing so a human can re-check the markup."""
    out = Path(__file__).resolve().parent.parent / "data"
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)
        _open_listing(page, "14")
        (out / "page.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(out / "page.png"), full_page=True)
        rows = _parse_list_rows(page)
        print(f"Saved {out/'page.html'} and {out/'page.png'}")
        print(f"Parsed {len(rows)} rows from the visible page.")
        for r in rows[:3]:
            print(f"  {r['tender_id']} | {r['district'] or '?'} | "
                  f"{r['title'][:60]}")
        browser.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Scrape tntenders.gov.in (statewide, CAPTCHA-free route).")
    ap.add_argument("--probe", action="store_true",
                    help="dump live page.html + screenshot to data/")
    ap.add_argument("--show", action="store_true",
                    help="run browser headed (visible) for debugging")
    ap.add_argument("--window", default="14", choices=["today", "7", "14"],
                    help="closing-date window to walk (default 14 days)")
    ap.add_argument("--max-pages", type=int, default=1000)
    ap.add_argument("--detail", action="store_true",
                    help="also open every detail page (slow — testing only)")
    args = ap.parse_args()

    if args.probe:
        probe(headless=not args.show)
    else:
        n = 0
        want = (lambda _r: True) if args.detail else None
        for t in scrape(headless=not args.show, max_pages=args.max_pages,
                        window=args.window, needs_detail=want,
                        progress=lambda m: print(f"[{m}]")):
            n += 1
            print(f"{t['tender_id']:26} | {t['district'][:14]:14} | "
                  f"{t['est_amount_raw'][:12]:12} | {t['title'][:52]}")
        print(f"\nTotal rows parsed: {n}")
        if n == 0:
            print("0 rows — the portal layout may have changed. "
                  "Run: python3 engine/scrape.py --probe")
