#!/usr/bin/env python3
"""
Fold already-stored annotations from primary/secondary into the flat scheme.

    python scripts/migrate_flatten_mechanisms.py --dry-run   # say what would change
    python scripts/migrate_flatten_mechanisms.py             # rewrite, keeping a backup

The task used to ask for one `primary_mechanism` plus a `secondary_mechanisms`
multiselect, each with its own justification box. It now asks for one flat
`mechanisms` multiselect and one justification per mechanism ticked. Those are
different scheme names, and Potato keys stored answers by scheme name: without
this, every pre-flatten answer is still on disk and invisible to the annotator,
the admin console, the agreement report and the export.

The mapping, per argument:

    primary_mechanism = M                 ->  mechanisms gains M
    secondary_mechanisms = {A, B}         ->  mechanisms gains A and B
    primary_justification = t             ->  justification_<M> = t
    secondary_justification_<A> = t       ->  justification_<A> = t

Nothing is ranked afterwards, which is the point: the flat scheme has no place
to record which mechanism was the primary one, so that distinction is dropped
here and cannot be recovered from the rewritten file. THE BACKUP IS THE
RECOVERY -- one `.pre-flatten-<stamp>` copy beside each rewritten file, kept
unless you pass --no-backup.

Two cases need a decision rather than a mapping, and both are reported by name:

  * A mechanism recorded as BOTH the primary and a secondary -- the old
    `primary_repeated_as_secondary` violation. It is one ticked mechanism now,
    and its two justifications are joined rather than one of them thrown away.
  * A justification left behind by a mechanism nobody ticked. It is carried
    over as it stands, so the export's `orphaned_justification` still finds it
    and somebody still clears it.

Run it with the study server STOPPED. Potato holds each annotator's state in
process memory and rewrites the whole file on their next save, which would put
the old scheme names straight back.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

SLUG = {m: m.lower().replace(" ", "_") for m in MECHANISMS}
OLD_SECONDARY_JUSTIFICATION = {m: "secondary_justification_" + SLUG[m] for m in MECHANISMS}
NEW_JUSTIFICATION = {m: "justification_" + SLUG[m] for m in MECHANISMS}

OLD_SCHEMES = ({"primary_mechanism", "primary_justification", "secondary_mechanisms"}
               | set(OLD_SECONDARY_JUSTIFICATION.values()))


def as_pairs(raw):
    """The stored form of one argument, as a list of ({schema, name}, value).

    Measured on Potato 2.9.3, an item is on disk as a LIST of
    `[{schema, name}, value]` pairs -- a dict with tuple keys, serialized --
    and as the nested-dict form after Potato's own normalization. Both are
    read; the list form is what is written back, because that is what Potato
    itself writes.
    """
    if isinstance(raw, dict):
        return [({"schema": schema, "name": name}, value)
                for schema, entry in raw.items()
                for name, value in (entry.items() if isinstance(entry, dict)
                                    else [(entry, entry)])]
    out = []
    for entry in raw or []:
        try:
            key, value = entry
            out.append(({"schema": key["schema"], "name": key["name"]}, value))
        except (TypeError, ValueError, KeyError):
            continue
    return out


def text_of(pairs, scheme):
    for key, value in pairs:
        if key["schema"] == scheme:
            return (value or "").strip() or None
    return None


def labels_of(pairs, scheme):
    """Every label recorded under a scheme, in codebook order.

    The name is the label and so is the value -- Potato writes the input's
    `value` attribute, which the generated form sets to the label -- so the
    name is what is read here and the value is not trusted to agree.
    """
    chosen = {key["name"] for key, value in pairs
              if key["schema"] == scheme and value not in (None, "", False)}
    return [m for m in MECHANISMS if m in chosen] + sorted(chosen - set(MECHANISMS))


def flatten(pairs: list) -> tuple[list, dict]:
    """One argument's stored answers, flattened. Returns (pairs, what happened)."""
    if not any(key["schema"] in OLD_SCHEMES for key, _ in pairs):
        return pairs, {}

    primary = (labels_of(pairs, "primary_mechanism") or [None])[0]
    secondaries = labels_of(pairs, "secondary_mechanisms")
    mechanisms = ([primary] if primary else []) + [m for m in secondaries if m != primary]
    mechanisms = [m for m in MECHANISMS if m in mechanisms] + \
                 [m for m in mechanisms if m not in MECHANISMS]

    note = {"mechanisms": mechanisms}

    justifications: dict[str, str] = {}
    if primary:
        primary_text = text_of(pairs, "primary_justification")
        if primary_text:
            justifications[primary] = primary_text
    for mechanism, scheme in OLD_SECONDARY_JUSTIFICATION.items():
        value = text_of(pairs, scheme)
        if not value:
            continue
        if mechanism in justifications and justifications[mechanism] != value:
            # The same mechanism was the primary AND a secondary, each with its
            # own justification. One ticked mechanism now, so both texts go in
            # its one box: throwing either away would be throwing away what an
            # annotator wrote.
            justifications[mechanism] = justifications[mechanism] + " — " + value
            note.setdefault("joined_justifications", []).append(mechanism)
        else:
            justifications[mechanism] = value
        if mechanism not in mechanisms:
            note.setdefault("orphaned_justifications", []).append(mechanism)

    # A pre-flatten justification for a mechanism nobody ticked is kept, under
    # the flat name, so the export still reports it and somebody still clears
    # it. A slug with no mechanism behind it (a renamed label) is left alone.
    out = [(key, value) for key, value in pairs
           if key["schema"] not in OLD_SCHEMES]
    for mechanism in mechanisms:
        out.append(({"schema": "mechanisms", "name": mechanism}, mechanism))
    for mechanism, value in justifications.items():
        scheme = NEW_JUSTIFICATION.get(mechanism)
        if scheme:
            out.append(({"schema": scheme, "name": "text_box"}, value))
    return out, note


