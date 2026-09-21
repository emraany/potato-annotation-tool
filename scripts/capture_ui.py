#!/usr/bin/env python3
"""
Capture the study's pages as full-page screenshots into ui/.

`potato preview --screenshot` writes the 1280x900 VIEWPORT, which on this task
is the instructions banner and half the argument -- none of the questions. These
are `full_page=True` against a running server, driven as a real annotator, which
is the only way to see the conditional half of the form at all.

    python scripts/capture_ui.py --url http://127.0.0.1:8765

Writes, and prints each page's height so a regression in page length is visible
without opening anything:

    01-login-and-register
    02-guidelines                           the guidelines banner, opened
    04-annotation-gate-shut                 the Sensible Test alone
    05-annotation-gate-open                 one check Yes, mechanism revealed
    06-annotation-one-secondary-ticked      one secondary, its justification open
    07-submit-refused                       Submit on an unfinished argument

It registers a throwaway account, which is a real annotator holding real
assignments: delete it afterwards, from `user_config.json` as well as from
`annotation_output/`.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--shots", default=str(ROOT / "ui"))
    args = ap.parse_args()

    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    user = "shot-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6)) + "@example.com"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})

        def shot(name: str) -> None:
            page.screenshot(path=str(shots / f"{name}.png"), full_page=True)
            print(f"  {name:<40} {page.evaluate('() => document.body.scrollHeight')}px "
                  f"({page.evaluate('() => document.body.scrollHeight') / 900:.1f} screens)")

        def advance() -> None:
            btn = page.locator("#next-btn").first
            btn.wait_for(state="visible", timeout=8000)
            for _ in range(25):
                if btn.is_enabled():
                    break
                page.wait_for_timeout(200)
            btn.click()
            page.wait_for_timeout(900)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

        page.goto(args.url, wait_until="networkidle")
        shot("01-sign-in")
        page.fill("#login-email", user)
        page.click("#login-content button[type=submit]")
        page.wait_for_load_state("networkidle")

        shot("04-annotation-gate-shut")
        page.click("[data-schema-name='manipulation_check'] input[value='Yes']")
        page.wait_for_timeout(700)
        page.click("[data-schema-name='deception_check'] input[value='No']")
        page.click("[data-schema-name='rhetorical_wrapper_check'] input[value='No']")
        page.wait_for_timeout(700)
        shot("05-annotation-gate-open")

        page.click("[data-schema-name='primary_mechanism'] input[value='Spurious Causal Correlation']")
        box = page.query_selector("[name='primary_justification:::text_box']")
        box.fill("Children #2 and #5 are only set beside each other; neither asserts the causal link.")
        box.dispatch_event("input")
        page.click("[data-schema-name='secondary_mechanisms'] input[value='Affective Hijacking']")
        page.wait_for_timeout(700)
        shot("06-annotation-one-secondary-ticked")

        # Submit on an argument that is not finished: it refuses, marks every
        # question that has no answer, and points at Save draft. This is the
        # state the two buttons exist for, so it is the one worth a picture.
        page.click("[data-schema-name='secondary_mechanisms'] input[value='Affective Hijacking']")
        page.wait_for_timeout(500)
        page.click("#next-btn")
        page.wait_for_timeout(900)
        shot("07-submit-refused")

        # The guidelines, which are the banner rather than a page. It starts
        # shut, so this opens it -- the shot is of what an annotator sees when
        # they go looking for a definition, not of the collapsed summary line.
        page.evaluate(
            "() => { const b = document.querySelector('details.annotation-instructions-banner');"
            " if (b) { b.open = true; } }")
        page.wait_for_timeout(400)
        shot("02-guidelines")

        browser.close()

    print(f"\nthrowaway annotator left behind: {user}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
