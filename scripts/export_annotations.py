#!/usr/bin/env python3
"""
Export VLMD Subtask 1 annotations to the downstream CSV and JSONL, and check
the constraints Potato cannot enforce on its own.

    python scripts/export_annotations.py
    python scripts/export_annotations.py --strict      # exit 1 on any error

Reads annotation_output/<user>/user_state.json directly rather than Potato's own
csv/jsonl export, for three reasons:

  * Potato's tabular export is one sparse column per scheme-AND-LABEL --
    `sensible_test_failed.Yes` with the value "Yes", and no
    `sensible_test_failed` column anywhere. Nothing downstream wants that shape.
  * It carries `instance_id` and `user_id` and NO item fields, so the meme,
    the relation and the claim id are not in it at all.
  * It has no timestamps. Those live in `instance_id_to_behavioral_data`.

Outputs, into annotation_output/export/:

    annotations.csv        one row per (argument, annotator)
    annotations.jsonl      the same, in the downstream schema
    validation_report.json every constraint violation, with ids

`mechanisms` is `[]` and `justifications` is `{}` on an argument that passed
the Sensible Test. Never the string "None", in any casing: the absent case is
written from the ABSENCE of the key in the stored state, so there is no code
path that can produce a placeholder string.

The mechanisms are FLAT. There is no primary and no secondary: one list, every
mechanism at work in the argument in it, nothing ranking them. A pre-flatten
export carried `primary_mechanism`, `primary_justification`,
`secondary_mechanisms` and `secondary_justifications`; those columns are gone,
and `scripts/migrate_flatten_mechanisms.py` is what folds old stored answers
into the flat scheme names.

Annotators can leave an argument part-answered and come back to it, so a draft
reaches this script like anything else. `incomplete_sensible_test` is what
catches one, and `--strict` is what refuses to hand over a study with drafts
still in it.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The Sensible Test is one question on screen -- "Does this argument fail the
# Sensible Test?", Yes or No -- and `sensible_test` downstream is Pass/Fail.
# This is the whole of the translation between the two namings.
VERDICT = {"Yes": "Fail", "No": "Pass"}

MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

# scheme name <- mechanism label. One justification per mechanism, gated in
# config.yaml on that mechanism being ticked.
JUSTIFICATION = {
    m: "justification_" + m.lower().replace(" ", "_") for m in MECHANISMS
}

# A justification that is present but says nothing. Compared case-folded, with
# surrounding punctuation and whitespace stripped.
NULL_ANSWERS = {
    "", "none", "n/a", "na", "nil", "nothing", "no", "-", "--", "?", "??",
    "idk", "i don't know", "i dont know", "unknown", "unsure", "same",
    "same as above", "as above", "see above", "ditto", "tbd", "todo", "x",
}

MIN_JUSTIFICATION_CHARS = 15


def normalize_stored(raw):
    """
    `instance_id_to_label_to_value[<id>]` as a {scheme: {label: value}} dict.

    Measured on Potato 2.9.3, an item is stored on disk as a LIST of
    `[{schema, name}, value]` pairs -- a dict with tuple keys, serialized. The
    documentation and the tabular exporter both describe the nested-dict form,
    which is what exists after Potato's own normalization. A script reading the
    file straight off disk gets the list, so handle both.
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


def single(labels: dict, scheme: str):
    """The one label a radio recorded, or None when the scheme is absent."""
    entry = labels.get(scheme)
    if not entry:
        return None
    if isinstance(entry, dict):
        keys = [k for k, v in entry.items() if v not in (None, "", False)]
        return keys[0] if keys else None
    return entry or None


def multi(labels: dict, scheme: str) -> list[str]:
    """Every label a multiselect recorded, in the config's order."""
    entry = labels.get(scheme)
    if not entry:
        return []
    if isinstance(entry, dict):
        chosen = {k for k, v in entry.items() if v not in (None, "", False)}
    else:
        chosen = {entry}
    ordered = [m for m in MECHANISMS if m in chosen]
    return ordered + sorted(chosen - set(ordered))


