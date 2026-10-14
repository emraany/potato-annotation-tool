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
  research team edits theirs. So: read the file at boot and hand it to Potato
  as HTML -- rendered here rather than by Potato, because Potato's markdown has
  no tables and the guidelines have three. Nothing rewrites a sentence of it.

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
not start is worse than a study with a stale sentence in it: the six mechanism
names in guidelines.md against the six labels in config.yaml, which is the one
drift that would have annotators reading one set of definitions and clicking
another.
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
INSTANCES = ROOT / "data" / "instances.json"

# scripts/ on the path, so `import assignments` and `import accounts` work
# however this file was started -- `python scripts/serve.py` puts it there,
# `python -m scripts.serve` does not.
sys.path.insert(0, str(ROOT / "scripts"))

# Every question that has to be answered before an argument counts as done, and
# the minimum length where there is one. Filled in from config.yaml at boot --
# see `take_requirements` -- so adding a required scheme to the config is still
# the only thing anybody has to do.
REQUIRED: "list[tuple[str, int]]" = []


# --- the guidelines -----------------------------------------------------------

def guidelines_markdown() -> str:
    """guidelines.md, verbatim."""
    if not GUIDELINES.exists():
        raise SystemExit(f"{GUIDELINES} does not exist; there are no guidelines to show")
    return GUIDELINES.read_text()


# --- tables -------------------------------------------------------------------
#
# The guidelines carry three tables -- the task overview, the mechanisms and the
# rules -- and Potato's markdown renderer has no table syntax: a pipe table
# comes out as one paragraph of `|` characters. So the document is rendered
# here, a table at a time, and handed to Potato as HTML. Everything between
# the tables is still Potato's own markdown renderer, and the result goes
# through Potato's sanitizer either way (it lets table tags through).

TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_RULE = re.compile(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$")


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip()[1:-1].split("|")]


def inline(text: str) -> str:
    """One cell's markdown, without the <p> a renderer wraps it in."""
    from potato.codebook.markdown import render_markdown

    html = render_markdown(text).strip()
    if html.startswith("<p>") and html.endswith("</p>"):
        html = html[3:-4]
    return html


# A cell holding only `^` is merged into the cell above it -- which is how the
# Rules table gives each mechanism one cell down the side of all its rules.
MERGE_UP = "^"

# The Rules table's sections, by what is in its Mechanism cell: a light tint
# for the row, a deeper one for the merged Mechanism cell. Inline, because
# Potato's sanitizer strips `class` off <tr> and <td> but keeps `style` --
# `background-color`, not the `background` shorthand, which it drops.
RULE_COLOURS = {
    "Affective Hijacking": ("#fdecec", "#f8d5d5"),            # red
    "Episodic Overgeneralization": ("#f3edfc", "#e4d8f7"),    # purple
    "Satirical Trivialization": ("#eaf6ee", "#d3ecdb"),       # green
    "Spurious Legitimacy Signaling": ("#fdf7dd", "#f7ebb5"),  # yellow
    "All mechanisms": ("#f2f2f5", "#e2e2e8"),                 # grey
}


