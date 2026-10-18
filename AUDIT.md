# Deliverable 1 — Existing product audit

Measured 18-Oct-2026 against the actual code and data, not the summary.
Where the brief and the implementation disagree, the implementation wins.

## 1.1 What is genuinely working

| Component | Lines | State |
|---|---|---|
| `engine/scrape.py` | 786 | Working. Walks the 14-day listing backwards, retries, reports coverage |
| `engine/store.py` | 395 | Working. SQLite, 5 event types, field-level change descriptions |
| `engine/classify.py` | 351 | Working. Department 100%, work category 86% |
| `engine/export.py` | 281 | Working. NDJSON round-trip verified lossless incl. change hashes |
| `engine/main.py` | 210 | Working. Detail-fetch gating, department filter, bounded top-up |
| `engine/latest.py` | 180 | **Working but unproven** — one run so far |
| `engine/notify.py` | 342 | Built and tested end-to-end; **never switched on** |
| `site/index.html` | 1,857 | Working. Filters, Insights, 5 charts, exports |
| `app/server.py` | 243 | Local dev only; superseded by the static site |

Operationally: 39 successful nightly runs, 2 failures (both 09-Sep, during
initial debugging; none since). 35 successful deploys.

## 1.2 Partially implemented — the important finding

Three features shipped on 18-Oct are **effectively inert**, because they
only populate when a tender's detail page is re-fetched:

| Field | Coverage | Feature it powers |
|---|---|---|
| `tender_category` | **1%** | The Works/Goods/Services filter |
| `documents` | **0%** | Document list on the card |
| `bid_validity` | **0%** | "EMD locked ~90d" and the commitment curve |

**Correction (same day):** the original figure here was 57 nights, which
was wrong and wrong in my favour. It divided 11,341 tenders by 200 a night.
But the sweep only walks the 14-day closing listing, so the top-up can only
ever reach tenders that are still open — 2,751 of them, of which 2,634 lack
the field. The real figure was **13 nights**. Raising the top-up to 500 a
run brings it to about **five**, and the nightly request load to roughly
1,000 over ninety minutes, around 11 a minute, which stays inside polite.

Until coverage is adequate the contract-type filter disables itself and
says "filling in — 4% of open tenders so far", rather than appearing broken
by silently returning nothing.

**This is the single most important thing to fix before showing anyone.**
Options: raise the top-up limit (portal politeness is the constraint),
prioritise open tenders only (~2,700, so ~14 nights at the current rate),
or accept a slow fill and hide the filters until coverage is adequate.

## 1.3 Data quality, measured

| | |
|---|---|
| Tenders | 11,341 |
| District set | 96% |
| Work category set | 86% |
| Published value | 78% (plus ~2,100 recovered from EMD) |
| EMD present | 99% |
| Work description | 80% |

ADW returns **zero tenders**. A 45-page sweep over 331 live tenders found
no Adi Dravidar or welfare organisation. The brief lists five target
departments; there are four. The classifier handles ADW codes if any ever
appear.

## 1.4 Technical limitations

1. **Discovery lag unproven.** Historic median 3.8 days. The 30-minute
   poller has run once. No evidence yet it closed the gap.
2. **Award data unavailable.** Verified today: district websites publish
   nothing (Cuddalore's tender section is empty), `tenders.tn.gov.in` does
   not respond, and the portal's own AOC data sits behind a CAPTCHA on
   `WebTenderStatusLists`. Legally public under the TN Transparency in
   Tenders Act 1998, practically inaccessible by scraping.
3. **Documents not stored.** Fetchable (4.5 MB NIT PDF, HTTP 200, no
   CAPTCHA) but links are session-bound and the corpus is ~3.5 GB.
4. **14-day visibility window.** 58% of tenders allow longer bid windows,
   so the listing route alone sees them late.
5. **No accounts.** Static site. Cannot deliver per-user anything.

## 1.5 Operational risks

**Payload growth.** `site/data.json` is now 12 MB raw / **1,050 KB
gzipped**, up from ~300 KB this morning — the new per-tender fields roughly
quadrupled it. Every visitor downloads and parses this. On a mid-range
Android phone on 4G this is already noticeable and it grows daily. The
archive split helps closed tenders; it does not help field growth.

**Repo growth.** `.git` is 37 MB, `data/tenders.ndjson` 16 MB. Fine now,
but every nightly commit adds a diff forever.

**Test coverage is narrow.** Two tests run in CI:
- `test_no_phantoms.py` — asserts an unchanged tender raises no event
- `check_layout.py` — asserts the page structure is not broken

Nothing tests filter behaviour. Three UI bugs reached production today and
were found by the user, not by CI: the town filter matched only exact
strings, the results column was pushed out of the grid at desktop width,
and the clear buttons had no handler at all.

**Scheduled-run fragility.** GitHub cron is best-effort. The nightly job
has drifted between 21:14 and 23:49 UTC. The 30-minute poller has fired
once since being added several hours ago.

## 1.6 Where the brief and the code differ

| Brief says | Actually |
|---|---|
| Five departments incl. ADW | Four. ADW is zero |
| "Tender classification by district, department, work type and category" | True, but *contract* category is 1% populated |
| "Analytics dashboard with multiple insights" | True — 12 panels, 5 charts |
| "Retender detection" | True, 178 found — but framing corrected: it indicates *investigate*, not *low competition* |
| "EMD-based estimated tender values" | True, back-tested: median error 0.0%, 88% within 20% |
