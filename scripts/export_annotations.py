#!/usr/bin/env python3
"""
Export VLMD Subtask 1 annotations to the downstream CSV and JSONL, and check
the three constraints Potato cannot enforce on its own.

    python scripts/export_annotations.py
    python scripts/export_annotations.py --strict      # exit 1 on any error

Reads annotation_output/<user>/user_state.json directly rather than Potato's own
csv/jsonl export, for three reasons:

  * Potato's tabular export is one sparse column per scheme-AND-LABEL --
    `manipulation_check.Yes` with the value "Yes", and no `manipulation_check`
    column anywhere. Nothing downstream wants that shape.
  * It carries `instance_id` and `user_id` and NO item fields, so the meme,
    the relation and the claim id are not in it at all.
  * It has no timestamps. Those live in `instance_id_to_behavioral_data`.

Outputs, into annotation_output/export/:

    annotations.csv        one row per (argument, annotator)
    annotations.jsonl      the same, in the downstream schema
    validation_report.json every constraint violation, with ids

`primary_mechanism` is `null` and `secondary_mechanisms` is `[]` on an argument
that passed the Sensible Test. Never the string "None", in any casing: the
absent case is written from the ABSENCE of the key in the stored state, so
there is no code path that can produce a placeholder string.

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

CHECKS = ["manipulation_check", "deception_check", "rhetorical_wrapper_check"]

MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

# scheme name <- mechanism label
SECONDARY_JUSTIFICATION = {
    m: "secondary_justification_" + m.lower().replace(" ", "_") for m in MECHANISMS
}

# A justification that is present but says nothing. Compared case-folded, with
# surrounding punctuation and whitespace stripped.
NULL_ANSWERS = {
    "", "none", "n/a", "na", "nil", "nothing", "no", "-", "--", "?", "??",
    "idk", "i don't know", "i dont know", "unknown", "unsure", "same",
    "same as above", "as above", "see above", "ditto", "tbd", "todo", "x",
}

MIN_JUSTIFICATION_CHARS = 25


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


def derive_sensible_test(checks: dict) -> str | None:
    """All three No is a Pass; any Yes is a Fail; anything missing is undecided.

    Derived here and nowhere else. The brief's spreadsheet asked annotators for
    a single Pass/Fail and the guidelines contradicted themselves about which
    direction it ran, so the task does not collect it at all.
    """
    values = [checks.get(c) for c in CHECKS]
    if any(v is None for v in values):
        return None
    return "Fail" if any(v == "Yes" for v in values) else "Pass"


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

            checks = {c: single(labels, c) for c in CHECKS}
            verdict = derive_sensible_test(checks)
            primary = single(labels, "primary_mechanism")
            secondaries = multi(labels, "secondary_mechanisms")
            secondary_justifications = {
                m: text(labels, SECONDARY_JUSTIFICATION[m]) for m in MECHANISMS
                if text(labels, SECONDARY_JUSTIFICATION[m]) is not None
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
                "manipulation_check": checks["manipulation_check"],
                "deception_check": checks["deception_check"],
                "rhetorical_wrapper_check": checks["rhetorical_wrapper_check"],
                "sensible_test": verdict,
                "primary_mechanism": primary,
                "primary_justification": text(labels, "primary_justification"),
                "secondary_mechanisms": secondaries,
                "secondary_justifications": secondary_justifications,
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
    The three constraints from the brief, plus the ones that follow from them.

    Potato enforces NONE of these three. What it does enforce is that a hidden
    scheme records nothing, which is why the third one has never been violated
    in practice -- the absence is structural, not checked here.
    """
    out = []
    where = {"instance_id": r["instance_id"], "annotator": r["annotator_id"]}

    def problem(kind, detail):
        out.append({"kind": kind, **where, "detail": detail})

    if r["sensible_test"] is None:
        problem("incomplete_sensible_test",
                f"checks are {[r[c] for c in CHECKS]}; at least one was not answered")

    # Constraint 1 — the same mechanism cannot be both primary and secondary.
    if r["primary_mechanism"] and r["primary_mechanism"] in r["secondary_mechanisms"]:
        problem("primary_repeated_as_secondary",
                f"{r['primary_mechanism']!r} is recorded as both primary and secondary")

    # Constraint 2 — a justification cannot be empty or a placeholder.
    if r["primary_mechanism"]:
        if is_empty_in_spirit(r["primary_justification"]):
            problem("empty_primary_justification",
                    f"primary_justification is {r['primary_justification']!r}")
        elif len(r["primary_justification"]) < MIN_JUSTIFICATION_CHARS:
            problem("short_primary_justification",
                    f"{len(r['primary_justification'])} chars, under "
                    f"{MIN_JUSTIFICATION_CHARS}")

    for mechanism in r["secondary_mechanisms"]:
        justification = r["secondary_justifications"].get(mechanism)
        if is_empty_in_spirit(justification):
            problem("empty_secondary_justification",
                    f"{mechanism!r} selected with justification {justification!r}")

    # A justification left behind by a mechanism that was later un-ticked.
    # `display_logic` is not transitive: a secondary justification's own gate
    # reads only its multiselect, so un-ticking the mechanism hides the box but
    # keeps the text that was in it.
    for mechanism, justification in r["secondary_justifications"].items():
        if mechanism not in r["secondary_mechanisms"] and not is_empty_in_spirit(justification):
            problem("orphaned_secondary_justification",
                    f"{mechanism!r} is not selected but its justification holds "
                    f"{justification[:60]!r}")

    # Constraint 3 — a pass carries no mechanism; a fail carries one.
    if r["sensible_test"] == "Pass":
        leaked = [k for k in ("primary_mechanism", "primary_justification") if r[k]]
        if leaked or r["secondary_mechanisms"]:
            problem("mechanism_on_a_passing_item",
                    f"Sensible Test passed but {leaked + (['secondary_mechanisms'] if r['secondary_mechanisms'] else [])} are filled")
    if r["sensible_test"] == "Fail" and not r["primary_mechanism"]:
        problem("no_mechanism_on_a_failing_item",
                "a check came back Yes but no primary mechanism was recorded")

    return out