def text(labels: dict, scheme: str):
    """A text scheme's value. Stored under the label name `text_box`."""
    entry = labels.get(scheme)
    if not entry:
        return None
    if isinstance(entry, dict):
        value = entry.get("text_box") or next(iter(entry.values()), None)
    else:
        value = entry
    value = (value or "").strip()
    return value or None


def is_empty_in_spirit(value) -> bool:
    if value is None:
        return True
    stripped = value.strip().strip(".,;:!-–—_*\"'()[] ").casefold()
    return stripped in NULL_ANSWERS


def derive_sensible_test(answer: str | None) -> str | None:
    """Yes is a Fail, No is a Pass, unanswered is undecided.

    Translated here and nowhere else. The question is asked in the annotator's
    direction -- "does this argument FAIL the test" -- because that is the
    direction the guidelines read in, and Pass/Fail is the direction the brief's
    spreadsheet reads in. An unrecognised answer is undecided rather than a
    guess: a renamed label should show up as a draft, not as a Pass.
    """
    return VERDICT.get(answer)


def timestamps(behavioral: dict, instance_id: str) -> dict:
    data = (behavioral or {}).get(instance_id) or {}
    events = [e.get("timestamp") for e in data.get("interactions", [])
              if isinstance(e.get("timestamp"), (int, float))]
    start = data.get("session_start") or (min(events) if events else None)
    end = data.get("session_end") or (max(events) if events else None)

    def iso(value):
        if not value:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat(timespec="seconds")

    return {
        "first_seen_utc": iso(start),
        "last_saved_utc": iso(end),
        "time_on_item_ms": data.get("total_time_ms") or (
            int((end - start) * 1000) if start and end else None),
    }


def collect(task_dir: Path, items: dict) -> tuple[list[dict], list[dict]]:
    records, problems = [], []
    output = task_dir / "annotation_output"
    if not output.exists():
        raise SystemExit(f"no annotation_output/ under {task_dir}; nobody has annotated yet")

    for state_path in sorted(output.glob("*/user_state.json")):
        annotator = state_path.parent.name
        state = json.loads(state_path.read_text())
        behavioral = state.get("instance_id_to_behavioral_data", {})

        for instance_id, raw in (state.get("instance_id_to_label_to_value") or {}).items():
            labels = normalize_stored(raw)
            if not labels:
                continue

            item = items.get(instance_id, {})
            if not item:
                problems.append({
                    "kind": "unknown_instance",
                    "instance_id": instance_id,
                    "annotator": annotator,
                    "detail": "annotated id is not in data/instances.json — the data "
                              "was rebuilt and this id no longer exists",
                })

            failed = single(labels, "sensible_test_failed")
            verdict = derive_sensible_test(failed)
            mechanisms = multi(labels, "mechanisms")
            justifications = {
                m: text(labels, JUSTIFICATION[m]) for m in MECHANISMS
                if text(labels, JUSTIFICATION[m]) is not None
            }

            record = {
                "instance_id": instance_id,
                "meme_id": item.get("meme_id"),
                "image_file": item.get("image_file"),
                "relation": item.get("relation"),
                "claim_id": item.get("claim_id"),
                # The argument's place in args_0911.json, which is also the
                # number shown on screen and the number people quote when they
                # hand work over. 0-based and 1-based, because both get asked
                # for and deriving one from the other in a spreadsheet is how
                # off-by-one errors happen.
                "source_index": item.get("source_index"),
                "item_number": item.get("item_number"),
                "annotator_id": annotator,
                "sensible_test": verdict,
                "mechanisms": mechanisms,
                "justifications": justifications,
                "notes": text(labels, "notes"),
                "flag_for_discussion": bool(multi(labels, "flag_for_discussion")),
                **timestamps(behavioral, instance_id),
            }
            records.append(record)
            problems.extend(validate(record))

    # Reading order, so the CSV reads in the same order as the study: memes in
    # args_0911.json order, each meme bottom up. `source_index` is the line in
    # args_0911.json and is a column, so the file can be sorted back. An
    # orphaned id -- annotated, then removed by a rebuild -- has no number, so
    # it sorts to the end rather than to the front.
    records.sort(key=lambda r: (r["item_number"] is None, r["item_number"],
                                r["instance_id"], r["annotator_id"]))
    return records, problems


