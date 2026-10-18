# TN Tender Monitoring — project brief

A working product that monitors Tamil Nadu government tenders, classifies
them, and surfaces analysis the source portal cannot provide. Live at
**tenders.rdecosolutions.org**. Running unattended since 09-Sep-2026.

Written to be shared for a second opinion. Facts here are measured, not
estimated; where something is uncertain it says so.

---

## 1. What it does

Scrapes `tntenders.gov.in` (GePNIC, the NIC e-procurement system used by
most Indian states), classifies each tender by department and type of work,
keeps full history, detects changes, and publishes a filterable web app
with an analytics dashboard.

**Scope:** Tamil Nadu statewide, five departments — MAWS (Municipal
Administration & Water Supply), RD&PR (Rural Development & Panchayat Raj),
DTP (Town Panchayats), ADW (Adi Dravidar Welfare), HR&CE (Hindu Religious &
Charitable Endowments).

**Target user:** a small-to-mid Tamil Nadu civil contractor bidding on
municipal and panchayat works — water supply, borewells, roads, drainage,
buildings — typically ₹2–50 lakh, operating in one to three districts.

---

## 2. Current scale (measured 18-Oct-2026)

| | |
|---|---|
| Tenders held | 11,341 |
| Change events recorded | 12,796 |
| Districts covered | 38 of 38 |
| By department | MAWS 5,260 · RD&PR 5,053 · DTP 727 · HR&CE 301 · ADW 0 |
| Events | 11,338 NEW · 804 corrigenda · 652 date extensions · 2 retenders |
| District coverage | 96% |
| Work-category coverage | 86% |
| Nightly runs | 39 successful, 2 failed |

ADW is zero. A 45-page sweep across 331 live tenders found no Adi Dravidar
or welfare organisation at all. The classifier handles ADW org codes if any
appear; the portal simply carries none.

---

## 3. Architecture

Free, entirely on GitHub, no server:

- **GitHub Actions** runs the scraper nightly (21:00 UTC / 02:30 IST), full
  sweep, 60–160 minutes.
- **A second workflow polls every 30 minutes** for newly published tenders.
- **The repository is the database.** Actions runners are ephemeral and
  change detection needs yesterday's state, so both tables are dumped to
  sorted NDJSON and committed each run. NDJSON rather than the SQLite file
  because git diffs text cheaply; committing a ~2 MB binary nightly would
  bloat the repo within months.
- **GitHub Pages** serves a static site: one HTML file that loads a ~300 KB
  gzipped JSON and filters in the browser. No backend, no accounts.
- Python 3 + Playwright. No front-end framework or chart library.

**Why free hosting:** Pages on a private repo needs a paid plan, and at
that price a VPS is better value. The repo is therefore public. A complete
VPS deployment kit exists in `deploy/` if that trade changes.

---

## 4. The central constraint: CAPTCHA

As of Sep 2026 the portal CAPTCHA-gates nearly every browse route — Active
Tenders, by Location, by Organisation, Advanced Search, Corrigendum,
Cancelled/Retendered, Tenders Status, and "Closing by Date".

**We do not solve CAPTCHAs.** Three routes remain open and are what the
scraper uses:

1. Tenders by Closing Date → Closing Today / 7 days / **14 days**
2. Individual tender detail pages
3. The homepage, which lists the newest tenders

**Consequence:** the 14-day route makes a tender visible when its *closing
date* approaches, not when it is published. Measured across 11,297
tenders, that made us a median **3.8 days late**, with only 17% seen on the
day of publication — while 58% of tenders allow a bid window longer than 14
days. The 30-minute homepage poller was built to close this; it has been
running too briefly to re-measure, which is the single most important
outstanding verification.

---

## 5. What makes it different from competitors

Competitors (BidAssist — 900k users, 1.5 crore tenders, all-India plus 195
countries; TenderTiger; Tender247; ContraVault AI) all beat it on breadth,
documents, results data, mobile apps and accounts.

What they do not have, because the portal deletes closed tenders and only
accumulated history produces it:

- **Price benchmarks** by work type × district, ~300 cells with 5+ priced
  tenders behind them. Every tender card shows its position, e.g. "priced
  116% above typical for Civil Works in Cuddalore".
- **Retender detection** — 178 cases where a body re-published the same
  work after 21+ days, 130 at the identical price. Strong thin-competition
  signal; the only proxy available without award data.
- **Notice period per body** — median days from publication to closing,
  across 206 bodies with enough history. Ranges 8 to 26 days.
