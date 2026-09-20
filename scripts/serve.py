#!/usr/bin/env python3
"""
Start the study.

    python scripts/serve.py                 # http://localhost:8765
    python scripts/serve.py --port 9000

This is how the study is started. `potato start config.yaml` still boots, but it
boots a study with no guidelines on screen and no drafts, because both of those
are done here rather than in config.yaml. Two reasons they are here:

  THE GUIDELINES.  `annotation_instructions` takes inline text, not a filename,
  so the only ways to put guidelines.md in front of an annotator are to paste a
  copy of it into config.yaml or to read the file at boot. A pasted copy is a
  second copy of the six definitions, and it goes stale the first time the
  research team edits theirs. So: read the file, hand it to Potato as markdown.
  Nothing rewrites a sentence of it.

  DRAFTS.  `required: true` on a question means two different things, and Potato
  ties them together: "this has to be answered before the argument is done" and
  "this has to be answered before you may look at the next argument". The second
  one is what makes an annotator stuck on an argument they want to think about,
  and what makes them answer it badly to get past it. So the schemes are handed
  to Potato as optional -- which is what unblocks Next, in the browser and on
  the server both -- and what `required` really means is put back underneath, in
  the two places where it is about completeness rather than about navigation:

      the navbar badge      `In Progress` for an argument with answers but not
                            all of them; `Labeled` only when it is finished.
      the progress counter  counts finished arguments. A draft is not one.

  NAVIGATION IS A SCROLL, NOT A FILTER.  Potato's two arrow buttons beside the
  counter jump to the next and the previous UNANNOTATED argument, and that is
  the reason a finished argument used to be reachable only by typing its number
  into `Go to`: the arrows stepped straight over it. They step one argument at a
  time here, through the whole corpus, finished ones included -- the badge above
  is what says which kind of argument you landed on.

  Everything typed is already saved as it is typed; a draft is not a new kind of
  save, it is the same save without the gate. Nothing here relaxes what the
  export demands: scripts/export_annotations.py --strict still fails on an
  argument that was never finished.

  THE TWO BUTTONS.  Potato has one, and whether it lets you past is invisible
  until you press it. static/study.js splits it: **Save draft** leaves the
  argument unfinished and goes to the next one, **Submit** declares it done and
  says which question is missing if it is not. Submit is Potato's own button
  relabelled -- it keeps the save, the navigation and the loading state -- and
  the validation hangs off `navigateToNext`, so the right-arrow key means
  Submit too.

Also checked at boot, and only warned about, never fatal -- a study that will
not start is worse than a study with a stale sentence in it:

  * the six mechanism names in guidelines.md against the six labels in
    config.yaml, which is the one drift that would have annotators reading one
    set of definitions and clicking another;
  * the three Sensible Test check sentences in guidelines.md against the three
    question texts in config.yaml.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDELINES = ROOT / "guidelines.md"
CONFIG = ROOT / "config.yaml"

# Every question that has to be answered before an argument counts as done, and
# the minimum length where there is one. Filled in from config.yaml at boot --
# see `take_requirements` -- so adding a required scheme to the config is still
# the only thing anybody has to do.
REQUIRED: "list[tuple[str, int]]" = []


# --- the guidelines -----------------------------------------------------------

def guidelines_markdown() -> str:
    """guidelines.md, verbatim.

    Potato renders `annotation_instructions` as markdown only when it holds no
    block-level HTML tag, so this must stay free of raw HTML -- a single <table>
    in guidelines.md would send the whole document down the HTML path and the
    markdown around it would show as source text on every page.
    """
    if not GUIDELINES.exists():
        raise SystemExit(f"{GUIDELINES} does not exist; there are no guidelines to show")
    return GUIDELINES.read_text()


MECHANISM_HEADING = re.compile(r"^#{3,4}\s+(.+?)\s*$", re.M)
CHECK_LINE = re.compile(r"^-\s+\*\*The\s+(.+?)\s+Check:\*\*\s*(.+)$", re.M)


def config_mechanism_labels(text: str) -> list[str]:
    block = re.search(r"name:\s*primary_mechanism\b(.*?)\n\n", text, re.S)
    if not block:
        return []
    return re.findall(r"\{name:\s*'([^']+)'", block.group(1))


def warn_on_drift(markdown: str) -> None:
    config_text = CONFIG.read_text()

    section = markdown.split("## 4. The Mechanisms", 1)
    found = MECHANISM_HEADING.findall(section[1].split("\n## ", 1)[0]) if len(section) > 1 else []
    expected = config_mechanism_labels(config_text)
    if found != expected:
        print("  WARNING  guidelines.md and config.yaml disagree about the mechanisms.\n"
              f"           guidelines.md: {found}\n"
              f"           config.yaml:   {expected}\n"
              "           Rename the LABEL in config.yaml to match the guidelines, and "
              "leave every scheme NAME alone -- renaming a scheme hides the answers "
              "already stored under it.", file=sys.stderr)

    flat = " ".join(config_text.split())
    checks = CHECK_LINE.findall(markdown)
    if len(checks) != 3:
        print(f"  WARNING  guidelines.md has {len(checks)} Sensible Test check(s), not 3.",
              file=sys.stderr)
    for name, question in checks:
        if " ".join(question.split()) not in flat:
            print(f"  WARNING  the {name} Check is worded differently in config.yaml "
                  f"than in guidelines.md.\n           guidelines.md: {question}",
                  file=sys.stderr)


# --- drafts -------------------------------------------------------------------

def take_requirements(schemes: list) -> None:
    """Move `required` out of the config and into REQUIRED.

    After this the config claims nothing is required, which is what Potato's own
    two gates read -- the browser's, off the `validation="required"` attribute it
    writes into the generated form, and the server's, off `label_requirement`.
    Both stop blocking. REQUIRED is what the badge, the counter and the arrows
    are answered from afterwards.
    """
    for scheme in schemes:
        requirement = scheme.get("label_requirement")
        required = scheme.get("required") is True or requirement is True or (
            isinstance(requirement, dict) and requirement.get("required") is True)
        if not required:
            continue
        REQUIRED.append((scheme["name"], int(scheme.get("min_chars") or 0)))
        scheme["label_requirement"] = {"required": False}
        scheme.pop("required", None)


def unfinished(flask_server, user_state, instance_id: str) -> list:
    """The required questions this argument is still missing.

    Hidden questions are skipped, exactly as Potato's own check does: a required
    follow-up behind a `display_logic` gate that is shut cannot be answered, and
    counting it would mark every passing argument unfinished forever.
    """
    hidden = flask_server._hidden_scheme_names(user_state, instance_id)
    answers: dict = {}
    for label, value in (user_state.instance_id_to_label_to_value.get(instance_id) or {}).items():
        if hasattr(label, "get_schema"):
            answers.setdefault(label.get_schema(), []).append(value)

    missing = []
    for name, min_chars in REQUIRED:
        if name in hidden:
            continue
        values = [v for v in answers.get(name, []) if v]
        if not values:
            missing.append(name)
        elif min_chars:
            # A justification typed down to two words is not a finished one. The
            # export applies the same floor; applying it here too means the badge
            # says so while the annotator can still do something about it.
            longest = max((len(str(v).strip()) for v in values), default=0)
            if longest < min_chars:
                missing.append(name)
    return missing


def patch_for_drafts(flask_server) -> None:
    import potato.user_state_management as user_states
    from potato.quality_control import count_dataset_items

    # What `required` means now, everywhere it is about completeness: the badge
    # in the navbar reads this.
    flask_server._instance_meets_required_annotation_rules = (
        lambda user_state, instance_id: unfinished(flask_server, user_state, instance_id))

    # ...and what it no longer means. The routes module does
    # `from potato.flask_server import _instance_meets_required_annotation_rules`
    # when it is first imported, which is inside create_app() and therefore
    # after the line above -- so it takes a reference to the strict version and
    # would go on blocking Next with it. It has to be told separately, and only
    # once it exists.
    #
    # Imported as `routes`, not `potato.routes`: flask_server puts the package
    # directory on sys.path and imports it by bare name. Importing it the other
    # way here would build a SECOND module object, register all 159 handlers a
    # second time, and fail on the first duplicate endpoint -- so this reads
    # whatever is in sys.modules rather than importing anything.
    original_create_app = flask_server.create_app

    def create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        for name in ("routes", "potato.routes"):
            module = sys.modules.get(name)
            if module is not None:
                module._instance_meets_required_annotation_rules = (
                    lambda *ignored, **ignored_kw: [])
        return app

    flask_server.create_app = create_app

    def progress_counts(username):
        """Finished arguments, out of the whole corpus.

        Potato's own version counts an argument with any answer at all, which
        with drafts would count a half-filled one as done. Only the arguments
        that already carry an answer are examined -- the rest cannot be finished
        by definition, and checking all 1621 on every page render is work nobody
        asked for.
        """
        user_state = flask_server.get_user_state(username)
        ordering = list(user_state.instance_id_ordering)
        annotated = user_state.get_annotated_instance_ids().intersection(ordering)
        done = [i for i in annotated if not unfinished(flask_server, user_state, i)]
        return count_dataset_items(done), count_dataset_items(ordering)

    flask_server.progress_counts = progress_counts

    # The two arrow buttons. Potato aims them at the next and previous
    # UNANNOTATED argument, which is a filter rather than a scroll: a finished
    # argument is stepped over, and typing its number into `Go to` was the only
    # way back to one. Here they are the neighbours -- every argument is one
    # press away from the one beside it, and the navbar badge says whether the
    # one you land on is Labeled, In Progress or Not labeled.
    #
    # `None` at either end rather than a wrap-around: the far end of 1621
    # arguments is not where somebody pressing an arrow meant to go. Potato
    # answers `None` with an "All items have been annotated!" toast, which is
    # the wrong sentence here, so study.js disables the button that would
    # produce it -- this stays the backstop for a page whose script never ran.
    def find_next(self):
        current = self.get_current_instance_index()
        return current + 1 if current + 1 < len(self.instance_id_ordering) else None

    def find_prev(self):
        current = self.get_current_instance_index()
        return current - 1 if current > 0 else None

    user_states.InMemoryUserState.find_next_unannotated_index = find_next
    user_states.InMemoryUserState.find_prev_unannotated_index = find_prev


# --- the page script ----------------------------------------------------------
#
# static/study.js, inlined into every page as `custom_footer_html`. Inlined
# rather than linked because `static/` is not served -- Potato reads `base_css`
# off disk and inlines that too, and there is no route that would serve a second
# file out of the task directory.
#
# It does two things the template does not: it shuts the guidelines banner
# (Potato hardcodes `open`, and the banner holds a whole document), and it
# splits Next into Save draft and Submit. The requirement list it validates
# Submit against is written out beside it, from the same schemes the server
# reads, so the button and the badge cannot disagree about what "finished"
# means.
SCRIPT = ROOT / "static" / "study.js"


def footer() -> str:
    if not SCRIPT.exists():
        print(f"  WARNING  {SCRIPT} is missing; the page gets no Save draft button "
              "and the guidelines banner will be open on every argument",
              file=sys.stderr)
        return ""
    required = [{"name": name, "min_chars": min_chars} for name, min_chars in REQUIRED]
    return ("<script>window.VLMD_REQUIRED = "
            + json.dumps(required)
            + ";</script>\n<script>\n" + SCRIPT.read_text() + "\n</script>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args()

    markdown = guidelines_markdown()
    warn_on_drift(markdown)

    import potato.flask_server as flask_server

    original_init_config = flask_server.init_config

    def init_config(parsed):
        original_init_config(parsed)
        config = flask_server.config
        config["annotation_instructions"] = markdown
        # After take_requirements, which is what fills REQUIRED.
        take_requirements(config.get("annotation_schemes") or [])
        config["custom_footer_html"] = footer()
        print(f"guidelines   {GUIDELINES.name}, {len(markdown.splitlines())} lines, "
              "rendered into the banner on every argument")
        print(f"drafts       on; {len(REQUIRED)} question(s) required to finish an "
              "argument, none required to leave one")

    flask_server.init_config = init_config
    patch_for_drafts(flask_server)

    sys.argv = ["potato", "start", args.config, "-p", str(args.port)]
    flask_server.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
