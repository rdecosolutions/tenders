# Validation brief — please challenge this plan

I asked an AI coding assistant (Claude) to audit my Tamil Nadu tender
platform against a product brief you helped me write. It disagreed with
parts of the brief and proposed a different sequence. I want a second
opinion on whether it is right.

**What I need from you:** challenge the reasoning below. Where it is wrong,
say so and say why. Where the evidence is thin, say what would settle it.
Do not simply agree.

---

## 1. The product, factually

A working tender monitoring platform for Tamil Nadu, live at
tenders.rdecosolutions.org since 9 September 2026. Python + Playwright
scraper on GitHub Actions, static site on GitHub Pages, the git repository
itself used as the database (NDJSON). No backend, no accounts, ₹0 running
cost.

Measured 18 October 2026:

- 11,341 tenders, 12,796 change events, all 38 districts
- Departments: MAWS 5,260 · RD&PR 5,053 · DTP 727 · HR&CE 301 · **ADW 0**
- District coverage 96%, work-category coverage 86%, EMD present 99%
- 39 successful nightly runs, 2 failures (both on day one)

Analysis features that competitors do not have, because they need
accumulated history the source portal deletes:

- Price benchmarks by work type × district (~300 cells with 5+ tenders)
- 178 retenders detected (130 re-published at an identical price)
- Notice period per body, across 206 bodies (ranges 8 to 26 days)
- Deadline-slip rates per body (one body moves 75% of its deadlines)
- ~2,100 tender values recovered from EMD (EMD is 1% of value; back-tested
  on 8,298 tenders where both are known: median error 0.0%, 88% within 20%)
- Deposit capital planner (EMD is locked for bid validity, 30–90 days)
- A scored shortlist, "Worth a look today", with transparent reasons

---

## 2. What the audit found that the brief did not know

**(a) Three features shipped on 18 Oct are inert.** Contract type is 1%
populated, document lists 0%, bid validity 0%. They fill only when a
tender's detail page is re-fetched, at 200 per night against 11,341
tenders — about 57 nights. Shown to a contractor today, the
Works/Goods/Services filter returns almost nothing and looks broken.

**(b) The site payload quadrupled today**, from roughly 300 KB to 1,050 KB
gzipped, because each new per-tender field ships to every visitor. Mobile
load is the concern; it grows daily.

**(c) ADW has zero tenders.** A 45-page sweep over 331 live tenders found
no Adi Dravidar organisation at all. The brief treats five departments as
active; there are four.

**(d) The 30-minute poller is unproven.** It was built to fix a measured
median discovery lag of 3.8 days (only 17% of tenders seen on the day they
were published, while 58% of tenders allow bid windows longer than the
14-day visibility route). It has run once. No evidence yet either way.

**(e) Award data was investigated and is unavailable.** You suggested
district tender bulletins, which was a good idea and legally correct — the
Tamil Nadu Transparency in Tenders Act 1998 requires weekly publication of
"intimations of acceptance of tenders" above ₹5 lakh. Tested:

- District websites (Cuddalore, Thanjavur, Coimbatore): tender sections
  exist but are empty
- tenders.tn.gov.in: resolves to a live host, does not respond
- The portal's own `WebTenderStatusLists` has an "AOC" (Award of Contract)
  filter, confirming the data exists — behind a CAPTCHA, which we will not
  bypass

Conclusion offered: legally public, practically inaccessible by scraping.
A direct written request to a District Tender Bulletin Officer might work.

---

## 3. The recommendation I am asking you to challenge

Your brief listed 14 features across four phases, and separately advised
validating with 5 contractors and 3 consultants first. Claude argued these
contradict each other, and proposed three releases instead:

**R1 — Make what exists trustworthy (1 week, no new infrastructure)**
Backfill the three inert fields, shrink the payload, switch on Web Push
(already built and tested, never enabled), and measure whether the poller
closed the discovery lag.

**R2 — Service-assisted pilot (2 weeks, no code)**
Three to five contractors. I curate a daily digest per client from saved
filter URLs. Success = each tells me which tenders were useful and why, and
at least one asks for something that does not exist.

**R3 — Build whatever R2 proves (scope decided after R2, deliberately not
specified now)**

The core argument: twelve insight panels and five charts exist, but nothing
reaches a user who is not already visiting the website, and no contractor
has ever used it. Building profiles, matching engines and AI summaries
before that is guessing.

---

## 4. Specific questions

1. Is "make it trustworthy, then pilot, then build" right — or is it
   over-cautious for a bootstrapped solo founder who needs revenue?
2. Is a service-assisted pilot genuinely a substitute for building
   accounts, or does it just delay the inevitable and burn two weeks?
3. Is the ICP right? It assumes small civil contractors. Would tender
   consultants, or suppliers, or infrastructure finance, pay more for the
   historical analysis and care less about alerting?
4. Is depth in one state against all-India aggregators (BidAssist: 900k
   users, 1.5 crore tenders, SMS/WhatsApp alerts, full documents, award
   results) a defensible wedge, or a ceiling?
5. Pricing floated at ₹999 / ₹2,999 / ₹7,999 per month. Plausible for this
   ICP in Tamil Nadu, or wrong by an order of magnitude?
6. Is there a legitimate route to award data that has not been tried?
   Note that scraping, CAPTCHA bypass and credentialed portals are out of
   scope; RTI requests and direct approaches to Bulletin Officers are not.
7. What would make a contractor pay, that is not award data?

## 5. Where Claude may be biased

It built most of this, so treat its judgement of what is "already good
enough" with suspicion. It has also repeatedly argued that notifications
matter more than features — which is plausible, but it is also the cheapest
thing to recommend, since it is already built.
