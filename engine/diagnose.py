"""
Explain what the portal actually served us.

Runs only when a scrape fails. Writes a short Markdown report that GitHub
shows on the run summary page, which is readable without signing in —
unlike the job log. The whole point is to answer one question quickly:
did the portal refuse us, or did our parsing break?
"""

from __future__ import annotations

import sys

from scrape import LIST_URL, SELECTORS, USER_AGENT, _parse_list_rows


def main() -> int:
    from playwright.sync_api import sync_playwright

    lines = ["## Scrape diagnosis", ""]
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_context(user_agent=USER_AGENT).new_page()
            pg.set_default_timeout(45_000)
            pg.goto(LIST_URL, wait_until="domcontentloaded")
            pg.wait_for_timeout(3000)

            html = pg.content()
            body = " ".join(pg.inner_text("body").split())
            rows = _parse_list_rows(pg)
            tab = pg.query_selector(SELECTORS["tab_14day"])

            lines += [
                f"- reached the page: **yes** (HTTP-level)",
                f"- page title: `{pg.title()}`",
                f"- CAPTCHA on the page: **{'YES' if 'captchaImage' in html else 'no'}**",
                f"- results table present: **{'yes' if pg.query_selector('table#table') else 'NO'}**",
                f"- rows parsed on the landing tab: **{len(rows)}**",
                f"- '14 day' tab found: **{'yes' if tab else 'NO'}**",
                "",
                "First 700 characters the portal returned:",
                "",
                "```",
                body[:700],
                "```",
            ]
            b.close()
    except Exception as exc:
        lines += [f"- could not even load the page: `{type(exc).__name__}: {exc}`"]

    lines += [
        "",
        "**Reading this:** a CAPTCHA or zero rows here, when the same code "
        "returns hundreds of rows from a home connection, means the portal "
        "is refusing GitHub's datacenter IPs. That is a hosting problem, not "
        "a code problem — the fix is to run the engine from a normal IP "
        "(see `deploy/`).",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
