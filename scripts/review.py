#!/usr/bin/env python3
"""
Resolving disagreements: what the review site shows and what it writes.

Two annotators who read the same argument differently sit down together, one
shares a screen, and they settle it. This module is everything about that
except the web page -- scripts/review_site.py is the page -- so the backend
console can build the gold dataset from the same code the review site saves
through.

WHAT IT READS. The annotations on disk, through `export_annotations.collect`,
and the colour of each argument, through `agreement.category` -- the same
function that colours the Agreement tab's map. An argument is the same colour
here as it is there.

WHAT IT WRITES. One file: annotation_output/review/decisions.json. Nobody's
user_state.json is opened for writing, ever. Deleting that file un-resolves
everything and changes nothing an annotator sees. It lives under
annotation_output/ only because that is the directory the Railway volume is
mounted on; anywhere else, it would be gone on the next deploy.

ONLY THE PAIR. An argument is shown, coloured and counted for a pair from
those two people's answers alone, whoever else annotated it. A third
annotator's answer never appears on a pair's page or changes its colour.

ONE DECISION PER ARGUMENT. An argument four people annotated belongs to six
pairs, and whichever pair resolves it first settles it for all of them. The
others see it as resolved, and by whom, and can reopen it. Reopening replaces
the decision and pushes the old one onto that argument's `history`, so nothing
that was ever decided is lost.

THE GOLD DATASET, one row per argument, from two sources:

  agreed    two or more annotators, every one of them finished, the same
            verdict and the same mechanism set, and every answer within the
            rules `validate` holds a resolution to. Taken as-is. Agreeing on
            an answer the rules forbid (a Fail with no mechanism, say) is a
            disagreement with the guidelines, and goes to review instead.
  resolved  a decision saved here. It wins over `agreed` -- a pair can open
            a green argument and overrule it.

An argument one person annotated is in neither, because nobody has checked it.

A DECISION ANSWERS WHAT WAS SAID AT THE TIME. Each one keeps what every
annotator had said when it was made. If one of them changes their answer
afterwards, the decision still stands in gold -- it is what the pair settled
on -- but it is marked `changed_since_resolved`, and the pair see it back on
their map as not done until they look again and save.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import threading
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement as iaa              # noqa: E402
import export_annotations as export  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INSTANCES = ROOT / "data" / "instances.json"
OUTPUT = ROOT / "annotation_output"
DECISIONS = OUTPUT / "review" / "decisions.json"
TASKS = OUTPUT / "review" / "assignments.json"

MECHANISMS = export.MECHANISMS

# Accounts that are on disk but are not annotators: made while the study was
# being built and checked. They are left out of the review queue, out of the
# pair sign-in, and out of gold. REVIEW_EXCLUDE (comma-separated) replaces the
# list rather than adding to it.
NOT_ANNOTATORS = frozenset(
    n.strip().lower() for n in
    os.environ.get("REVIEW_EXCLUDE", "demo1,demo2,demo3").split(",") if n.strip())

# Worst last, as in agreement.CATEGORIES. An argument with three or more
# annotators takes the colour of its worst pair.
ORDER = [name for name, _ in iaa.CATEGORIES]
TITLES = dict(iaa.CATEGORIES)

_lock = threading.Lock()


# --- reading ------------------------------------------------------------------

def load_items() -> dict:
    return {i["id"]: i for i in json.loads(INSTANCES.read_text())}


def load_records(items: dict) -> list[dict]:
    if not OUTPUT.exists():
        return []
    records, _ = export.collect(ROOT, items)
    return [r for r in records if r["annotator_id"] not in NOT_ANNOTATORS]


def annotators(records: list[dict]) -> set[str]:
    return {r["annotator_id"] for r in records}


# --- what kind of disagreement an argument is ---------------------------------

def of_pair(per: dict, pair) -> dict:
    """The two people's answers out of everybody's on one argument."""
    return {who: per[who] for who in pair if who in per}


def colour(sets: dict) -> str:
    """The worst pairwise category among everybody who annotated it."""
    cats = [iaa.category(a, b) for a, b in combinations(sets.values(), 2)]
    return max(cats, key=ORDER.index) if cats else "identical"


def _list(names) -> str:
    names = [n for n in MECHANISMS if n in names] + sorted(set(names) - set(MECHANISMS))
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _nothing(verdict) -> str:
    if verdict == "Pass":
        return "passed it, no mechanism"
    if verdict == "Fail":
        return "failed it but ticked no mechanism"
    return "left it unfinished, no mechanism"


def header(per: dict) -> dict:
    """The sentence at the top of an argument, read out at the meeting.

    One template, which comes out as the five the map's colours need:
    what everybody ticked, then what each person did beyond that. Two people
    with one extra between them read "Both ticked X. A also added Y."; nothing
    in common reads "A ticked X. B ticked Y."
    """
    sets = {who: iaa.mechanism_set(r.get("mechanisms")) for who, r in per.items()}
    verdicts = {who: iaa.verdict(r) for who, r in per.items()}
    kind = colour(sets)
    everybody = "Both" if len(per) == 2 else f"All {len(per)}"

    common = frozenset.intersection(*sets.values()) if sets else frozenset()
    lines = []
    if kind == "identical":
        lines.append(f"{everybody} ticked {_list(common)}." if common
                     else f"{everybody} ticked no mechanism.")
    else:
        if common:
            lines.append(f"{everybody} ticked {_list(common)}.")
        for who in sorted(per):
            mine = sets[who]
            if not mine:
                lines.append(f"**{who}** {_nothing(verdicts[who])}.")
            elif mine - common:
                verb = "also added" if common else "ticked"
                lines.append(f"**{who}** {verb} {_list(mine - common)}.")

    # An answer outside the rules, even one everybody gave. Unfinished answers
    # are left to the verdict line: an unanswered Sensible Test is a draft,
    # not a broken rule.
    rule_lines = [f"**{who}**: {problem}"
                  for who in sorted(per) if verdicts[who]
                  for problem in validate(per[who])]

    split = len({v for v in verdicts.values()}) > 1
    verdict_line = None
    if split:
        verdict_line = "Pass/Fail differs: " + "; ".join(
            f"**{who}** said {verdicts[who] or 'nothing yet'}" for who in sorted(per)) + "."

    return {
        "category": kind,
        "title": TITLES[kind],
        "lines": lines,
        "verdict_line": verdict_line,
        "rule_lines": rule_lines,
        # Green with a split verdict, or green with an answer the rules forbid,
        # is still a disagreement. The map cannot show it -- two empty sets are
        # identical whatever the verdicts said -- so it is carried separately
        # and counted as needing review.
        "needs_review": kind != "identical" or split or bool(rule_lines),
        "common": sorted(common, key=MECHANISMS.index),
    }


# --- the decisions file -------------------------------------------------------

def load_decisions() -> dict:
    if not DECISIONS.exists():
        return {}
    try:
        payload = json.loads(DECISIONS.read_text())
    except ValueError:
        # A corrupt file must not read as "nothing resolved": the next save
        # would overwrite every decision in it. Refuse instead.
        raise RuntimeError(f"{DECISIONS} is not valid JSON; fix or move it before reviewing")
    return payload.get("decisions") or {}


def _atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=1, ensure_ascii=False)
    # Written beside the target and renamed over it, so a crash mid-write
    # leaves the old file rather than half of a new one.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.stem + "-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


def _write(decisions: dict) -> None:
    _atomic(DECISIONS, {"version": 1, "decisions": decisions})


def validate(decision: dict) -> list[str]:
    """What the brief demands of an annotation, demanded of a resolution too.

    Gold is the dataset, so it holds itself to the rules the export's
    validation report holds the annotators to: a Pass carries no mechanism, a
    Fail carries at least one, and every mechanism carries a justification
    that says something.
    """
    problems = []
    verdict = decision.get("sensible_test")
    mechanisms = decision.get("mechanisms") or []
    if verdict not in iaa.VERDICTS:
        problems.append("Choose Pass or Fail.")
    if verdict == "Pass" and mechanisms:
        problems.append("A Pass has no mechanisms. Untick them, or make it a Fail.")
    if verdict == "Fail" and not mechanisms:
        problems.append("A Fail needs at least one mechanism.")
    for m in mechanisms:
        if m not in MECHANISMS:
            problems.append(f"{m!r} is not a mechanism.")
            continue
        text = (decision.get("justifications") or {}).get(m)
        if export.is_empty_in_spirit(text):
            problems.append(f"{m} needs a justification.")
        elif len(text.strip()) < export.MIN_JUSTIFICATION_CHARS:
            problems.append(f"{m}: the justification is under "
                            f"{export.MIN_JUSTIFICATION_CHARS} characters.")
    return problems


def changed_since(decision: dict, per: dict) -> list[str]:
    """Who has changed their verdict or mechanisms since the decision was made.

    Compared against the answers the decision kept. Justifications and notes
    are not compared: the decision chose its own, and rewording one is not a
    different answer.
    """
    changed = []
    for who, then in sorted((decision.get("annotations") or {}).items()):
        now = per.get(who)
        if (now is None or iaa.verdict(now) != then.get("sensible_test")
                or iaa.mechanism_set(now.get("mechanisms"))
                != iaa.mechanism_set(then.get("mechanisms"))):
            changed.append(who)
    return changed


def is_settled(decision, per: dict) -> bool:
    """Resolved, and nobody has changed their answer since."""
    return bool(decision) and not changed_since(decision, per)


def save(instance_id: str, decision: dict, pair: list[str], per: dict, item: dict) -> dict:
    """Record a resolution. The previous one, if any, goes onto `history`."""
    mechanisms = [m for m in MECHANISMS if m in (decision.get("mechanisms") or [])]
    record = {
        "instance_id": instance_id,
        "item_number": item.get("item_number"),
        "sensible_test": decision["sensible_test"],
        "mechanisms": mechanisms,
        "justifications": {m: decision["justifications"][m].strip() for m in mechanisms},
        "note": (decision.get("note") or "").strip(),
        "resolved_by": sorted(pair),
        "resolved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # What the annotators had said when this was decided, so a decision
        # can be read later without reconstructing the state it answered.
        "category": header(per)["category"],
        "annotations": {who: {"sensible_test": r["sensible_test"],
                              "mechanisms": r["mechanisms"]}
                        for who, r in sorted(per.items())},
    }
    with _lock:
        decisions = load_decisions()
        previous = decisions.get(instance_id)
        if previous:
            history = previous.pop("history", [])
            record["history"] = history + [previous]
        decisions[instance_id] = record
        _write(decisions)
    return record


# --- what each pair has been given to resolve ---------------------------------
#
# A task is a pair, a stretch of argument numbers and the colours in it to
# resolve. The study owner hands them out on the backend console; the review
# site shows a pair their tasks and nothing else. Written by the console only,
# to annotation_output/review/assignments.json -- not the annotators'
# annotation_output/assignments.json, which is a different thing.

NON_GREEN = [name for name in ORDER if name != "identical"]


def load_tasks() -> list[dict]:
    if not TASKS.exists():
        return []
    try:
        return json.loads(TASKS.read_text()).get("tasks") or []
    except ValueError:
        raise RuntimeError(f"{TASKS} is not valid JSON; fix or move it before assigning")


def overlap_ranges(numbers) -> list[list[int]]:
    """Argument numbers both of a pair annotated, as runs: [[from, to, n], ...]."""
    runs = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
            runs[-1][2] += 1
        else:
            runs.append([n, n, 1])
    return runs


def shared_numbers(by_argument: dict, items: dict, pair) -> list[int]:
    """The argument numbers both of a pair annotated."""
    a, b = sorted(pair)
    return sorted(items[i]["item_number"] for i, per in by_argument.items()
                  if a in per and b in per and items.get(i, {}).get("item_number") is not None)


def add_task(pair, lo, hi, colours, label: str, total: int,
             shared=(), cap=None) -> dict:
    """`shared` is the argument numbers both of the pair annotated. A blank
    from or to is the start or end of that overlap. `cap` stops the task at
    that many shared arguments counted from `from`: 1-100 capped at 30 ends at
    the 30th shared argument, or at 100 if there are fewer."""
    pair = sorted({str(p).strip().lower() for p in pair if str(p).strip()})
    if len(pair) != 2:
        raise ValueError("A task needs two different annotators.")
    shared = sorted(set(shared))
    if not shared:
        raise ValueError(f"{pair[0]} and {pair[1]} have not annotated any of the same arguments.")
    blank = lambda v: v is None or str(v).strip() == ""
    lo = shared[0] if blank(lo) else int(lo)
    hi = shared[-1] if blank(hi) else int(hi)
    if lo > hi:
        lo, hi = hi, lo
    if lo < 1 or hi > total:
        raise ValueError(f"Argument numbers run from 1 to {total}.")
    cap = None if blank(cap) else int(cap)
    if cap is not None and cap < 1:
        raise ValueError("The cap has to be at least 1, or left blank for all of them.")
    inside = [n for n in shared if lo <= n <= hi]
    if cap is not None and cap < len(inside):
        hi = inside[cap - 1]
    colours = [c for c in ORDER if c in (colours or [])]
    if not colours:
        raise ValueError("Pick at least one colour to resolve.")
    task = {
        "id": secrets.token_hex(4),
        "pair": pair, "from": lo, "to": hi, "colours": colours,
        "label": (label or "").strip(),
        "cap": cap,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _lock:
        tasks = load_tasks()
        tasks.append(task)
        _atomic(TASKS, {"version": 1, "tasks": tasks})
    return task


def remove_task(task_id: str) -> bool:
    """Take a task back. Decisions made under it stay: they are about the
    argument, not about the task."""
    with _lock:
        tasks = load_tasks()
        kept = [t for t in tasks if t.get("id") != task_id]
        if len(kept) == len(tasks):
            return False
        _atomic(TASKS, {"version": 1, "tasks": kept})
    return True


def tasks_for(pair) -> list[dict]:
    pair = sorted(pair)
    return [t for t in load_tasks() if t.get("pair") == pair]


def task_arguments(task: dict, items: dict, by_argument: dict) -> list[dict]:
    """The arguments a task covers: in its range, annotated by both of the
    pair, and one of its colours. A green argument whose Pass/Fail differs, or
    where somebody's answer breaks a rule, goes with any task that asks for a
    non-green colour, since it is a disagreement the map has no colour for."""
    a, b = task["pair"]
    wanted = set(task.get("colours") or [])
    out = []
    for instance_id, per in by_argument.items():
        if a not in per or b not in per:
            continue
        number = items.get(instance_id, {}).get("item_number")
        if number is None or not (task["from"] <= number <= task["to"]):
            continue
        info = header(of_pair(per, task["pair"]))
        hidden = info["category"] == "identical" and info["needs_review"]
        if info["category"] in wanted or (hidden and wanted & set(NON_GREEN)):
            out.append({"instance_id": instance_id, "number": number,
                        "meme_id": items[instance_id].get("meme_id"),
                        "category": info["category"],
                        "verdict_split": bool(info["verdict_line"]),
                        # green on the map but still to resolve
                        "hidden": hidden})
    out.sort(key=lambda r: r["number"])
    return out


def task_summary(task: dict, items: dict, by_argument: dict, decisions: dict) -> dict:
    args = task_arguments(task, items, by_argument)
    return {**task, "n": len(args),
            "resolved": sum(1 for r in args
                            if is_settled(decisions.get(r["instance_id"]),
                                          by_argument[r["instance_id"]])),
            "changed": sum(1 for r in args
                           if r["instance_id"] in decisions
                           and changed_since(decisions[r["instance_id"]],
                                             by_argument[r["instance_id"]]))}


# --- gold ---------------------------------------------------------------------

GOLD_COLUMNS = [
    "instance_id", "item_number", "source_index", "meme_id", "image_file",
    "relation", "claim_id",
    "sensible_test", "mechanisms",
    *[export.JUSTIFICATION[m] for m in MECHANISMS],
    "source", "annotators", "category", "resolved_by", "resolved_at", "resolution_note",
    "changed_since_resolved",
]


def gold(items: dict, records: list[dict], decisions: dict) -> list[dict]:
    """One row per argument that is either agreed or resolved, in argument order.

    `justifications` is a list of {mechanism, annotator, text}, the same shape
    on every row. A resolved argument has one per mechanism, chosen or written
    at the meeting, with annotator None. An agreed argument has one per
    mechanism per annotator, since none of theirs is the gold one.
    """
    by_argument = iaa.by_argument(records)
    rows = []
    for instance_id in set(by_argument) | set(decisions):
        item = items.get(instance_id, {})
        base = {k: item.get(k) for k in
                ("source_index", "item_number", "meme_id", "image_file",
                 "relation", "claim_id")}
        per = by_argument.get(instance_id, {})
        decision = decisions.get(instance_id)
        if decision:
            rows.append({
                "instance_id": instance_id, **base,
                "sensible_test": decision["sensible_test"],
                "mechanisms": decision["mechanisms"],
                "justifications": [{"mechanism": m, "annotator": None,
                                    "text": decision["justifications"].get(m)}
                                   for m in decision["mechanisms"]],
                "source": "resolved",
                "annotators": sorted(per),
                "category": decision.get("category"),
                "resolved_by": decision.get("resolved_by"),
                "resolved_at": decision.get("resolved_at"),
                "resolution_note": decision.get("note") or None,
                "verdict_split": len({a.get("sensible_test") for a in
                                      (decision.get("annotations") or {}).values()}) > 1,
                "revision": len(decision.get("history") or []) + 1,
                "changed_since_resolved": changed_since(decision, per),
            })
            continue
        if len(per) < 2:
            continue
        info = header(per)
        if info["needs_review"] or any(r["sensible_test"] is None for r in per.values()):
            continue
        first = next(iter(per.values()))
        mechanisms = [m for m in MECHANISMS if m in iaa.mechanism_set(first["mechanisms"])]
        rows.append({
            "instance_id": instance_id, **base,
            "sensible_test": first["sensible_test"],
            "mechanisms": mechanisms,
            "justifications": [{"mechanism": m, "annotator": who,
                                "text": r["justifications"].get(m)}
                               for m in mechanisms for who, r in sorted(per.items())],
            "source": "agreed",
            "annotators": sorted(per),
            "category": "identical",
            "resolved_by": None, "resolved_at": None, "resolution_note": None,
            "verdict_split": False, "revision": None, "changed_since_resolved": [],
        })
    rows.sort(key=lambda r: (r["item_number"] is None, r["item_number"], r["instance_id"]))
    return rows


def gold_csv_row(row: dict) -> dict:
    out = {k: row.get(k) for k in GOLD_COLUMNS if k in row}
    out["mechanisms"] = "|".join(row["mechanisms"])
    for m in MECHANISMS:
        mine = [j for j in row["justifications"] if j["mechanism"] == m and j["text"]]
        # resolved: the one text; agreed: every annotator's, labelled
        out[export.JUSTIFICATION[m]] = " || ".join(
            j["text"] if j["annotator"] is None else f"{j['annotator']}: {j['text']}"
            for j in mine) or None
    out["annotators"] = "|".join(row["annotators"])
    out["resolved_by"] = "|".join(row["resolved_by"] or [])
    out["changed_since_resolved"] = "|".join(row["changed_since_resolved"])
    return out


# --- progress, for the backend console ----------------------------------------

def progress(records: list[dict], decisions: dict) -> list[dict]:
    """Per pair: how many shared arguments need resolving, and how many are done."""
    by_argument = iaa.by_argument(records)
    pairs: dict = {}
    for instance_id, per in by_argument.items():
        if len(per) < 2:
            continue
        for pair in combinations(sorted(per), 2):
            row = pairs.setdefault(pair, {"pair": list(pair), "shared": 0,
                                          "to_resolve": 0, "resolved": 0, "numbers": []})
            row["shared"] += 1
            number = next(iter(per.values())).get("item_number")
            if number is not None:
                row["numbers"].append(number)
            if header(of_pair(per, pair))["needs_review"]:
                row["to_resolve"] += 1
                if is_settled(decisions.get(instance_id), per):
                    row["resolved"] += 1
    for row in pairs.values():
        row["ranges"] = overlap_ranges(row.pop("numbers"))
    return sorted(pairs.values(), key=lambda r: (-(r["to_resolve"] - r["resolved"]), r["pair"]))