def validate(r: dict) -> list[dict]:
    """
    The constraints from the brief, plus the ones that follow from them.

    Potato enforces NONE of these. What it does enforce is that a hidden scheme
    records nothing, which is why the last one has never been violated in
    practice -- the absence is structural, not checked here.

    Flattening the mechanisms retired one check outright: the same mechanism
    could once be recorded as both the primary and a secondary, and a single
    list of checkboxes cannot hold a label twice.
    """
    out = []
    where = {"instance_id": r["instance_id"], "annotator": r["annotator_id"]}

    def problem(kind, detail):
        out.append({"kind": kind, **where, "detail": detail})

    if r["sensible_test"] is None:
        problem("incomplete_sensible_test",
                "the Sensible Test question was not answered")

    # Constraint 1 — every ticked mechanism carries a justification, and that
    # justification says something. The floor is the same one config.yaml's
    # `min_chars` draws on screen and scripts/serve.py holds Submit against, so
    # a justification that got past the page gets past this too.
    for mechanism in r["mechanisms"]:
        justification = r["justifications"].get(mechanism)
        if is_empty_in_spirit(justification):
            problem("empty_justification",
                    f"{mechanism!r} selected with justification {justification!r}")
        elif len(justification) < MIN_JUSTIFICATION_CHARS:
            problem("short_justification",
                    f"{mechanism!r}: {len(justification)} chars, under "
                    f"{MIN_JUSTIFICATION_CHARS}")

    # A justification left behind by a mechanism that was later un-ticked.
    # `display_logic` is not transitive: a justification's own gate reads only
    # the mechanisms multiselect, so un-ticking the mechanism hides the box but
    # keeps the text that was in it.
    for mechanism, justification in r["justifications"].items():
        if mechanism not in r["mechanisms"] and not is_empty_in_spirit(justification):
            problem("orphaned_justification",
                    f"{mechanism!r} is not ticked but its justification holds "
                    f"{justification[:60]!r}")

    # Constraint 2 — a pass carries no mechanism; a fail carries at least one.
    if r["sensible_test"] == "Pass" and r["mechanisms"]:
        problem("mechanism_on_a_passing_item",
                f"Sensible Test passed but {r['mechanisms']} are ticked")
    if r["sensible_test"] == "Fail" and not r["mechanisms"]:
        problem("no_mechanism_on_a_failing_item",
                "the Sensible Test came back failed but no mechanism was ticked")

    return out


# One `mechanisms` column holding the whole set, pipe-joined in codebook order,
# and one justification column per mechanism beside it. A row with two
# mechanisms is one row with two justifications filled in -- not two rows, and
# not a ranking.
CSV_COLUMNS = [
    "instance_id", "item_number", "source_index", "meme_id", "image_file",
    "relation", "claim_id",
    "annotator_id",
    "sensible_test",
    "mechanisms",
    *[JUSTIFICATION[m] for m in MECHANISMS],
    "notes", "flag_for_discussion",
    "first_seen_utc", "last_saved_utc", "time_on_item_ms",
]


def csv_row(r: dict) -> dict:
    row = {k: r.get(k) for k in CSV_COLUMNS if k in r}
    row["mechanisms"] = "|".join(r["mechanisms"])
    for mechanism in MECHANISMS:
        row[JUSTIFICATION[mechanism]] = r["justifications"].get(mechanism)
    row["flag_for_discussion"] = "true" if r["flag_for_discussion"] else "false"
    return row