- **Deadline-slip rates** — Pattukottai moves 75% of its closing dates.
- **Recovered values** — the portal publishes no value for ~2,100 tenders
  but does publish the EMD, which is set at 1% of value. Back-tested on
  8,298 tenders where both are known: median error 0.0%, 88% within 20%.
- **Deposit capital planning** — EMD is locked for the bid validity period
  (30–90 days), not until closing. Nobody totals this.
- **Deep TN municipal taxonomy** — UGSS, biomining, temple car renovation,
  motor rewinding, seemai karuvelam clearing. Generic aggregators classify
  this as "Civil Works".

---

## 6. What it cannot do

- **Award data — who won, at what price.** Permanently out of reach. The TN
  Results of Tenders page is CAPTCHA'd; CPPP is CAPTCHA'd; the GePNIC MIS
  Reports portal requires registered credentials, not merely a CAPTCHA.
  This is the single biggest competitive gap and no amount of work closes
  it through legitimate means.
- **Tender documents.** Fetchable (verified: a 4.5 MB NIT PDF, HTTP 200, no
  CAPTCHA) but the links are session-bound and mirroring them is ~3.5 GB
  across open tenders. Needs real hosting. Currently only the document
  list — names, types, sizes — is captured.
- **Accounts / per-user alerts.** A static site cannot do this. It is the
  structural blocker on selling seats.
- **Seasonality.** Needs a year of history; about three real months exist.

---

## 7. Features built

**Discovery:** search across every visible field, filters for department,
district, town (type-ahead, narrows to the district), work type, the
portal's own Works/Goods/Services split, open-vs-invitation-only, value
bands, closing window, published window, saved, and "tendered before".
Shareable URLs, CSV export, calendar (.ics) export with day-before alarms.

**Insights dashboard**, all respecting active filters:
- "Worth a look today" — eight tenders scored on competition, pricing,
  time to prepare and deposit size, each showing its reasons
- Open now, with a 14-day deadline-load chart
- Typical cost by work type, with a value-distribution chart
- Who buys most / where the money is (clickable bars that apply filters)
- Deadline reliability per body
- Deposit requirements and a capital planner with a commitment curve
- Notice-period distribution
- Work that came back (retenders)
- What the current filter excludes
- Publishing rhythm

**Engine:** five change types (new, corrigendum, retender, date extension,
cancellation), with corrigenda naming the field that moved.

---

## 8. Bugs found and fixed, worth knowing

Four bugs existed in modules documented as "tested and working". All only
manifested across separate runs on live data, which is why in-process
self-tests passed:

1. Change detection used Python's `hash()`, which is salted per process —
   every tender raised a phantom corrigendum on every run.
2. `upsert()` inserted every scraper key and crashed on unknown columns.
3. `work_categories` (a derived field) sat in the change hash, so improving
   the keyword lists fired fake "corrected" alerts for every re-tagged
   tender.
4. Detail-only fields were restored from storage only when blank, but the
   listing always reports status="active" — so a tender flipped
   corrigendum→active nightly and alerted every time.

A regression test now runs in CI before every scrape and asserts that an
unchanged tender raises nothing.

Separately: the nightly sweep was silently covering about a fifth of the
window and reporting success. The listing is sorted by closing date, so
newly published tenders sit on the last pages — walking forwards meant the
pages never reached were exactly the new publications. The sweep now walks
backwards and reports coverage.

---

## 9. Honest commercial position

The scraping is commodity. The accumulated history is not — 178 retenders,
notice periods for 206 bodies, benchmarks across 300 cells, none of which
exist for someone who starts scraping tomorrow.

But three things are missing to sell it:

1. **No accounts.** One static page, same view for everyone. Cannot sell
   seats or deliver per-client alerts.
2. **No proof it works.** No contractor has used it for a month.
3. **Notifications are built and switched off.** Web Push is implemented
   and tested end-to-end; the subscription secret was never added. So a
   30-minute head start reaches nobody unless they open the site.

A service model — one person curating per-client digests — would validate
demand without building accounts, and needs no new product work.

---

## 10. Useful questions for a second opinion

- Is there a legitimate route to award/results data we have missed?
- Is depth in one state a defensible wedge against all-India aggregators,
  or a ceiling?
- Service-first or build accounts first?
- Is the ICP right? Should it be contractors, or suppliers/consultants who
  would pay more for the market analysis?
- What would make a contractor pay, that is not award data?
