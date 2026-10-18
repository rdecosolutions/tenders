# Deliverable 2 — Feature gap analysis

Complexity is in days of work. Customer value is judged against the ICP in
the brief: a small TN civil contractor bidding ₹2–50 lakh in one to three
districts. "Validated" means a real contractor has asked for it; nothing
here is validated yet, which is itself the finding.

## Must fix now (before anyone sees it)

| Feature | Status | Value | Complexity | Dependencies |
|---|---|---|---|---|
| Backfill `tender_category` / documents / bid validity | 1% / 0% / 0% | High — three shipped features are inert and look broken | 1 d | Portal politeness limits |
| Switch on Web Push | Built, tested, off | **Highest** — everything else reaches nobody | 15 min of Kim's time | A repo secret |
| Verify the 30-min poller | 1 run | High — it is the core product claim | 0 d, needs 3 days to elapse | Time |
| Cut the site payload | 1,050 KB gz | High — mobile load, growing daily | 1 d | None |

## Build after validation (3+ contractors using it)

| Feature | Status | Value | Complexity | Dependencies |
|---|---|---|---|---|
| Company profile + saved preferences | Not built | High — removes re-filtering every visit | 3 d | Backend, auth |
| Personalised daily digest | Engine exists, no per-user routing | High | 2 d | Profiles |
| Tender shortlist with statuses | Saved tenders exist (browser-local) | Medium | 2 d | Backend |
| Matching score per profile | Generic version shipped ("Worth a look") | Medium — already 80% there | 1 d | Profiles |
| WhatsApp / email delivery | Not built | Medium — email free via Actions; WhatsApp needs a paid gateway | 1–3 d | Profiles, BSP approval |

## Future roadmap (needs infrastructure first)

| Feature | Status | Value | Complexity | Dependencies |
|---|---|---|---|---|
| Document download + storage | Metadata only | High — every competitor has it | 4 d | VPS/object storage, ~3.5 GB |
| AI tender summary | Not built | High **if** documents exist | 4 d | Documents, LLM budget |
| Eligibility checker | Not built | High for the ICP | 3 d | Documents + profile |
| Preparation checklist | Not built | Medium | 2 d | Document extraction |
| Bid tracking workspace | Not built | Medium — ICP has 1–2 admin staff | 4 d | Backend, auth |

## Not worth building yet

| Feature | Why not |
|---|---|
| Award data pipeline | Verified unavailable: district bulletins empty, state portal down, AOC behind CAPTCHA. Do not build a pipeline for data you cannot obtain |
| Seasonality analytics | Needs a year; ~3 real months exist. Improves by waiting, not building |
| Multi-tenant SaaS billing | Zero paying customers. Premature |
| Native mobile app | PWA already installs. Solves nothing the web app does not |
| Pre-bid meeting tracking | Measured: 0 of 10 sampled tenders populate it |
| Tenderer-class filter | Only ~30% carry a real class; rest are "OTHERS" |

## Recommended next three releases

**R1 — Make what exists trustworthy (1 week, no new infrastructure)**
Backfill the three inert fields; shrink the payload; switch on push;
measure the poller. *Acceptance:* contract-type filter covers >80% of open
tenders; payload under 400 KB gzipped; a notification arrives on a phone;
measured discovery lag published.

**R2 — Service-assisted pilot (2 weeks, no code)**
Three to five contractors. Kim curates a daily digest per client from
saved filter URLs. *Acceptance:* each says which tenders were useful and
why; at least one asks for something we have not built.

**R3 — Build whatever R2 proved (scope decided by R2, not now)**
Most likely profiles + per-user alerts, which forces the backend decision.
Writing the spec before R2 is guessing.

## The honest summary

The product is further along than the brief implies on analysis, and
further behind on delivery. Twelve insight panels and five charts exist;
nothing reaches a user who is not already looking at the site, and three
features shipped today operate on 1% of the data.

Nothing in Phases 1–3 of the brief should start until three contractors
have used what exists for a fortnight.
