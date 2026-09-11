"""
Send a Web Push notification summarising what changed on the last run.

Runs at the end of the nightly workflow. Reads the events the engine just
recorded and pushes one short digest to each subscribed device.

WHY IT LOOKS LIKE THIS
  GitHub Pages is static, so there is no server to hold subscriptions.
  They live in the PUSH_SUBSCRIPTIONS repository secret as a JSON array —
  one entry per device, pasted by hand. That is fine for a handful of
  devices and keeps the whole thing free; it is the part to replace with a
  small hosted endpoint if this ever needs to scale past a few people.

  Subscriptions are NOT committed to the repo. A push endpoint is a
  capability URL: anyone who can read it can send notifications to that
  device, and this repository is public.

Two channels, either or both. Whichever is configured gets used.

  ntfy (easy)  — install the ntfy app, subscribe to a topic, set one secret.
                 Nothing to store per device, nothing that expires.
  Web Push     — native browser notifications, no extra app, but each
                 device's subscription has to be pasted into a secret and
                 re-pasted whenever the browser rotates it.

Environment:
  NTFY_TOPIC           ntfy topic name (repository secret) — the topic name
                       IS the password, so it must be long and random
  NTFY_SERVER          defaults to https://ntfy.sh
  VAPID_PRIVATE_KEY    the PEM private key (repository secret)
  VAPID_SUBJECT        mailto: address for the push service to complain to
  PUSH_SUBSCRIPTIONS   JSON array of PushSubscription objects
  SITE_URL             where a tapped notification should open

Exit code is always 0: a failed notification must never fail the scrape.
The data is already committed by the time this runs.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from store import connect

SITE_URL = os.environ.get("SITE_URL", "https://tenders.rdecosolutions.org/")
LOOKBACK_HOURS = int(os.environ.get("NOTIFY_HOURS", "20"))
CONFIG_PATH = (__import__("pathlib").Path(__file__).resolve().parent.parent
               / "notify_config.json")


def load_config() -> dict:
    """
    Read notify_config.json — which changes are worth interrupting someone
    for. Kept as a plain file in the repo, not a secret, so it can be edited
    in GitHub's web editor without touching code.
    """
    defaults = {"departments": [], "districts": [], "work_categories": [],
                "local_bodies": [], "min_amount": 0, "event_types": [],
                "style": "digest", "max_per_tender": 5,
                "closing_soon_days": 3, "exclude_work_categories": []}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return defaults
    return {k: raw.get(k, v) for k, v in defaults.items()}


def _amount(raw: str) -> float:
    digits = "".join(c for c in (raw or "") if c.isdigit() or c == ".")
    try:
        return float(digits) if digits else 0.0
    except ValueError:
        return 0.0


def matches(row, cfg: dict) -> bool:
    """True if this change is one the config asks to be told about."""
    def ok(field, key):
        wanted = cfg.get(key) or []
        return not wanted or (row[field] or "") in wanted

    if cfg.get("event_types") and row["event_type"] not in cfg["event_types"]:
        return False
    if not (ok("department", "departments") and ok("district", "districts")
            and ok("local_body", "local_bodies")):
        return False
    tags = set(t for t in (row["work_categories"] or "").split(",") if t)
    wanted_work = cfg.get("work_categories") or []
    if wanted_work and not tags & set(wanted_work):
        return False
    # Opt-out list: the quickest way to silence a whole class of tender you
    # never bid on, e.g. shop rentals, without listing everything you do want.
    excluded = set(cfg.get("exclude_work_categories") or [])
    if excluded and tags and tags <= excluded:
        return False
    if _amount(row["est_amount_raw"]) < float(cfg.get("min_amount") or 0):
        return False
    return True

# Wording matches the badges on the alerts screen.
LABEL = {"NEW": "new", "CORRIGENDUM": "corrected", "RETENDER": "retendered",
         "DATE_EXTENSION": "extended", "CANCELLATION": "cancelled"}


def kinds_is_backfill(rows) -> bool:
    """A burst this large is the engine catching up, not the portal moving."""
    return sum(1 for r in rows if r["event_type"] == "NEW") >= 300


def recent_matching(conn, cfg: dict) -> list:
    since = (datetime.now(timezone.utc)
             - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    rows = conn.execute(
        "SELECT e.event_type, e.detail, t.department, t.district, "
        "t.local_body, t.title, t.est_amount_raw, t.work_categories, "
        "t.closing_date "
        "FROM events e JOIN tenders t ON t.tender_id = e.tender_id "
        "WHERE e.created_at >= ? ORDER BY e.id DESC", (since,)).fetchall()
    return [r for r in rows if matches(r, cfg)]


def closing_soon(conn, cfg: dict) -> list:
    """
    Tenders matching the config that close within the next few days.

    These raise no event -- nothing about them changed -- so nothing else
    would ever mention them. Missing a deadline costs more than missing a
    listing, which makes this the alert most worth having.
    """
    days = int(cfg.get("closing_soon_days") or 0)
    if days <= 0:
        return []
    now = datetime.now()
    horizon = now + timedelta(days=days)
    out = []
    for r in conn.execute(
            "SELECT '' AS event_type, '' AS detail, department, district, "
            "local_body, title, est_amount_raw, work_categories, closing_date "
            "FROM tenders WHERE status != 'cancelled'"):
        d = _parse_closing(r["closing_date"])
        if d and now <= d <= horizon and matches(r, dict(cfg, event_types=[])):
            out.append((d, r))
    out.sort(key=lambda pair: pair[0])
    return [r for _d, r in out]


def _parse_closing(raw: str):
    """Portal dates look like '15-Sep-2026 11:00 AM'."""
    for fmt in ("%d-%b-%Y %I:%M %p", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime((raw or "").strip(), fmt)
        except ValueError:
            continue
    return None


def build_digest(conn, cfg: dict | None = None) -> tuple[str, str] | None:
    """Summarise recent matching events, or None if there is nothing."""
    cfg = cfg or load_config()
    rows = recent_matching(conn, cfg)
    soon = closing_soon(conn, cfg)
    if not rows and not soon:
        return None
    if not rows:
        # Nothing changed, but deadlines are coming.
        d = int(cfg.get("closing_soon_days") or 3)
        head = soon[0]
        return (f"{len(soon)} closing within {d} days",
                f"Next: {(head['title'] or '')[:70]} "
                f"\u2014 {head['closing_date']}")

    if cfg.get("style") == "per_tender":
        top = rows[: int(cfg.get("max_per_tender") or 5)]
        lines = []
        for r in top:
            amt = f" \u20b9{r['est_amount_raw']}" if r["est_amount_raw"] else ""
            lines.append(f"\u2022 {(r['title'] or '')[:70]}{amt}"
                         f" \u2014 {r['district'] or '?'}")
        more = len(rows) - len(top)
        body = "\n".join(lines) + (f"\n+{more} more" if more > 0 else "")
        return (f"{len(rows)} matching tender{'s' if len(rows) != 1 else ''}",
                body)

    # A re-scrape from an empty database would otherwise push "2,400 tender
    # updates", which is noise, not news.
    if kinds_is_backfill(rows):
        return ("Initial catch-up",
                f"{len(rows)} tenders loaded. Normal alerts resume tomorrow.")

    kinds = Counter(r["event_type"] for r in rows)
    bits = [f"{n} {LABEL.get(k, k.lower())}" for k, n in kinds.most_common()]
    title = f"{len(rows)} tender update{'s' if len(rows) != 1 else ''}"

    places = Counter(r["district"] for r in rows if r["district"])
    body = ", ".join(bits)
    if places:
        top = ", ".join(f"{d} ({n})" for d, n in places.most_common(3))
        body += f"\n{top}"
    if soon:
        d = int(cfg.get("closing_soon_days") or 3)
        body += f"\n\u23f0 {len(soon)} closing within {d} days"
    return title, body


def send_ntfy(title: str, body: str) -> bool:
    """
    Post the digest to an ntfy topic. Stdlib only — no dependency to install.

    The topic name is the only credential, so anyone who learns it can read
    these alerts or send junk to the phone. That is the trade for needing no
    account; keep the name long and random.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{server}/{topic}", data=body.encode("utf-8"), method="POST",
        headers={
            "Title": title,
            "Tags": "page_facing_up",
            "Click": SITE_URL,
        })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(f"ntfy: sent to {server}/***  (HTTP {resp.status})")
        return True
    except urllib.error.URLError as exc:
        print(f"ntfy: failed ({exc}) — continuing")
        return False


