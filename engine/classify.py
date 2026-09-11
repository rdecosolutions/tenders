"""
Classification engine.

Turns a raw tender (title + organisation + any description text) into:
  - department  : one of MAWS / RD&PR / DTP / ADW / HR&CE  (or UNKNOWN)
  - work_categories : list of the standard work-category tags (multi-tag)

The rules come straight from the Proposed Flow document. They are keyword
based and deliberately transparent so the app designer can tune them
without touching scraper code. Order matters only for department: an
organisation-name match is trusted before a title-keyword guess.

TUNED AGAINST LIVE DATA (09-Sep-2026, 311 real statewide tenders).
The single biggest accuracy lever turned out not to be keywords at all:
every GePNIC tender id embeds its owning organisation's code, e.g.
    2026_RDTN_700545_5  -> RDTN -> RD&PR
    2026_MAWS_695413_1  -> MAWS -> MAWS
That code is exact, so ORG_CODE_DEPT below is consulted first and the
keyword sets are now only a fallback for ids we don't recognise.

Two real bugs the live data exposed, both fixed here:
  - bare "corporation" matched Salt Corporation, Medical Services
    Corporation and Metropolitan Transport Corporation as MAWS;
  - the HR&CE title hint "ther" (Tamil for temple car) matched "other"
    and "Thermal", tagging TNEB power-station works as temple work.
Needles are now matched on word boundaries, not raw substrings.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# DEPARTMENTS
# ---------------------------------------------------------------------------
# Canonical department codes exactly as the doc specifies (HR&CE, not HRNC).
DEPARTMENTS = ["MAWS", "RD&PR", "DTP", "ADW", "HR&CE"]

# ---------------------------------------------------------------------------
# PRIMARY SIGNAL: the organisation code embedded in every GePNIC tender id
# (2026_<CODE>_<serial>_<lot>). Observed live; add to it as new codes appear.
# ---------------------------------------------------------------------------
ORG_CODE_DEPT = {
    # Municipal Administration & Water Supply and the bodies under it
    "MAWS": "MAWS",     # MAWS||<Municipality / City Municipal Corporation>
    "DMA":  "MAWS",     # Directorate Of Municipal Administration
    "CoC":  "MAWS",     # Corporation of Chennai
    "CMWSSB": "MAWS",   # Chennai Metro Water Supply and Sewerage Board
    "TWAD": "MAWS",     # TN Water Supply and Drainage Board
    # Rural Development & Panchayat Raj
    "RDTN": "RD&PR",
    "RD":   "RD&PR",
    "TNRTP": "RD&PR",
    # Town Panchayats
    "DTP":  "DTP",
    # Adi Dravidar & Tribal Welfare
    "ADW":  "ADW",
    "ADWD": "ADW",
    "TAHDCO": "ADW",
    # Hindu Religious & Charitable Endowments
    "HRCE": "HR&CE",
    "HRNC": "HR&CE",    # legacy spelling seen on some ids; canonical is HR&CE
}


def dept_from_tender_id(tender_id: str) -> str:
    """
    Read the department straight off a GePNIC tender id, or return ''.

    '2026_RDTN_700545_5' -> 'RD&PR'.  Unknown codes return '' so the caller
    falls back to the keyword rules below.
    """
    parts = (tender_id or "").split("_")
    if len(parts) < 2:
        return ""
    return ORG_CODE_DEPT.get(parts[1], "")


# Strong signals: if the tender's organisation / inviting-authority text
# contains one of these, we trust it over any title guess.
ORG_HINTS = {
    "MAWS": [
        # "corporation" alone is far too greedy — it swallowed Salt
        # Corporation, Medical Services Corporation and Metropolitan
        # Transport Corporation. Qualify it.
        "maws", "municipal administration", "municipal corporation",
        "city municipal corporation", "corporation of chennai",
        "water supply", "cmwssb", "municipality",
        "commissioner of municipal", "twad",
    ],
    "RD&PR": [
        "rural development", "panchayat raj", "block development",
        "district rural development", "drda", "village panchayat",
        "panchayat union",
    ],
    "DTP": [
        "town panchayat", "directorate of town panchayat",
    ],
    "ADW": [
        "adi dravidar", "adw", "tribal welfare", "welfare school",
        "welfare hostel",
    ],
    "HR&CE": [
        "hr&ce", "hindu religious", "charitable endowment",
        "devasthanam", "temple", "arulmigu",
    ],
}

# Weaker signals from the title itself, used only when ORG_HINTS is silent.
TITLE_DEPT_HINTS = {
    # "ther" is the Tamil temple car. As a bare substring it matched
    # "other" and "Thermal"; word-boundary matching makes it safe.
    "HR&CE": ["temple", "ther", "temple car", "arulmigu", "mandapam", "gopuram"],
    # bare "hostel" tagged a Technical Education hostel-security tender as
    # ADW, so it is qualified now.
    "ADW": ["welfare school", "welfare hostel", "adi dravidar",
            "adi dravidar welfare hostel"],
    "DTP": ["town panchayat"],
    "RD&PR": ["village road", "panchayat union", "rural"],
    "MAWS": ["ugss", "underground sewer", "municipality", "corporation"],
}

# ---------------------------------------------------------------------------
# WORK CATEGORIES  (multi-tag; a tender can match several)
# ---------------------------------------------------------------------------
# Canonical tags mirror STEP 3 "Work Category" in the doc.
WORK_CATEGORIES = {
    # Vocabulary below is tuned against 311 real statewide tenders
    # (09-Sep-2026). Portal spellings are inconsistent, so the misspellings
    # are deliberate: "submercible" and "constraction" really are what the
    # municipalities type, and they are common enough to matter.
    "Water Supply": [
        "water supply", "cwss", "infiltration well", "collection well",
        "o&m water", "hand pump", "handpump", "mini power pump",
        "overhead tank", "oht", "ohts", "water tank", "sintex", "sump",
        "borewell", "bore well", "bore-well", "tube well", "tubewell",
        "pipeline", "pipe line", "delivery pipeline", "head works",
        "water tanker", "flushing", "epl work", "gi pipe",
        "distribution line", "deepening", "community well", "eri",
    ],
    "Motor & Pump Supply": [
        "motor supply", "supply of motor", "pump", "motor rewinding",
        "rewinding", "submersible", "submercible", "pumpset", "pump set",
        "pumping", "motor", "hp motor",
    ],
    "UGSS": [
        "ugss", "ugd", "underground sewer", "underground drainage",
        "sewer well", "sewerage", "sewer", "non-clog", "non clog",
        "manhole", "man hole", "sewage treatment", "stp", "septage",
    ],
    "Drainage": [
        "drainage", "storm water", "storm-water", "strom water", "open drain",
        "side drain", "soak pit", "soakpit", "soakage pit", "culvert",
        "desilting", "de-silting", "silt removal", "gully", "main channel",
    ],
    "Roads (BT)": [
        "bt road", "bituminous", "b.t road", "black top", "blacktop",
        "wbm", "premix", "bt patch", "bt patches", "patch work",
        "pot hole", "pothole",
    ],
    "Roads (CC)": [
        "cc road", "c.c road", "cement concrete road", "concrete road",
        "cement concrete pavement",
    ],
    "Roads (General)": [
        "road work", "road works", "improvements road", "road from",
        "link road", "approach road", "street work",
    ],
    "Roads (Paver Block)": [
        "paver block", "paver-block", "interlocking", "paving block",
    ],
    "Road Furniture": [
        "speed breaker", "road marking", "guard stone", "street light",
        "streetlight", "bus shelter",
    ],
    "Biomining": [
        "biomining", "bio-mining", "bio mining", "legacy waste",
    ],
    "Solid Waste Management": [
        "solid waste", "swm", "fresh waste", "waste processing",
        "garbage", "compost", "mcf", "micro compost", "godardhaan",
        "gobardhan",
    ],
    "Sanitation & Public Health": [
        "sanitary worker", "sanitary workers", "public health",
        "domestic breeding checker", "dbc", "conservancy", "toilet",
        "public toilet", "fogging", "anti larval",
        "sanitary complex", "community sanitary", "mini csc", "csc",
        "fstp", "faecal sludge",
    ],
    "Buildings": [
        "building", "school building", "hostel building", "community hall",
        "office building", "panchayat building", "construction of building",
        "kitchen shed", "noon meal centre",
    ],
    "Civil Works": [
        "civil work", "compound wall", "renovation", "repair", "repairs",
        "rcc", "reinforced cement concrete", "construction of",
        "constraction of", "constuction of", "const of", "improvement",
        "improvements", "upgradation", "restoration", "strengthening",
        "dismantling", "dismanling", "raising", "reconstruction",
        "landscape", "sluice",
    ],
    # Municipalities put their shop and market lettings out to tender too.
    # This was the single biggest untagged cluster in MAWS — hundreds of
    # "Rental Charges for Block F Shop No 49" — and it is not construction
    # at all, so a contractor almost certainly wants to filter it OUT.
    "Shop & Property Rental": [
        "shop", "shops", "rental charges", "monthly rent", "daily market",
        "vegetable market", "lease", "licence fee", "license fee",
        "license to charge", "parking lot", "bus stand", "market stall",
        "rent for", "auction of", "e-auction", "allotment of space",
    ],
    "Street Lighting": [
        "street light", "streetlight", "street lamp", "lamp post",
        "led fitting", "high mast", "solar light",
    ],
    "Electrical Works": [
        "electrical maintenance", "electrical work", "electrical works",
        "wiring", "transformer", "air conditioner", "ac unit",
        "annual maintenance contract", "amc", "erection",
    ],
    "Consultancy & Surveys": [
        "consultancy", "consultancy services", "request for proposal", "rfp",
        "detailed project report", "dpr", "feasibility", "conduct of survey",
    ],
    "IT & Software": [
        "software", "antivirus", "anti virus", "computer", "e-governance",
        "end point security", "server", "website",
    ],

    # Recurring procurement clusters that are not "works" at all. These
    # categories are NOT in the original Proposed Flow doc — they were added
    # because together they are a big slice of real DTP/municipal tenders.
    # Rename or drop them if the flow doc's taxonomy should stay closed.
    "Supplies & Equipment": [
        "procurement of", "supply of ss", "stainless steel plates",
        "tumblers", "breakfast scheme", "stationeries", "stationery",
        "walki - talki", "walkie talkie", "cctv", "surveillance camera",
        "surveillance cameras", "gym equipment", "furniture",
    ],
    "Vehicle & Machinery Hire": [
        "hiring of", "hire of", "front end loader", "skid steer loader",
        "tipper lorry", "hmv", "jcb", "earth mover", "vehicle hire",
    ],
}


_NEEDLE_RE_CACHE: dict = {}


def _needle_re(needle: str):
    """Whole-word matcher for one keyword (cached — this runs a lot)."""
    rx = _NEEDLE_RE_CACHE.get(needle)
    if rx is None:
        rx = re.compile(r"(?<!\w)" + re.escape(needle) + r"(?!\w)")
        _NEEDLE_RE_CACHE[needle] = rx
    return rx


def _hit(text: str, needles: list[str]) -> bool:
    """
    True if any needle appears in text as a whole word.

    Substring matching was the original behaviour and it was wrong: "ther"
    fired on "other", "corporation" fired on every corporation in the state.
    """
    return any(_needle_re(n).search(text) for n in needles)


def classify_department(title: str, org: str = "", tender_id: str = "") -> str:
    """
    Return a department code, or 'UNKNOWN'.

    Order of trust: the tender id's organisation code (exact), then the
    organisation text, then the title.
    """
    by_code = dept_from_tender_id(tender_id)
    if by_code:
        return by_code

    org_l = (org or "").lower()
    for dept in DEPARTMENTS:
        if _hit(org_l, ORG_HINTS[dept]):
            return dept

    title_l = (title or "").lower()
    # Title fallback — check HR&CE/ADW/DTP first as they're most distinctive.
    for dept in ["HR&CE", "ADW", "DTP", "RD&PR", "MAWS"]:
        if _hit(title_l, TITLE_DEPT_HINTS[dept]):
            return dept
    return "UNKNOWN"


def classify_work(title: str, description: str = "") -> list[str]:
    """Return all matching work-category tags (possibly empty)."""
    text = f"{title} {description}".lower()
    tags = [cat for cat, needles in WORK_CATEGORIES.items() if _hit(text, needles)]

    # Temple-car specifics should suppress the generic 'Civil Works' noise
    # only if nothing else matched, keep behaviour simple and additive here.
    return tags


def classify(title: str, org: str = "", description: str = "",
             tender_id: str = "") -> dict:
    return {
        "department": classify_department(title, org, tender_id),
        "work_categories": classify_work(title, description),
    }


if __name__ == "__main__":
    # Quick self-check with synthetic examples (not live data).
    samples = [
        ("Construction of CC road at Kumbakonam Ward 12",
         "Commissioner, Kumbakonam Municipality"),
        ("Renovation of temple car at Arulmigu Temple",
         "HR&CE Department"),
        ("Supply and rewinding of submersible motor",
         "Block Development Officer, Panchayat Union"),
        ("Construction of welfare hostel building",
         "Adi Dravidar Welfare"),
    ]
    for title, org in samples:
        print(f"\n{title}\n  org={org}\n  ->", classify(title, org))