def render_table(lines: list[str]) -> str:
    header, body = table_cells(lines[0]), [table_cells(line) for line in lines[2:]]
    head = "".join(f"<th>{inline(cell)}</th>" for cell in header)

    # How many rows each cell spans: a `^` below adds one to the cell it joins.
    spans = [[1] * len(row) for row in body]
    for r, row in enumerate(body):
        for c, cell in enumerate(row):
            if cell == MERGE_UP:
                top = r - 1
                while top > 0 and c < len(body[top]) and body[top][c] == MERGE_UP:
                    top -= 1
                spans[top][c] += 1

    rules = header[:2] == ["Mechanism", "Rule"]
    rows, section = [], None
    for r, row in enumerate(body):
        if row and row[0] != MERGE_UP:
            section = row[0]
        light, deep = RULE_COLOURS.get(section, (None, None)) if rules else (None, None)
        cells = []
        for c, cell in enumerate(row):
            if cell == MERGE_UP:
                continue
            attrs = f' rowspan="{spans[r][c]}"' if spans[r][c] > 1 else ""
            if light:
                attrs += (f' style="background-color:{deep};font-weight:650;vertical-align:middle"'
                          if c == 0 else
                          f' style="background-color:{light}' + (';font-weight:600"' if c == 1 else '"'))
            cells.append(f"<td{attrs}>{inline(cell)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    kind = "guidelines-table rules-table" if rules else "guidelines-table"
    return (f'<table class="{kind}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>")


def guidelines_html(markdown: str) -> str:
    """guidelines.md as HTML, pipe tables included."""
    from potato.codebook.markdown import render_markdown

    parts, prose, lines, i = [], [], markdown.splitlines(), 0
    while i < len(lines):
        if (TABLE_ROW.match(lines[i]) and i + 1 < len(lines)
                and TABLE_RULE.match(lines[i + 1])):
            parts.append(render_markdown("\n".join(prose)) if "".join(prose).strip() else "")
            prose, end = [], i + 2
            while end < len(lines) and TABLE_ROW.match(lines[end]):
                end += 1
            parts.append(render_table(lines[i:end]))
            i = end
            continue
        prose.append(lines[i])
        i += 1
    if "".join(prose).strip():
        parts.append(render_markdown("\n".join(prose)))
    return "\n".join(parts)


def guidelines_mechanisms(markdown: str) -> list[str]:
    """The first column of the table under `## <n>. The Mechanisms`."""
    section = re.split(r"^## (?:\d+\. )?The Mechanisms\s*$", markdown, maxsplit=1, flags=re.M)
    if len(section) < 2:
        return []
    rows = [line for line in section[1].split("\n## ", 1)[0].splitlines()
            if TABLE_ROW.match(line) and not TABLE_RULE.match(line)]
    # "Affective Hijacking (AH)": the abbreviation is for the reader, not a label.
    return [re.sub(r"\s*\([A-Z]+\)$", "", table_cells(row)[0]) for row in rows[1:]]


def config_mechanism_labels(text: str) -> list[str]:
    block = re.search(r"name:\s*mechanisms\b(.*?)\n\n", text, re.S)
    if not block:
        return []
    return re.findall(r"\{name:\s*'([^']+)'", block.group(1))


def warn_on_drift(markdown: str) -> None:
    config_text = CONFIG.read_text()

    found = guidelines_mechanisms(markdown)
    expected = config_mechanism_labels(config_text)
    if found != expected:
        print("  WARNING  guidelines.md and config.yaml disagree about the mechanisms.\n"
              f"           guidelines.md: {found}\n"
              f"           config.yaml:   {expected}\n"
              "           Rename the LABEL in config.yaml to match the guidelines, and "
              "leave every scheme NAME alone -- renaming a scheme hides the answers "
              "already stored under it.", file=sys.stderr)


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

    # A Fail with nothing ticked is not finished. The page cannot store one any
    # more -- static/study.js sets the verdict from the ticks -- but a draft
    # left under the old Yes/No question can: Yes answered, the mechanisms not
    # yet ticked. `mechanisms` is optional now that none ticked is a pass, so
    # without this those drafts would read as finished Fails with no mechanism.
    # Opening one and pressing Submit settles it either way.
    if "Yes" in answers.get("sensible_test_failed", []) and not any(answers.get("mechanisms", [])):
        missing.append("mechanisms")
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


def patch_for_unticking(flask_server) -> None:
    """Un-ticking the last box of a multiselect is saved as an un-tick.

    The page saves through /updateinstance with the whole of its state, and
    Potato clears a multiselect's stored ticks before re-writing it -- but only
    for a multiselect that appears in the payload. Un-ticking every box removes
    the scheme from the payload altogether, so nothing is cleared and the old
    ticks stay stored: the argument reloads with them ticked, the justification
    boxes they gate count as owed, and the export reads mechanisms on what may
    be a passing argument. Un-ticking one of two was always fine; it is the last
    one that never reached the server.

    Here the page's save clears every multiselect it does not mention, because
    for that payload absent means nothing is ticked. Only /updateinstance:
    /submit_annotation also passes `complete_set=True` but is not what the page
    calls, and no one has said what its callers send.
    """
    from flask import request

    original_create_app = flask_server.create_app

    def create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        for name in ("routes", "potato.routes"):
            module = sys.modules.get(name)
            if module is None or getattr(module, "_vlmd_unticking", False):
                continue
            preclear = module._preclear_exclusive_schemas

            def preclear_absent_too(user_state, username, instance_id, schema_names,
                                    *, complete_set, _preclear=preclear):
                if (complete_set and instance_id != "__phase_page__"
                        and request.path == "/updateinstance"):
                    multiselects = {
                        s.get("name") for s in flask_server.config.get("annotation_schemes") or []
                        if isinstance(s, dict) and s.get("annotation_type") == "multiselect"}
                    schema_names = set(schema_names) | multiselects
                return _preclear(user_state, username, instance_id, schema_names,
                                 complete_set=complete_set)

            module._preclear_exclusive_schemas = preclear_absent_too
            module._vlmd_unticking = True
        return app

    flask_server.create_app = create_app


# --- assignments ---------------------------------------------------------------
#
# A stretch of the corpus handed to somebody, read off
# annotation_output/assignments.json -- see scripts/assignments.py for the file
# and why it is an overlay rather than a Potato assignment. Three things read
# it, and none of them changes what anybody can reach:
#
#   the band          `/vlmd/batch` feeds the strip static/study.js draws under
#                     the navbar: the stretch, how much of it is done, how much
#                     is left, and the next unfinished argument in it.
#   the arrows        stop at the edges of the batch instead of the edges of the
#                     corpus -- but only while the annotator is inside their
#                     batch. Outside it they step as they always did, so typing
#                     an out-of-batch number into `Go to` cannot strand anybody.
#   where you land    first arrival in a browser session goes to the first
#                     unfinished argument of the batch rather than argument 1.
#
# `Go to` still reaches all 1,621, an out-of-batch argument still saves, and an
# annotator with no entry in the file sees exactly the study as it ran before
# any of this existed.

_ASSIGNMENTS: dict = {"mtime": None, "data": None}
_CORPUS: dict = {}


def corpus() -> dict:
    """`{number: id}`, `{id: number}` and the corpus size, read once."""
    if not _CORPUS:
        instances = json.loads(INSTANCES.read_text()) if INSTANCES.exists() else []
        numbers = {int(i["item_number"]): i["id"] for i in instances}
        _CORPUS.update(number_to_id=numbers,
                       id_to_number={v: k for k, v in numbers.items()},
                       total=len(instances))
    return _CORPUS


def assignment_data() -> dict:
    """assignments.json, re-read when it changes.

    Re-read rather than loaded at boot because the console writes it while the
    study is running: an admin handing somebody a stretch should not need a
    restart to do it, and the annotator should see the band on their next page.
    Cheap -- it is one stat() per page render, and the file is a few hundred
    bytes.
    """
    import assignments

    try:
        mtime = assignments.FILE.stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _ASSIGNMENTS["mtime"] or _ASSIGNMENTS["data"] is None:
        _ASSIGNMENTS["mtime"] = mtime
        _ASSIGNMENTS["data"] = assignments.load()
    return _ASSIGNMENTS["data"]


def batch_numbers(username: str) -> set:
    import assignments
    return assignments.numbers_for(assignment_data(), username, corpus()["total"])


def batch_state(flask_server, username: str) -> dict:
    """What the band says: the stretch, what is done in it, what is left.

    Only the arguments that already carry an answer are examined for
    finishedness, for the same reason `progress_counts` only examines those:
    an argument nobody has touched cannot be finished, and walking every
    argument of a 400-long batch on every page render is work nobody asked for.
    """
    import assignments

    numbers = sorted(batch_numbers(username))
    if not numbers:
        return {"assigned": False, "corpus": corpus()["total"]}

    number_to_id = corpus()["number_to_id"]
    user_state = flask_server.get_user_state(username)
    annotated = set(user_state.get_annotated_instance_ids())

    done, drafts, next_unfinished = 0, 0, None
    for number in numbers:
        instance_id = number_to_id.get(number)
        if instance_id in annotated and not unfinished(flask_server, user_state, instance_id):
            done += 1
            continue
        if instance_id in annotated:
            drafts += 1
        if next_unfinished is None:
            next_unfinished = number

    current = None
    index = user_state.get_current_instance_index()
    ordering = user_state.instance_id_ordering
    if 0 <= index < len(ordering):
        current = corpus()["id_to_number"].get(ordering[index])

    data = assignment_data()
    return {
        "assigned": True,
        "corpus": corpus()["total"],
        "ranges": assignments.as_ranges(numbers),
        "labels": [e["label"] for e in assignments.ranges_for(data, username) if e.get("label")],
        "total": len(numbers),
        "done": done,
        "drafts": drafts,
        "left": len(numbers) - done,
        "next_unfinished": next_unfinished,
        "current": current,
        "in_batch": current in set(numbers) if current else False,
        # Whether the two arrows have anywhere to go. They are bounded by the
        # batch (see `step` below) and study.js greys out an arrow with nothing
        # behind it -- but study.js reads the CORPUS ends off the navbar, and
        # the batch ends are not in the page. They are here.
        "has_prev": bool(current and any(n < current for n in numbers)),
        "has_next": bool(current and any(n > current for n in numbers)),
    }


def patch_for_assignments(flask_server) -> None:
    import potato.user_state_management as user_states
    from flask import jsonify, session

    original_find_next = user_states.InMemoryUserState.find_next_unannotated_index
    original_find_prev = user_states.InMemoryUserState.find_prev_unannotated_index

    def step(self, forward: bool):
        """The neighbouring argument, inside the batch if they are inside it.

        Outside their batch -- somebody who typed 900 into `Go to` with 1-320
        to do -- the arrows are the plain neighbours they were before, because
        an arrow that jumped them back into the batch would be a trapdoor, and
        one that refused to move would be a dead end.
        """
        fallback = (original_find_next if forward else original_find_prev)(self)
        numbers = batch_numbers(getattr(self, "user_id", ""))
        if not numbers:
            return fallback

        index = self.get_current_instance_index()
        ordering = self.instance_id_ordering
        if not 0 <= index < len(ordering):
            return fallback
        here = corpus()["id_to_number"].get(ordering[index])
        if here is None or here not in numbers:
            return fallback

        candidates = [n for n in numbers if (n > here if forward else n < here)]
        if not candidates:
            return None            # the edge of the batch; study.js greys it out
        target = min(candidates) if forward else max(candidates)
        return self.instance_id_to_order.get(corpus()["number_to_id"].get(target), fallback)

    user_states.InMemoryUserState.find_next_unannotated_index = lambda self: step(self, True)
    user_states.InMemoryUserState.find_prev_unannotated_index = lambda self: step(self, False)

    original_create_app = flask_server.create_app

    def create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)

        @app.route("/vlmd/batch")
        def vlmd_batch():
            username = session.get("username")
            if not username:
                return jsonify({"assigned": False}), 200
            try:
                return jsonify(batch_state(flask_server, username))
            except Exception as error:      # noqa: BLE001 -- a band, not the study
                # The band is an aid. A study that will not render because the
                # strip above it could not be computed is a worse study.
                print(f"  WARNING  batch state for {username!r}: {error}", file=sys.stderr)
                return jsonify({"assigned": False}), 200

        @app.before_request
        def land_in_batch():
            """First arrival of a session lands on the first unfinished
            argument of the batch, not on argument 1.

            Once per session, and only from outside the batch: somebody who
            stopped at 180 yesterday comes back to 180, and somebody who walked
            out of their batch on purpose is not dragged back on the next page.
            """
            from flask import request

            if request.path not in ("/", "/annotate") or session.get("vlmd_landed"):
                return None
            username = session.get("username")
            if not username:
                return None
            session["vlmd_landed"] = True

            try:
                numbers = batch_numbers(username)
                if not numbers:
                    return None
                user_state = flask_server.get_user_state(username)
                ordering = user_state.instance_id_ordering
                if not ordering:
                    return None
                index = user_state.get_current_instance_index()
                here = (corpus()["id_to_number"].get(ordering[index])
                        if 0 <= index < len(ordering) else None)
                if here in numbers:
                    return None

                state = batch_state(flask_server, username)
                target = state.get("next_unfinished") or min(numbers)
                landing = user_state.instance_id_to_order.get(
                    corpus()["number_to_id"].get(target))
                if landing is not None:
                    user_state.current_instance_index = landing
            except Exception as error:      # noqa: BLE001
                print(f"  WARNING  could not land {username!r} in their batch: {error}",
                      file=sys.stderr)
            return None

        return app

    flask_server.create_app = create_app


def patch_for_accounts(flask_server) -> None:
    """Registration that validates, and a reset that is emailed, not displayed.

    Wrapped around create_app rather than written into Potato: the two view
    functions are swapped on the built app and the register form is rewritten
    as Jinja loads the template. scripts/accounts.py says why both had to
    change -- the short version is that Potato's /forgot-password prints a
    working reset link for any username typed into it.
    """
    import accounts

    original_create_app = flask_server.create_app

    def create_app(*args, **kwargs):
        app = original_create_app(*args, **kwargs)
        accounts.install_template_patch(app)
        accounts.install(app, flask_server)
        return app

    flask_server.create_app = create_app


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
        config["annotation_instructions"] = guidelines_html(markdown)
        # After take_requirements, which is what fills REQUIRED.
        take_requirements(config.get("annotation_schemes") or [])
        config["custom_footer_html"] = footer()
        print(f"guidelines   {GUIDELINES.name}, {len(markdown.splitlines())} lines, "
              "rendered into the banner on every argument")
        print(f"drafts       on; {len(REQUIRED)} question(s) required to finish an "
              "argument, none required to leave one")

    flask_server.init_config = init_config
    patch_for_drafts(flask_server)
    patch_for_unticking(flask_server)
    patch_for_assignments(flask_server)
    patch_for_accounts(flask_server)

    sys.argv = ["potato", "start", args.config, "-p", str(args.port)]
    flask_server.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