def report(lines: list[str]) -> None:
    """
    Write a short status to the GitHub run summary.

    The job log needs admin rights to read; the run summary does not. When
    a notification does not arrive, this is the page that says why.
    """
    print("\n".join(lines))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main() -> int:
    test_mode = "--test" in sys.argv
    subs_raw = os.environ.get("PUSH_SUBSCRIPTIONS", "").strip()
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    have_webpush = bool(subs_raw and key)

    has_ntfy = bool(os.environ.get("NTFY_TOPIC", "").strip())
    if not have_webpush and not has_ntfy:
        report([
            "## Notifications: nothing configured",
            "",
            "- `PUSH_SUBSCRIPTIONS` secret set: **"
            f"{'yes' if subs_raw else 'NO'}**",
            f"- `VAPID_PRIVATE_KEY` secret set: **{'yes' if key else 'NO'}**",
            "",
            "Add the missing secret under Settings -> Secrets and variables "
            "-> Actions. The subscription blob comes from the "
            "**Enable notifications** button on the site.",
        ])
        return 0

    if test_mode:
        title = "Test notification"
        body = ("If you can read this, notifications are working. "
                "The real ones arrive after each nightly scrape.")
    else:
        # Build the digest once and reuse it for every channel.
        conn = connect()
        digest = build_digest(conn)
        conn.close()
        if digest is None:
            report([f"## Notifications: nothing to send",
                    "",
                    f"No tender changed in the last {LOOKBACK_HOURS}h, so no "
                    f"notification was sent. This is normal on a quiet day."])
            return 0
        title, body = digest

    send_ntfy(title, body)

    if not have_webpush:
        return 0

    try:
        subs = json.loads(subs_raw)
    except json.JSONDecodeError as exc:
        print(f"PUSH_SUBSCRIPTIONS is not valid JSON ({exc}). Skipping.")
        return 0
    if isinstance(subs, dict):          # a single device, pasted directly
        subs = [subs]

    from pywebpush import WebPushException, webpush
    payload = json.dumps({"title": title, "body": body, "url": SITE_URL})
    subject = os.environ.get("VAPID_SUBJECT", "mailto:kim@piperocket.digital")

    sent = 0
    problems: list[str] = []
    for i, sub in enumerate(subs, 1):
        try:
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key="vapid_private_key.pem",
                    vapid_claims={"sub": subject})
            sent += 1
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            # 404/410 mean the browser threw the subscription away. It will
            # never work again — that device must re-subscribe on the site.
            if code in (404, 410):
                problems.append(
                    f"device {i}: subscription expired (HTTP {code}) — open "
                    f"the site on it and press **Enable notifications** "
                    f"again, then replace its blob in the secret")
            else:
                problems.append(f"device {i}: {exc}")

    lines = [f"## Notifications: {sent} of {len(subs)} device(s) reached", "",
             f'Sent: "**{title}** — {body}"'.replace("\n", " / "), ""]
    if problems:
        lines += ["Problems:", ""] + [f"- {p}" for p in problems] + [""]
    if sent and not problems:
        lines += ["If nothing appeared on the phone, the push left GitHub "
                  "successfully — so the problem is on the device: check "
                  "Android Settings -> Apps -> Chrome -> Notifications is on, "
                  "and that the site is not muted in Chrome."]
    report(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