def migrate_state(state: dict) -> tuple[dict, list]:
    changed = []
    stored = state.get("instance_id_to_label_to_value") or {}
    for instance_id, raw in list(stored.items()):
        pairs = as_pairs(raw)
        new_pairs, note = flatten(pairs)
        if not note:
            continue
        stored[instance_id] = [[key, value] for key, value in new_pairs]
        changed.append({"instance_id": instance_id, **note})
    return state, changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task-dir", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would change and write nothing")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not keep a .pre-flatten copy of each rewritten file")
    args = ap.parse_args()

    output = Path(args.task_dir) / "annotation_output"
    if not output.exists():
        raise SystemExit(f"no annotation_output/ under {args.task_dir}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    total_files = total_items = 0

    for state_path in sorted(output.glob("*/user_state.json")):
        annotator = state_path.parent.name
        state = json.loads(state_path.read_text())
        state, changed = migrate_state(state)
        if not changed:
            print(f"  {annotator:<16} nothing to flatten")
            continue

        total_files += 1
        total_items += len(changed)
        print(f"  {annotator:<16} {len(changed)} argument(s)")
        for entry in changed[:5]:
            mechanisms = ", ".join(entry["mechanisms"]) or "none"
            print(f"      {entry['instance_id']:<24} -> {mechanisms}")
        if len(changed) > 5:
            print(f"      ... and {len(changed) - 5} more")
        for entry in changed:
            for mechanism in entry.get("joined_justifications", []):
                print(f"      JOINED   {entry['instance_id']}: {mechanism} was both "
                      "primary and secondary; its two justifications are now one")
            for mechanism in entry.get("orphaned_justifications", []):
                print(f"      ORPHAN   {entry['instance_id']}: {mechanism} is not "
                      "ticked but its justification was kept")

        if args.dry_run:
            continue
        if not args.no_backup:
            backup = state_path.with_suffix(f".json.pre-flatten-{stamp}")
            shutil.copy2(state_path, backup)
        state_path.write_text(json.dumps(state, ensure_ascii=False))

    if args.dry_run:
        print(f"\ndry run: {total_items} argument(s) across {total_files} annotator(s) "
              "would be flattened; nothing written")
    elif total_files:
        print(f"\nflattened {total_items} argument(s) across {total_files} annotator(s)"
              + ("" if args.no_backup else f"; backups are *.pre-flatten-{stamp}"))
    else:
        print("\nnothing to flatten; every stored answer is already on the flat schemes")
    return 0


# --- self-test ----------------------------------------------------------------
#
# The mapping is four lines of dictionary work over a file nobody can
# reconstruct if it goes wrong, so the four shapes it has to handle are written
# down and checked rather than reasoned about.

SELF_TEST_CASES = [
    ("a pass, untouched",
     [({"schema": "sensible_test_failed", "name": "No"}, "No")],
     []),
    ("primary only",
     [({"schema": "sensible_test_failed", "name": "Yes"}, "Yes"),
      ({"schema": "primary_mechanism", "name": "Affective Hijacking"}, "Affective Hijacking"),
      ({"schema": "primary_justification", "name": "text_box"}, "Child #5 carries the claim.")],
     [("mechanisms", "Affective Hijacking", "Affective Hijacking"),
      ("justification_affective_hijacking", "text_box", "Child #5 carries the claim.")]),
    ("primary and two secondaries",
     [({"schema": "primary_mechanism", "name": "Esoteric Code Activation"}, "Esoteric Code Activation"),
      ({"schema": "primary_justification", "name": "text_box"}, "Only lands for an in-group reader."),
      ({"schema": "secondary_mechanisms", "name": "Affective Hijacking"}, "Affective Hijacking"),
      ({"schema": "secondary_mechanisms", "name": "Satirical Trivialization"}, "Satirical Trivialization"),
      ({"schema": "secondary_justification_affective_hijacking", "name": "text_box"}, "The expression supplies contempt."),
      ({"schema": "secondary_justification_satirical_trivialization", "name": "text_box"}, "The caption invites dismissal.")],
     [("mechanisms", "Affective Hijacking", "Affective Hijacking"),
      ("mechanisms", "Satirical Trivialization", "Satirical Trivialization"),
      ("mechanisms", "Esoteric Code Activation", "Esoteric Code Activation"),
      ("justification_esoteric_code_activation", "text_box", "Only lands for an in-group reader."),
      ("justification_affective_hijacking", "text_box", "The expression supplies contempt."),
      ("justification_satirical_trivialization", "text_box", "The caption invites dismissal.")]),
    ("the same mechanism twice",
     [({"schema": "primary_mechanism", "name": "Affective Hijacking"}, "Affective Hijacking"),
      ({"schema": "primary_justification", "name": "text_box"}, "As the primary."),
      ({"schema": "secondary_mechanisms", "name": "Affective Hijacking"}, "Affective Hijacking"),
      ({"schema": "secondary_justification_affective_hijacking", "name": "text_box"}, "And again as a secondary.")],
     [("mechanisms", "Affective Hijacking", "Affective Hijacking"),
      ("justification_affective_hijacking", "text_box", "As the primary. — And again as a secondary.")]),
    ("an orphaned secondary justification",
     [({"schema": "primary_mechanism", "name": "Affective Hijacking"}, "Affective Hijacking"),
      ({"schema": "primary_justification", "name": "text_box"}, "Child #5 carries the claim."),
      ({"schema": "secondary_justification_satirical_trivialization", "name": "text_box"}, "Left behind after un-ticking.")],
     [("mechanisms", "Affective Hijacking", "Affective Hijacking"),
      ("justification_affective_hijacking", "text_box", "Child #5 carries the claim."),
      ("justification_satirical_trivialization", "text_box", "Left behind after un-ticking.")]),
]


def self_test() -> int:
    failures = 0
    for name, pairs, expected_new in SELF_TEST_CASES:
        out, note = flatten(list(pairs))
        kept = [(k["schema"], k["name"], v) for k, v in out
                if k["schema"] not in {"mechanisms"} | set(NEW_JUSTIFICATION.values())]
        new = [(k["schema"], k["name"], v) for k, v in out
               if k["schema"] in {"mechanisms"} | set(NEW_JUSTIFICATION.values())]
        untouched = [(k["schema"], k["name"], v) for k, v in pairs
                     if k["schema"] not in OLD_SCHEMES]
        ok = new == expected_new and kept == untouched
        if not expected_new:
            ok = ok and not note
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'BAD '} {name}")
        if not ok:
            print(f"        got      {new}")
            print(f"        expected {expected_new}")
            if kept != untouched:
                print(f"        other answers changed: {kept} != {untouched}")
    print(f"\n{len(SELF_TEST_CASES) - failures}/{len(SELF_TEST_CASES)} "
          "shapes migrated as specified")
    return 1 if failures else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
