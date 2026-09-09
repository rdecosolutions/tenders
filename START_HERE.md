# START HERE — building the TN Tender app with Claude Code

This is your single entry point. It tells you what to do, in order, from
opening the project to a running app. You do not need to be a developer;
Claude Code does the building, you approve steps and run a few commands.

---

## Before you begin (one time)

1. **Install Claude Code** (needs a paid Claude plan — Pro or higher).
   In Terminal:
   ```
   curl -fsSL https://claude.ai/install.sh | bash
   claude --version
   claude login
   ```

2. **Put this folder on your Desktop** (unzip it there).

3. **Open the project:**
   ```
   cd ~/Desktop/tn-tender-engine
   claude
   ```
   Claude Code auto-reads `CLAUDE.md`, so it starts with full context.

4. **Kick it off:** paste the entire contents of `CLAUDE_CODE_PROMPT.txt`
   as your first message.

That's the whole start. The rest of this file is the map of what happens
next, so you know where you are and what "done" looks like at each phase.

---

## The build in five phases

### Phase 1 — Make the scraper real  (the core technical task)
The only unfinished piece. Claude Code will:
- install Playwright + Chromium,
- run `python3 engine/scrape.py --probe` to capture the live portal,
- inspect the real page and fix the scraper's selectors,
- confirm real tenders flow into the database, correctly classified.

**Done when:** `python3 engine/main.py` stores real statewide tenders and
`python3 engine/main.py --since-hours 24` shows an alerts feed.

*This is the make-or-break phase. Everything else builds on it. If the
portal fights back (CAPTCHA, odd markup), this is where the time goes —
that's normal, not a failure.*

### Phase 2 — Prove the data is right
Before any UI, sanity-check the data with Claude Code:
- Are departments classified correctly across all five?
- Do work-category tags look right on real titles?
- Do the five event types show up over a couple of days of runs?
Refine the keyword lists in `classify.py` against what real tenders say.

**Done when:** you trust what's in the database.

### Phase 3 — Build the app front-end
Ask Claude Code to scaffold the app per the spec. It will ask you **web or
mobile** — decide based on how you want to use it (web is faster to build
and view anywhere; mobile is nicer for on-the-go alerts). The app reads the
database; it never scrapes. Screens:
- Filters: Department → District/Local Body → Work Type → Amount range →
  Closing Date
- Tender card: the ten fields from the spec
- Alerts feed: with the five badges (new / corrected / retendered /
  extended / cancelled)

**Done when:** you can open the app and filter real tenders.

### Phase 4 — Alerts delivery
Wire daily alerts to reach you: email first (simplest), push later. The
engine already records every event; this phase just delivers them.

**Done when:** you get a daily digest of new/changed tenders you care about.

### Phase 5 — Hosting so it runs without your laptop
Statewide monitoring needs a small always-on backend. Ask Claude Code to
recommend and set up the lightest option: a small VPS with a scheduled job,
or a cloud function + hosted database. Move SQLite → Postgres here if more
than one device will read it.

**Done when:** the engine runs on a schedule in the cloud and the app reads
from it, with your Mac switched off.

---

## What's in this folder
```
CLAUDE.md                 auto-loaded context for Claude Code
START_HERE.md             this file
CLAUDE_CODE_PROMPT.txt    paste this to begin
README.md                 quick technical reference
requirements.txt          Python dependencies
docs/
  TN_Tender_App_Build_Spec.docx   full data model + plan
engine/
  classify.py   dept + work-category rules   (tested)
  store.py      SQLite + 5-type detection     (tested)
  main.py       runner                        (working)
  scrape.py     portal scraper                (Phase 1 fixes this)
data/
  tenders.db    created on first run
```

## Honest expectations
This is a real app, built in phases, not a one-shot. Phases 1 and 5 (live
scraper, hosting) are where real effort and any cost land. The hardest
*logic* — classification and five-type change detection — is already built
and tested, which is the part that would otherwise eat the most time.
Go one phase at a time; don't start Phase 3 until Phase 1 truly works.

## If you get stuck
Paste the exact error back to Claude Code — it can see your machine and the
live site, so it can debug directly. For anything about the original
requirements, point it at `docs/TN_Tender_App_Build_Spec.docx`.