SELF_TEST_CASES = [
    ("a clean fail, one mechanism", "empty_justification", False, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "Child #5's described expression carries the claim."}}),
    ("a clean fail, two mechanisms", "empty_justification", False, {
        "sensible_test": "Fail",
        "mechanisms": ["Affective Hijacking", "Esoteric Code Activation"],
        "justifications": {
            "Affective Hijacking": "Child #5's described expression carries the claim.",
            "Esoteric Code Activation": "Only lands for a reader holding the prior in #14."}}),
    ("justification says None", "empty_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "None"}}),
    ("justification says NONE.", "empty_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "NONE."}}),
    ("justification is a dash", "empty_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "-"}}),
    ("justification too short", "short_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "emotive appeal"}}),
    ("mechanism with no justification", "empty_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Satirical Trivialization"]}),
    ("second mechanism with no justification", "empty_justification", True, {
        "sensible_test": "Fail",
        "mechanisms": ["Affective Hijacking", "Satirical Trivialization"],
        "justifications": {
            "Affective Hijacking": "Child #5's described expression carries the claim."}}),
    ("orphaned justification", "orphaned_justification", True, {
        "sensible_test": "Fail", "mechanisms": ["Affective Hijacking"],
        "justifications": {
            "Affective Hijacking": "Child #5's described expression carries the claim.",
            "Satirical Trivialization": "Text left behind after un-ticking it."}}),
    ("mechanism on a passing item", "mechanism_on_a_passing_item", True, {
        "sensible_test": "Pass", "mechanisms": ["Affective Hijacking"],
        "justifications": {"Affective Hijacking": "Child #5's described expression carries the claim."}}),
    ("failing item with no mechanism", "no_mechanism_on_a_failing_item", True, {
        "sensible_test": "Fail"}),
    ("a clean pass", "mechanism_on_a_passing_item", False, {"sensible_test": "Pass"}),
    ("unanswered Sensible Test", "incomplete_sensible_test", True,
     {"sensible_test": None}),
]


def self_test() -> int:
    """Prove each constraint fires, and that a clean record trips none of them.

    A validator nobody has watched fail is not evidence of anything.
    """
    base = {
        "instance_id": "selftest::0", "annotator_id": "selftest",
        "sensible_test": "Pass",
        "mechanisms": [], "justifications": {},
        "notes": None, "flag_for_discussion": False,
    }
    failures = 0
    for name, kind, should_fire, patch in SELF_TEST_CASES:
        record = {**base, **patch}
        kinds = {p["kind"] for p in validate(record)}
        fired = kind in kinds
        ok = fired is should_fire
        failures += 0 if ok else 1
        verb = "fires" if should_fire else "stays quiet"
        print(f"  {'ok  ' if ok else 'BAD '} {name:<38} {kind} {verb}"
              + ("" if ok else f"   (actually raised {sorted(kinds) or 'nothing'})"))
    print(f"\n{len(SELF_TEST_CASES) - failures}/{len(SELF_TEST_CASES)} checks behaved as specified")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="Run the validator against synthetic violations and exit")
    ap.add_argument("--task-dir", default=str(ROOT))
    ap.add_argument("--instances", default=str(ROOT / "data" / "instances.json"))
    ap.add_argument("--out-dir", default=str(ROOT / "annotation_output" / "export"))
    ap.add_argument("--strict", action="store_true", help="exit 1 if anything failed validation")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    items = {i["id"]: i for i in json.loads(Path(args.instances).read_text())}
    records, problems = collect(Path(args.task_dir), items)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl = out_dir / "annotations.jsonl"
    with jsonl.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    csv_path = out_dir / "annotations.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow(csv_row(r))

    by_kind: dict[str, int] = {}
    for p in problems:
        by_kind[p["kind"]] = by_kind.get(p["kind"], 0) + 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_records": len(records),
        "n_annotators": len({r["annotator_id"] for r in records}),
        "n_arguments": len({r["instance_id"] for r in records}),
        "n_problems": len(problems),
        "problems_by_kind": by_kind,
        "verdicts": {
            v: sum(1 for r in records if r["sensible_test"] == v)
            for v in ("Pass", "Fail", None)
        },
        "problems": problems,
    }
    (out_dir / "validation_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))

    print(f"{len(records)} annotation(s) by {report['n_annotators']} annotator(s) "
          f"over {report['n_arguments']} argument(s)")
    print(f"  Pass {report['verdicts']['Pass']}   Fail {report['verdicts']['Fail']}   "
          f"undecided {report['verdicts'][None]}")
    print(f"\nwrote {csv_path}")
    print(f"wrote {jsonl}")
    print(f"wrote {out_dir / 'validation_report.json'}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for kind, n in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  {kind}")
        for p in problems[:10]:
            print(f"    {p['instance_id']} / {p['annotator']}: {p['detail']}")
        if len(problems) > 10:
            print(f"    ... and {len(problems) - 10} more, all in validation_report.json")
    else:
        print("\nno constraint violations")

    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
