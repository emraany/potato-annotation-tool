#!/usr/bin/env python3
"""
Walk this study the way an annotator does, and check the server kept what the
page showed.

The generic walker in the potato-skill answers every radio with its first
option, which cannot drive this task: `primary_mechanism` is behind
`display_logic`, so the walker fills it before the gate has opened and never
exercises the branch that carries the dataset.

So this script:

  1. registers, and lands straight on item one -- there is no instructions
     phase; the guidelines are the banner above the argument, and the walk
     checks all six mechanisms are in it
  2. annotates three real items, one per branch:
        a PASS (three No, nothing else asked)
        a FAIL with a primary mechanism only
        a FAIL with a primary and one secondary, each justified
  3. leaves a fourth item deliberately unfinished: Submit has to refuse it and
     say what is missing, Save draft has to let it go, and the item left behind
     has to read In Progress
  4. navigates back to the first of them and checks the answers are still there
     -- by navigation, never by reload, because a browser restores form state
     itself and a reload would pass even if the server stored nothing
  5. reads annotation_output/<user>/user_state.json and compares what is stored
     against what was typed, scheme by scheme

    python scripts/walk_study.py --url http://127.0.0.1:8765

Exits non-zero on any mismatch. Screenshots land in ui/walk/.
"""

from __future__ import annotations

import argparse
import json
import random
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The six mechanism names, as the guidelines banner should render them.
MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

# What we will type into the three real items, and expect back out of
# user_state.json unchanged.
PLAN = [
    {
        "tag": "pass",
        "answers": {
            "manipulation_check": "No",
            "deception_check": "No",
            "rhetorical_wrapper_check": "No",
        },
        "text": {"notes": "A pass: the children carry the claim on their own."},
        "flag": False,
    },
    {
        "tag": "fail-primary-only",
        "answers": {
            "manipulation_check": "Yes",
            "deception_check": "No",
            "rhetorical_wrapper_check": "No",
            "primary_mechanism": "Spurious Causal Correlation",
        },
        "text": {
            "primary_justification":
                "Child #2 and child #5 are only adjacent; nothing in either "
                "asserts the causal link the claim needs.",
        },
        "flag": False,
    },
    {
        "tag": "fail-primary-and-secondary",
        "answers": {
            "manipulation_check": "No",
            "deception_check": "Yes",
            "rhetorical_wrapper_check": "Yes",
            "primary_mechanism": "Esoteric Code Activation",
        },
        "multiselect": {"secondary_mechanisms": ["Affective Hijacking"]},
        "text": {
            "primary_justification":
                "The link only lands for a reader who already holds the stereotype "
                "named in child #14; the argument never states it.",
            "secondary_justification_affective_hijacking":
                "Child #5's described expression supplies the contempt that carries "
                "the reader past the missing step.",
            "notes": "Two checks failed here, which is why both boxes are open.",
        },
        "flag": True,
    },
]

VISIBLE_JS = """
(name) => {
  const el = document.querySelector(`[data-schema-name="${name}"]`);
  if (!el) return null;
  let node = el;
  while (node && node !== document.body) {
    const cs = getComputedStyle(node);
    if (cs.display === 'none' || cs.visibility === 'hidden'
        || node.classList.contains('display-logic-hidden')) return false;
    node = node.parentElement;
  }
  return true;
}
"""


def log(msg: str = "") -> None:
    print(msg, flush=True)