CSV_COLUMNS = [
    "instance_id", "item_number", "source_index", "meme_id", "image_file",
    "relation", "claim_id",
    "annotator_id",
    "manipulation_check", "deception_check", "rhetorical_wrapper_check",
    "sensible_test",
    "primary_mechanism", "primary_justification",
    "secondary_mechanisms",
    *[SECONDARY_JUSTIFICATION[m] for m in MECHANISMS],
    "notes", "flag_for_discussion",
    "first_seen_utc", "last_saved_utc", "time_on_item_ms",
]


def csv_row(r: dict) -> dict:
    row = {k: r.get(k) for k in CSV_COLUMNS if k in r}
    row["secondary_mechanisms"] = "|".join(r["secondary_mechanisms"])
    for mechanism in MECHANISMS:
        row[SECONDARY_JUSTIFICATION[mechanism]] = r["secondary_justifications"].get(mechanism)
    row["flag_for_discussion"] = "true" if r["flag_for_discussion"] else "false"
    return row


SELF_TEST_CASES = [
    ("a clean fail", "primary_repeated_as_secondary", False, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "Child #5's described expression carries the claim.",
        "secondary_mechanisms": ["Esoteric Code Activation"],
        "secondary_justifications": {"Esoteric Code Activation": "Only lands for a reader holding the prior in #14."}}),
    ("primary repeated as secondary", "primary_repeated_as_secondary", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "Child #5's described expression carries the claim.",
        "secondary_mechanisms": ["Affective Hijacking"],
        "secondary_justifications": {"Affective Hijacking": "The same thing again, at length."}}),
    ("justification says None", "empty_primary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "None"}),
    ("justification says NONE.", "empty_primary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "NONE."}),
    ("justification is a dash", "empty_primary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "-"}),
    ("justification too short", "short_primary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "emotional appeal"}),
    ("secondary with no justification", "empty_secondary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "Child #5's described expression carries the claim.",
        "secondary_mechanisms": ["Satirical Trivialization"]}),
    ("orphaned secondary justification", "orphaned_secondary_justification", True, {
        "sensible_test": "Fail", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "Child #5's described expression carries the claim.",
        "secondary_justifications": {"Satirical Trivialization": "Text left behind after un-ticking it."}}),
    ("mechanism on a passing item", "mechanism_on_a_passing_item", True, {
        "sensible_test": "Pass", "primary_mechanism": "Affective Hijacking",
        "primary_justification": "Child #5's described expression carries the claim."}),
    ("failing item with no mechanism", "no_mechanism_on_a_failing_item", True, {
        "sensible_test": "Fail"}),
    ("a clean pass", "mechanism_on_a_passing_item", False, {"sensible_test": "Pass"}),
    ("unanswered check", "incomplete_sensible_test", True, {"sensible_test": None}),
]


def self_test() -> int:
    """Prove each constraint fires, and that a clean record trips none of them.

    A validator nobody has watched fail is not evidence of anything.
    """
    base = {
        "instance_id": "selftest::0", "annotator_id": "selftest",
        "manipulation_check": "No", "deception_check": "No",
        "rhetorical_wrapper_check": "No", "sensible_test": "Pass",
        "primary_mechanism": None, "primary_justification": None,
        "secondary_mechanisms": [], "secondary_justifications": {},
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
