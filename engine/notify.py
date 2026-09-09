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

# Wording matches the badges on the alerts screen.
LABEL = {"NEW": "new", "CORRIGENDUM": "corrected", "RETENDER": "retendered",
         "DATE_EXTENSION": "extended", "CANCELLATION": "cancelled"}


def build_digest(conn) -> tuple[str, str] | None:
    """Summarise recent events, or None if there is nothing worth sending."""
    since = (datetime.now(timezone.utc)
             - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    rows = conn.execute(
        "SELECT e.event_type, t.department, t.district, t.title "
        "FROM events e JOIN tenders t ON t.tender_id = e.tender_id "
        "WHERE e.created_at >= ?", (since,)).fetchall()
    if not rows:
        return None

    kinds = Counter(r["event_type"] for r in rows)
    bits = [f"{n} {LABEL.get(k, k.lower())}" for k, n in kinds.most_common()]
    title = f"{len(rows)} tender update{'s' if len(rows) != 1 else ''}"

    places = Counter(r["district"] for r in rows if r["district"])
    body = ", ".join(bits)
    if places:
        top = ", ".join(f"{d} ({n})" for d, n in places.most_common(3))
        body += f"\n{top}"
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


def main() -> int:
    subs_raw = os.environ.get("PUSH_SUBSCRIPTIONS", "").strip()
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    have_webpush = bool(subs_raw and key)

    if not have_webpush and not os.environ.get("NTFY_TOPIC", "").strip():
        print("No notification channel configured — skipping.")
        return 0

    # Build the digest once and reuse it for every channel.
    conn = connect()
    digest = build_digest(conn)
    conn.close()
    if digest is None:
        print(f"Nothing changed in the last {LOOKBACK_HOURS}h — no push sent.")
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

    sent = failed = 0
    for sub in subs:
        try:
            webpush(subscription_info=sub, data=payload,
                    vapid_private_key="vapid_private_key.pem",
                    vapid_claims={"sub": subject})
            sent += 1
        except WebPushException as exc:
            failed += 1
            code = getattr(exc.response, "status_code", None)
            # 404/410 mean the browser threw the subscription away. It will
            # never work again — that device must re-subscribe on the site.
            if code in (404, 410):
                print(f"  subscription expired (HTTP {code}) — that device "
                      f"needs to press Enable notifications again")
            else:
                print(f"  push failed: {exc}")
    print(f"Push: {sent} sent, {failed} failed. \"{title} — {body}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
