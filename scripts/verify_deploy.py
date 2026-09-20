#!/usr/bin/env python3
"""
DEPLOY.md step 5, items 1 and 2, against a deployed study.

    python scripts/verify_deploy.py --url <url> --annotate    # before a redeploy
    python scripts/verify_deploy.py --url <url> --check       # after one

`walk_study.py` cannot do this job: it invents a random username, which a study
with `allow_all_users: false` refuses, and it confirms what was stored by
reading annotation_output/<user>/user_state.json off the local disk, which on
Railway lives on a volume this machine cannot see. So the form driving is
reused from it -- same selectors, same PLAN -- and the read-back goes through
the admin API instead.

WHY THE READ-BACK PROVES THE VOLUME.  `--check` runs against a process that was
started after the redeploy. Its in-memory state was loaded from disk at boot.
If the annotation is there, it came off the volume; there is nowhere else it
could have come from.

The session cookie is written to a file by `--annotate` and replayed by
`--check`. A cookie that still authenticates after a restart is
POTATO_SECRET_KEY doing its job -- without it Potato generates a signing key per
process and every annotator is logged out on every deploy.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from walk_study import PLAN, Walk, log  # noqa: E402

ITEM = PLAN[0]          # the PASS branch: three No, a note, nothing else asked


def admin_user_state(url: str, admin_key: str, user: str) -> dict:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/admin/user_state/{urllib.parse.quote(user)}",
        headers={"X-API-Key": admin_key},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def annotations_in(state: dict) -> dict:
    """The annotations out of whatever shape /admin/user_state returns.

    Tried in order rather than assuming one key: this endpoint is read-only
    plumbing for a check, and a wrong guess here would report a lost study.
    """
    for key in ("annotations", "all_annotations", "instance_id_to_label_to_value"):
        value = state.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def sign_in(page, url: str, user: str, password: str, register: bool) -> None:
    page.goto(url, wait_until="networkidle")
    if register:
        page.evaluate("switchTab('register')")
        page.fill("#register-email", user)
        page.fill("#register-pass", password)
        page.click("#register-content button[type=submit], "
                   "#register-content input[type=submit]")
    else:
        page.evaluate("switchTab('login')")
        page.fill("#login-email", user)
        page.fill("#login-pass", password)
        page.click("#login-content button[type=submit], "
                   "#login-content input[type=submit]")
    page.wait_for_load_state("networkidle")


def annotate(page, w: Walk) -> None:
    for scheme, value in ITEM["answers"].items():
        w.set_radio(scheme, value)
    for scheme, value in ITEM.get("text", {}).items():
        w.set_text(scheme, value)
    w.advance()


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--user", default="deploy-check@example.invalid")
    ap.add_argument("--password", default="deploy-check-password-1")
    ap.add_argument("--admin-key", required=True)
    ap.add_argument("--cookies", default="/tmp/potato-verify-cookies.json")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--annotate", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args()

    problems: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900})

        if args.check and Path(args.cookies).exists():
            # Replay the pre-restart session. No login call is made, so if the
            # study still recognises us the cookie survived the new process.
            context.add_cookies(json.loads(Path(args.cookies).read_text()))

        page = context.new_page()
        w = Walk(page, Path("/tmp"))

        if args.annotate:
            sign_in(page, args.url, args.user, args.password, register=True)
            if "annotate" not in page.url and not page.query_selector("#next-btn"):
                # Already registered from an earlier run; log in instead.
                sign_in(page, args.url, args.user, args.password, register=False)
            if not page.query_selector("#next-btn"):
                problems.append(f"could not reach the annotation page; at {page.url}")
            else:
                annotate(page, w)
                log(f"annotated one item as {args.user}")
            Path(args.cookies).write_text(json.dumps(context.cookies()))
            log(f"session cookie saved to {args.cookies}")

        else:
            page.goto(args.url.rstrip("/") + "/annotate", wait_until="networkidle")
            if page.query_selector("#next-btn"):
                log("SESSION SURVIVED: the saved cookie still authenticates "
                    "(POTATO_SECRET_KEY is stable across deploys)")
            else:
                problems.append(
                    "the saved session cookie no longer authenticates -- "
                    "POTATO_SECRET_KEY is not stable across deploys")

        problems.extend(w.problems)
        browser.close()

    # The volume check, over the admin API, against the post-redeploy process.
    try:
        state = admin_user_state(args.url, args.admin_key, args.user)
        stored = annotations_in(state)
        log(f"admin API reports {len(stored)} annotated instance(s) for {args.user}")
        if args.check and not stored:
            problems.append(
                "the annotation made before the redeploy is GONE -- the volume "
                "is not mounted at annotation_output/, or not persisting")
        elif args.check:
            log("VOLUME SURVIVED: the annotation made before the redeploy is "
                "still there, read back out of a process started after it")
    except urllib.error.HTTPError as exc:
        problems.append(f"admin API returned {exc.code}: {exc.reason}")
    except Exception as exc:
        problems.append(f"admin API unreachable: {type(exc).__name__}: {exc}")

    if problems:
        log("")
        log(f"{len(problems)} problem(s):")
        for problem in problems:
            log(f"  - {problem}")
        return 1
    log("")
    log("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