class Walk:
    def __init__(self, page, shots: Path):
        self.page = page
        self.shots = shots
        self.problems: list[str] = []

    def shot(self, name: str) -> None:
        self.page.screenshot(path=str(self.shots / f"{name}.png"), full_page=True)

    def visible(self, scheme: str):
        return self.page.evaluate(VISIBLE_JS, scheme)

    def set_radio(self, scheme: str, value: str) -> bool:
        sel = f"[data-schema-name='{scheme}'] input[value={json.dumps(value)}]"
        el = self.page.query_selector(sel)
        if el is None:
            self.problems.append(f"no radio {scheme}={value!r} on the page")
            return False
        el.click()
        self.page.wait_for_timeout(180)
        return True

    def set_checkbox(self, scheme: str, value: str) -> bool:
        sel = f"[data-schema-name='{scheme}'] input[value={json.dumps(value)}]"
        el = self.page.query_selector(sel)
        if el is None:
            self.problems.append(f"no checkbox {scheme}={value!r} on the page")
            return False
        if not el.is_checked():
            el.click()
            self.page.wait_for_timeout(180)
        return True

    def set_text(self, scheme: str, value: str) -> bool:
        # The input is named `<scheme>:::text_box` whether it is an input or a
        # textarea, so a `textarea[name=...]` selector breaks on the default.
        sel = f"[name='{scheme}:::text_box']"
        el = self.page.query_selector(sel)
        if el is None:
            self.problems.append(f"no text box for {scheme}")
            return False
        el.fill(value)
        el.dispatch_event("input")
        el.dispatch_event("change")
        self.page.wait_for_timeout(120)
        return True

    def advance(self) -> None:
        """Click the advance control.

        Re-queried through a locator rather than held as a handle: the button is
        replaced when the page re-renders, so a handle taken before filling the
        form is detached by the time it is clicked. It is also disabled until
        the required schemes are filled, so give it a moment to enable.
        """
        btn = self.page.locator("#next-btn").first
        try:
            btn.wait_for(state="visible", timeout=8000)
        except Exception:
            self.problems.append("no advance button became visible on the page")
            return
        for _ in range(25):
            if btn.is_enabled():
                break
            self.page.wait_for_timeout(200)
        else:
            self.problems.append(
                f"the advance button never enabled; required warnings: "
                f"{self.required_warning() or 'none shown'}")
            return
        try:
            btn.click()
        except Exception as exc:                      # a re-render mid-click
            self.problems.append(f"advance click failed: {type(exc).__name__}")
            return
        self.page.wait_for_timeout(900)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    def save_draft(self) -> None:
        """Leave the argument unfinished and move on.

        The study replaces Potato's single Next with two buttons, and this is
        the one that does not validate -- see static/study.js.
        """
        btn = self.page.locator("#draft-btn").first
        if not btn.count():
            self.problems.append("no Save draft button on the page")
            return
        btn.click()
        self.page.wait_for_timeout(900)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    def required_warning(self) -> list[str]:
        return self.page.evaluate(
            "() => [...document.querySelectorAll('.required-fields-error, .alert, .error')]"
            ".map(e => e.textContent.trim()).filter(t => t.toLowerCase().includes('required'))")

    def instance_id(self) -> str:
        return self.page.evaluate(
            "() => document.querySelector('[data-instance-id]')?.dataset.instanceId"
            " || (window.currentInstance && window.currentInstance.id) || ''")

    def instance_number(self) -> int | None:
        """The number in the navbar, which is the number in args_0911.json.

        The corpus is served in file order and every annotator holds all of it,
        so `Instance #N` on screen, `Argument N of 1621` on the item, and the
        Nth entry of args_0911.json are the same argument. This reads the navbar
        rather than the item so the walk would notice if they stopped agreeing.
        """
        text = self.page.evaluate(
            "() => document.getElementById('instance-number')?.textContent || ''")
        digits = "".join(c for c in text if c.isdigit())
        return int(digits) if digits else None

    def go_to(self, number: int) -> None:
        """Type a number into the navbar box and go there. 1-based, as typed."""
        box = self.page.locator("#go_to").first
        if not box.count():
            self.problems.append("no jump box in the navbar")
            return
        box.fill(str(number))
        self.page.locator("#go-to-btn").first.click()
        self.page.wait_for_timeout(1200)
        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass


def answer_item(w: Walk, answers: dict, texts: dict, multi: dict, flag: bool) -> None:
    """Checks first, then whatever the gate opened. Order matters here."""
    for scheme in ("manipulation_check", "deception_check", "rhetorical_wrapper_check"):
        if scheme in answers:
            w.set_radio(scheme, answers[scheme])
    w.page.wait_for_timeout(400)

    if "primary_mechanism" in answers:
        if not w.visible("primary_mechanism"):
            w.problems.append("primary_mechanism stayed hidden after a check was answered Yes")
        else:
            w.set_radio("primary_mechanism", answers["primary_mechanism"])

    for scheme, values in (multi or {}).items():
        for value in values:
            w.set_checkbox(scheme, value)
    w.page.wait_for_timeout(400)

    for scheme, value in (texts or {}).items():
        w.set_text(scheme, value)

    if flag:
        w.set_checkbox("flag_for_discussion", "Flag for discussion")


def main() -> int:
    from playwright.sync_api import sync_playwright

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--task-dir", default=str(ROOT))
    ap.add_argument("--shots", default=str(ROOT / "ui" / "walk"))
    ap.add_argument("--keep-user", action="store_true",
                    help="Do not print the handover reminder about deleting this account")
    args = ap.parse_args()

    shots = Path(args.shots)
    shots.mkdir(parents=True, exist_ok=True)
    # A NetID, not an email: the study is passwordless and the sign-in field
    # takes either, but a throwaway that looks like a NetID is the one whose
    # directory is obvious in annotation_output/ afterwards.
    user = "walk" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        w = Walk(page, shots)

        page.goto(args.url, wait_until="networkidle")
        # One field, no password: the NetID is the whole sign-in, and a NetID
        # the study has not seen creates the account on the spot.
        page.fill("#login-email", user)
        page.click("#login-content button[type=submit], #login-content input[type=submit]")
        page.wait_for_load_state("networkidle")
        log(f"signed in as {user}")

        # --- the guidelines ----------------------------------------------------
        # They are the banner, not a link to a page: the whole of guidelines.md
        # renders above every argument. Two things have to hold. All six
        # mechanism definitions have to be in it -- a markdown document that
        # failed to render comes through as source text and would still contain
        # the names, so the check is on the rendered headings. And it has to
        # start shut: Potato hardcodes `open`, scripts/serve.py shuts it, and a
        # banner that stopped being shut puts three screens of prose above the
        # work on every item.
        banner = page.query_selector("details.annotation-instructions-banner")
        content = page.query_selector(".annotation-instructions-content")
        if not banner or not content:
            w.problems.append("no instructions banner on the page")
        else:
            if banner.get_attribute("open") is not None:
                w.problems.append("the guidelines banner is open by default")
            # text_content, not inner_text: the banner is shut, and Playwright
            # reads nothing out of an element that is not being rendered.
            headings = [h.text_content().strip() for h in content.query_selector_all("h4")]
            missing = [m for m in MECHANISMS if m not in headings]
            if missing:
                w.problems.append(f"the guidelines banner is missing {missing}")
            else:
                log(f"  guidelines in the banner, shut, {len(headings)} mechanisms")

        # Two buttons, not one. If study.js failed to load, Potato's single
        # `Next` is still there and still works, so nothing else in this walk
        # would notice -- and the annotator would have lost the only control
        # that lets them leave an argument unfinished.
        labels = page.evaluate(
            "() => ['draft-btn', 'next-btn'].map(id =>"
            " document.getElementById(id)?.textContent.trim() || null)")
        if labels != ["Save draft", "Submit"]:
            w.problems.append(f"the two buttons read {labels}, not "
                              "['Save draft', 'Submit']")
        else:
            log("  Save draft and Submit are both on the page")

        # --- the annotation phase ---------------------------------------------
        if not page.query_selector("[data-schema-name='primary_mechanism']"):
            w.shot("03-never-reached-annotation")
            log("\nnever reached the annotation phase")
            browser.close()
            for prob in w.problems:
                log(f"  - {prob}")
            return 2

        log("\nannotation phase")
        typed: dict[str, dict] = {}
        first_id = None
        for n, step in enumerate(PLAN, start=1):
            iid = w.instance_id()
            if first_id is None:
                first_id = iid
            log(f"  item {n} [{step['tag']}] {iid}")
            answer_item(w, step["answers"], step.get("text", {}),
                        step.get("multiselect", {}), step["flag"])
            w.shot(f"04-item{n}-{step['tag']}")

            # What SHOULD be on screen, given the branch.
            gated = {"primary_mechanism", "primary_justification", "secondary_mechanisms"}
            any_yes = any(v == "Yes" for k, v in step["answers"].items() if k.endswith("_check"))
            for scheme in gated:
                vis = w.visible(scheme)
                if any_yes and vis is False:
                    w.problems.append(f"item {n}: {scheme} hidden although a check is Yes")
                if not any_yes and vis is True:
                    w.problems.append(f"item {n}: {scheme} shown although all checks are No")

            expected = dict(step["answers"])
            expected.update(step.get("text", {}))
            for scheme, values in step.get("multiselect", {}).items():
                expected[scheme] = values
            if step["flag"]:
                expected["flag_for_discussion"] = ["Flag for discussion"]
            typed[iid] = expected

            before_id = iid
            w.advance()
            warn = w.required_warning()
            if warn:
                w.problems.append(f"item {n} [{step['tag']}] blocked on: {warn}")
                w.shot(f"04-item{n}-blocked")
                break
            # Advancing has to actually move. A click that saves but does not
            # navigate looks like success and silently annotates the next item
            # on top of this one.
            page.wait_for_timeout(800)
            if w.instance_id() == before_id:
                w.shot(f"04-item{n}-did-not-advance")
                w.problems.append(
                    f"item {n} [{step['tag']}] saved but the page stayed on "
                    f"{before_id}; the next item would be annotated over it")
                break

        # --- the draft ---------------------------------------------------------
        # Item four is answered ONE question deep and left. That is the whole
        # feature: Next must move, because being unable to leave a hard argument
        # is what makes people answer it badly to get past it. And the argument
        # left behind must read `In Progress` rather than `Labeled`, because the
        # badge is the only thing on screen that says it still owes an answer.
        log("\nleaving item four as a draft")
        draft_id = w.instance_id()
        draft_index = w.instance_number()
        w.set_radio("manipulation_check", "Yes")       # opens the gate, answers nothing else
        w.shot("04b-draft-part-answered")

        # Submit has to refuse this, and say which questions are missing. A
        # Submit that let it through would make the badge and the counter lie.
        page.locator("#next-btn").first.click()
        page.wait_for_timeout(900)
        marked = page.evaluate(
            "() => [...document.querySelectorAll('.vlmd-unfinished')]"
            ".map(f => f.dataset.schemaName)")
        if w.instance_id() != draft_id:
            w.problems.append("Submit advanced a part-answered argument")
        elif not marked:
            w.problems.append("Submit refused a part-answered argument but marked "
                              "no question, so nothing on screen says what is missing")
        else:
            log(f"  Submit refused it and marked {len(marked)} question(s)")

        w.save_draft()
        page.wait_for_timeout(800)
        if w.instance_id() == draft_id:
            w.problems.append("Save draft did not move off a part-answered argument")
            w.shot("04b-draft-blocked")
        else:
            log(f"  left argument {draft_index} part-answered and moved on")
            if draft_index:
                w.go_to(draft_index)
                badge = page.query_selector(".status-badge")
                state = badge.get_attribute("class") if badge else ""
                if "in_progress" not in (state or ""):
                    w.problems.append(
                        f"the draft reads {state!r}, not in_progress; the badge cannot "
                        "be relied on to say what is unfinished")
                else:
                    log("  and it reads In Progress")
                w.shot("04c-draft-in-progress")

        # --- navigate back, never reload --------------------------------------
        log("\nnavigating back to the first item (jump box, not reload)")
        # The navbar jump box is input[type=number]: it takes the 1-based
        # INSTANCE INDEX, not the instance id. The two arrows beside it are
        # previous/next *unannotated*, so on work already done they do nothing.
        box = page.locator("input[type=number]").first
        if box.count():
            box.fill("1")
            go = page.locator("button:has-text('Go')").first
            if go.count():
                go.click()
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
        else:
            w.problems.append("no jump control found; could not navigate back")

        w.shot("05-returned-to-first-item")
        back_on = w.instance_id()
        if first_id and back_on == first_id:
            restored = page.evaluate(
                "() => Object.fromEntries([...document.querySelectorAll("
                "'[data-schema-name] input:checked')].map(e => [e.name, e.value]))")
            log(f"  back on {back_on}, checked inputs restored: {len(restored)}")
            if not restored:
                w.problems.append(
                    f"came back to {first_id} and no answer was restored; the server "
                    f"did not keep what the page showed")
        else:
            log(f"  could not return to {first_id!r} (landed on {back_on!r}); "
                f"checking user_state.json instead")

        browser.close()

    # --- what the server actually stored -------------------------------------
    state_path = Path(args.task_dir) / "annotation_output" / user / "user_state.json"
    log(f"\n{state_path}")
    if not state_path.exists():
        log("  MISSING — nothing was stored for this annotator at all")
        return 1

    state = json.loads(state_path.read_text())
    stored = {iid: normalize_stored(v)
              for iid, v in state.get("instance_id_to_label_to_value", {}).items()}
    log(f"  {len(stored)} instance(s) stored")

    bad = 0
    for iid, expected in typed.items():
        got = stored.get(iid)
        if got is None:
            log(f"  {iid}: NOT in user_state.json, although it was answered")
            bad += 1
            continue
        log(f"  {iid}")
        for scheme, value in sorted(expected.items()):
            actual = got.get(scheme)
            ok = _matches(value, actual)
            bad += 0 if ok else 1
            log(f"    {'ok  ' if ok else 'BAD '} {scheme:<52} stored={json.dumps(actual, ensure_ascii=False)}")

    # The constraint the brief cares about most: a passing item must carry no
    # mechanism key at all, so it exports as absent rather than as "None".
    log("")
    for iid, expected in typed.items():
        if all(expected.get(k) == "No" for k in
               ("manipulation_check", "deception_check", "rhetorical_wrapper_check")):
            leaked = [k for k in ("primary_mechanism", "primary_justification",
                                  "secondary_mechanisms")
                      if stored.get(iid, {}).get(k) not in (None, {}, [])]
            if leaked:
                log(f"  BAD  {iid} passed the Sensible Test but still stores {leaked}")
                bad += 1
            else:
                log(f"  ok   {iid} passed, and stores no mechanism key at all")

    if bad:
        w.problems.append(f"{bad} stored value(s) did not match what was typed")

    log("")
    if w.problems:
        log("PROBLEMS")
        for prob in w.problems:
            log(f"  - {prob}")
    else:
        log("no problems found")
    if not args.keep_user:
        log(f"\nThis run left a real annotator account behind: {user}")
        log("handover.py removes it, or delete annotation_output/<user>/ by hand.")
    return 1 if w.problems else 0


def normalize_stored(raw):
    """
    `instance_id_to_label_to_value[<id>]` as a {scheme: {label: value}} dict.

    Measured on 2.9.3, an item is stored as a LIST of `[{schema, name}, value]`
    pairs -- a dict with tuple keys, serialized. The documentation and the
    tabular exporter both describe the nested-dict form, which is what comes
    back after Potato's own normalization, so a script reading user_state.json
    straight off disk has to handle both.
    """
    if isinstance(raw, dict):
        return raw
    out: dict[str, dict] = {}
    for entry in raw or []:
        try:
            key, value = entry
            out.setdefault(key["schema"], {})[key["name"]] = value
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _matches(expected, actual) -> bool:
    """Potato stores a label set as {label: value}; compare on the keys."""
    if actual is None:
        return False
    if isinstance(expected, list):
        return set(expected) <= set(actual if isinstance(actual, dict) else [actual])
    if isinstance(actual, dict):
        return expected in actual or expected in actual.values() or \
            any(str(expected) == str(v) for v in actual.values())
    return str(expected) == str(actual)


if __name__ == "__main__":
    sys.exit(main())
