#!/usr/bin/env python3
"""
Load annotations done outside the study into annotation_output/, as if they had
been typed on the site.

    python scripts/import_annotations.py data/VLMD_Subtask1_Week2_flattened.csv --dry-run
    python scripts/import_annotations.py data/VLMD_Subtask1_Week2_flattened.csv

Each annotator becomes annotation_output/<name>/user_state.json in the shape
Potato writes itself, so everything downstream -- the study server, the console,
the export, the agreement report, the Postgres mirror -- sees ordinary work by
an ordinary annotator. Nothing here is special-cased anywhere else.

THE HARD PART IS THE ID, and it is the reason this is a script and not a
one-liner. The spreadsheet names an argument by meme image and claim id, and
that pair IS NOT UNIQUE: the same claim is sometimes argued twice from disjoint
children, which is exactly the collision DESIGN.md describes under *The data*.
Fourteen rows in this file collide. They are separated by their children, and a
row that still cannot be pinned to exactly one argument is a hard error -- the
alternative is writing somebody's annotation onto the wrong argument, silently,
which no later check would catch.

Row order is not trusted either. The spreadsheet lists a meme's claims in its
own order and the corpus serves them bottom up, and the two disagree on several
memes, so nothing here maps by position.

WHAT AN IMPORTED ANNOTATION DOES NOT HAVE is timestamps. They come from
`instance_id_to_behavioral_data`, which is a record of somebody typing, and
inventing one would put a fabricated number in the export's `first_seen_utc`.
So imported work shows an empty time column, in the console and in the CSV.

PRE-FLATTEN COLUMNS. The sheet carries `primary_mechanism` and
`secondary_mechanisms` from before the mechanisms were flattened, and a
`mechanisms` column that is their union. The union is what gets written -- it
is checked against the other two on every row -- and the justifications follow
their mechanism: the primary's text into the primary's box, the secondary text
into each secondary's box. Where one justification covers several secondaries
it is written into each of their boxes, because that one sentence is what the
annotator wrote about all of them.

RUN IT WITH THE STUDY SERVER STOPPED. Potato holds each annotator's state in
memory and rewrites the whole file on their next save.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
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
CANONICAL = {m.casefold(): m for m in MECHANISMS}
JUSTIFICATION = {m: "justification_" + m.lower().replace(" ", "_") for m in MECHANISMS}

# "Yes, it failed" on screen is "Fail" in the sheet. The same translation as
# scripts/export_annotations.py, read the other way round.
VERDICT = {"Fail": "Yes", "Pass": "No"}

# A claim reference in the sheet is written "7. Obama is gay...", and the same
# child in the corpus is just the sentence.
CLAIM_PREFIX = re.compile(r"^[A-Za-z]?\d+\.\s*")


def names(value: str) -> list:
    """A mechanism cell as a list of codebook names, "None" dropped."""
    out = []
    for part in (value or "").replace(";", ",").split(","):
        name = part.strip()
        if not name or name.casefold() == "none":
            continue
        canonical = CANONICAL.get(name.casefold())
        if canonical is None:
            raise SystemExit(f"unknown mechanism {name!r} in the spreadsheet")
        if canonical not in out:
            out.append(canonical)
    return out


def fingerprint(text: str) -> set:
    """A children cell as a set of comparable openings.

    Compared on the first 40 characters, case-folded: the sheet writes a claim
    child as "- 7. Obama is gay and his wife is a man" and the corpus holds
    "Obama is gay and his wife is a man", and the sheet truncates nothing the
    corpus does not. Enough to tell two argument of the same claim apart, which
    is all this is for.
    """
    out = set()
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-").strip()
        line = CLAIM_PREFIX.sub("", line).strip().strip('"').casefold()
        if line and line != "(background knowledge only)":
            out.add(line[:40])
    return out


def corpus_index(instances: list) -> dict:
    """(meme_id, claim_id) -> [instance, ...], in served order."""
    index: dict = {}
    for item in sorted(instances, key=lambda i: int(i["item_number"])):
        index.setdefault((item["meme_id"], str(item["claim_id"])), []).append(item)
    return index


def child_text(item: dict) -> set:
    return {str(c.get("content", "")).strip().strip('"').casefold()[:40]
            for c in item.get("children", [])}


def resolve(rows: list, index: dict) -> tuple:
    """Every row's instance id, or the reasons some row has none.

    One annotator's rows at a time, so the "already taken" check is per
    annotator: two annotators answering the same argument is the whole point of
    the study, and two rows of ONE annotator landing on one argument is the
    error this exists to catch.
    """
    resolved: dict = {}
    problems: list = []
    groups: dict = {}
    for row in rows:
        stem = row["image"].rsplit(".", 1)[0]
        claim_id = row["claim_id"].strip()
        claim_id = claim_id[1:] if claim_id[:1].upper() == "C" else claim_id
        groups.setdefault((stem, claim_id), []).append(row)

    for key, group in groups.items():
        candidates = index.get(key, [])
        if not candidates:
            problems.append(f"{key[0]} claim {key[1]}: no such argument in the corpus")
            continue
        if len(group) == 1 and len(candidates) == 1:
            resolved[id(group[0])] = candidates[0]
            continue
        if len(group) != len(candidates):
            problems.append(
                f"{key[0]} claim {key[1]}: the sheet has {len(group)} row(s) and the "
                f"corpus has {len(candidates)} argument(s)")
            continue

        # The collision: the same claim argued twice. Score every row against
        # every candidate on shared children and take the best pairing, then
        # give what is left to what is left -- an argument whose children are
        # "(background knowledge only)" in the sheet scores nothing against
        # anything and is identified by being the other one.
        scores = []
        for row in group:
            mine = fingerprint(row["children"])
            for item in candidates:
                scores.append((len(mine & child_text(item)), id(row), item["id"], row, item))
        scores.sort(key=lambda s: -s[0])
        taken_rows, taken_items = set(), set()
        for score, row_key, item_id, row, item in scores:
            if score == 0 or row_key in taken_rows or item_id in taken_items:
                continue
            resolved[row_key] = item
            taken_rows.add(row_key)
            taken_items.add(item_id)
        left_rows = [r for r in group if id(r) not in taken_rows]
        left_items = [i for i in candidates if i["id"] not in taken_items]
        if len(left_rows) == 1 and len(left_items) == 1:
            resolved[id(left_rows[0])] = left_items[0]
        elif left_rows:
            problems.append(
                f"{key[0]} claim {key[1]}: {len(left_rows)} row(s) could not be told "
                "apart by their children")
    return resolved, problems


def justification_for(row: dict, mechanism: str) -> str:
    """The text this mechanism's box would hold: the primary's column when it is
    the primary, the secondary column otherwise."""
    if mechanism in names(row["primary_mechanism"]):
        return (row["primary_justification"] or "").strip()
    return (row["secondary_justification"] or "").strip()


def infer_verdict(row: dict, mechanisms: list) -> str | None:
    """The Sensible Test verdict a blank cell implies, or None if it implies none.

    Some rows carry mechanisms and justifications and no verdict -- the sheet
    was filled in mechanism-first and the verdict cell was missed. The verdict
    is not a separate judgement there: a mechanism EXISTS only on an argument
    that failed the test, and "None" is what a pass records. So

        a mechanism named   -> Fail
        "None" written out  -> Pass
        nothing at all      -> nothing inferred; the row stays a draft

    The last case is the one that matters: an empty row is an argument nobody
    got to, and guessing a verdict for it would be inventing an annotation.
    """
    if (row["sensible_test"] or "").strip():
        return None
    if mechanisms:
        return "Fail"
    wrote_none = any((row[column] or "").strip().casefold() == "none"
                     for column in ("mechanisms", "primary_mechanism",
                                    "secondary_mechanisms"))
    return "Pass" if wrote_none else None


def annotation_pairs(row: dict, infer: bool = True) -> tuple:
    """One row as the [{schema, name}, value] pairs Potato stores.

    Returns the pairs and the verdict inferred for this row, if one was. A row
    nobody annotated produces nothing, and an argument with no pairs is written
    nowhere -- which is how an argument an annotator never reached looks on
    disk.
    """
    pairs = []
    mechanisms = names(row["mechanisms"])

    sensible = (row["sensible_test"] or "").strip()
    inferred = infer_verdict(row, mechanisms) if infer else None
    verdict = VERDICT.get(sensible) or VERDICT.get(inferred or "")
    if verdict:
        pairs.append([{"schema": "sensible_test_failed", "name": verdict}, verdict])

    primary = names(row["primary_mechanism"])
    secondary = names(row["secondary_mechanisms"])
    if set(mechanisms) != set(primary) | set(secondary):
        raise SystemExit(
            f"row {row['source_row']}: `mechanisms` is {mechanisms} but primary plus "
            f"secondary is {sorted(set(primary) | set(secondary))}")

    justifications = {}
    for mechanism in primary:
        justifications[mechanism] = (row["primary_justification"] or "").strip()
    for mechanism in secondary:
        justifications[mechanism] = (row["secondary_justification"] or "").strip()

    for mechanism in mechanisms:
        pairs.append([{"schema": "mechanisms", "name": mechanism}, mechanism])
    for mechanism in mechanisms:
        text = justifications.get(mechanism)
        if text:
            pairs.append([{"schema": JUSTIFICATION[mechanism], "name": "text_box"}, text])

    # Justification text with no mechanism to hold it. A justification box only
    # exists on screen once its mechanism is ticked, so there is nowhere to put
    # this -- and dropping it would lose something an annotator actually wrote.
    # It goes to Notes, which is the box for what the questions have no room
    # for, marked so nobody reads it as a note typed there on purpose.
    stranded = [text for text in ((row["primary_justification"] or "").strip(),
                                  (row["secondary_justification"] or "").strip())
                if text] if not mechanisms else []

    note = " ".join(filter(None, [
        (row["notes"] or "").strip(),
        *(f"[written as a justification, with no mechanism ticked] {text}"
          for text in stranded)]))
    if note:
        pairs.append([{"schema": "notes", "name": "text_box"}, note])
    return pairs, inferred


def user_state(username: str, ordering: list, annotations: dict) -> dict:
    """The file Potato writes, with only the keys it reads.

    `load_user_state` takes everything but `user_id` with a default, so the
    empty containers are here for a human opening the file beside somebody
    else's, not for the loader.
    """
    order = {iid: n for n, iid in enumerate(ordering)}
    last = max((order[i] for i in annotations if i in order), default=0)
    return {
        "user_id": username,
        "instance_id_ordering": ordering,
        # Where they would land on signing in: the last argument they answered.
        "current_instance_index": last,
        "current_phase_and_page": ["annotation", None],
        "completed_phase_and_pages": [],
        "max_assignments": len(ordering),
        # Empty on purpose: timestamps are a record of somebody typing.
        "instance_id_to_behavioral_data": {},
        "instance_id_to_label_to_value": annotations,
        "instance_id_to_span_to_value": {},
        "phase_to_page_to_label_to_value": {},
        "phase_to_page_to_span_to_value": {},
        "qualified_categories": [],
        "category_qualification_scores": {},
        "instance_id_to_keyword_highlight_state": {},
        "instance_id_to_presentation_order": {},
        "instance_id_to_room_provenance": {},
        "instance_id_to_link_to_value": {},
        "instance_id_to_event_to_value": {},
        "crowd_metadata": {},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="the spreadsheet to import")
    ap.add_argument("--task-dir", default=str(ROOT))
    ap.add_argument("--instances", default=str(ROOT / "data" / "instances.json"))
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve and summarise, write nothing")
    ap.add_argument("--name", action="append", default=[], metavar="SHEET=NETID",
                    help="the sign-in name for one annotator, e.g. --name Denny=dht220001. "
                         "Repeatable. An annotator with no mapping keeps their name from "
                         "the sheet, lowercased.")
    ap.add_argument("--keep-case", action="store_true",
                    help="keep the spreadsheet's capitalisation in the directory name")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace an annotator who already has a user_state.json")
    ap.add_argument("--no-infer", action="store_true",
                    help="leave a blank Sensible Test blank instead of reading it off "
                         "the mechanisms; such rows import as drafts")
    args = ap.parse_args()

    # The sheet names people; the study knows them by what they sign in with.
    # The directory IS the annotator id -- it is what the export, the console
    # and the agreement report key on -- so the mapping has to happen here and
    # not by renaming directories afterwards.
    renames = {}
    for pair in args.name:
        sheet, _, netid = pair.partition("=")
        if not sheet.strip() or not netid.strip():
            raise SystemExit(f"--name wants SHEET=NETID, got {pair!r}")
        renames[sheet.strip().casefold()] = netid.strip()

    # The same rule the sign-in page applies, so an id that could never be
    # typed in is caught here rather than by an annotator who cannot get in.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from accounts import NETID_RE
    for sheet, netid in renames.items():
        if not NETID_RE.match(netid):
            raise SystemExit(
                f"{netid!r} is not something anybody could sign in with: 3 to 64 "
                "characters, lowercase letters, digits, and . _ + - @ inside")

    instances = json.loads(Path(args.instances).read_text())
    ordering = [i["id"] for i in sorted(instances, key=lambda i: int(i["item_number"]))]
    index = corpus_index(instances)

    rows = list(csv.DictReader(Path(args.csv).open()))
    by_annotator: dict = {}
    for row in rows:
        by_annotator.setdefault(row["annotator"].strip(), []).append(row)

    output = Path(args.task_dir) / "annotation_output"
    written, problems, inferences, unresolved = [], [], [], []

    for annotator, their_rows in sorted(by_annotator.items()):
        username = renames.get(annotator.casefold()) or (
            annotator if args.keep_case else annotator.casefold())
        resolved, trouble = resolve(their_rows, index)
        problems += [f"{annotator}: {t}" for t in trouble]

        annotations: dict = {}
        skipped = drafts = 0
        for row in their_rows:
            item = resolved.get(id(row))
            if item is None:
                continue
            pairs, inferred = annotation_pairs(row, infer=not args.no_infer)
            if not pairs:
                skipped += 1
                continue
            if item["id"] in annotations:
                problems.append(f"{annotator}: two rows both landed on {item['id']}")
                continue
            annotations[item["id"]] = pairs
            if inferred:
                inferences.append({
                    "annotator": username, "number": int(item["item_number"]),
                    "instance_id": item["id"], "verdict": inferred,
                    "mechanisms": names(row["mechanisms"]),
                    "unjustified": [m for m in names(row["mechanisms"])
                                    if not justification_for(row, m)],
                })
            elif not (row["sensible_test"] or "").strip():
                drafts += 1
                unresolved.append({
                    "annotator": username, "number": int(item["item_number"]),
                    "instance_id": item["id"],
                })

        written.append({
            "annotator": annotator, "username": username, "rows": len(their_rows),
            "annotated": len(annotations), "not_reached": skipped, "drafts": drafts,
            "state": user_state(username, ordering, annotations),
            # The arguments they actually answered something on -- not the rows
            # they were handed. An annotator who was given 100 and reached 71
            # overlaps a colleague on 71, and a summary that said 100 would be
            # describing the assignment rather than the work.
            "numbers": sorted(int(instance["item_number"]) for instance in instances
                              if instance["id"] in annotations),
        })

    print(f"{len(rows)} row(s), {len(by_annotator)} annotator(s)\n")
    for entry in written:
        if entry["username"] != entry["annotator"].casefold():
            print(f"  {entry['annotator']} signs in as {entry['username']}")
    for entry in written:
        numbers = entry["numbers"]
        reach = f"{min(numbers)}–{max(numbers)}" if numbers else "—"
        print(f"  {entry['username']:<10} {entry['annotated']:>3} annotated, "
              f"{entry['not_reached']:>2} not reached, {entry['drafts']} draft(s); "
              f"arguments {reach}")
    if inferences:
        print(f"\n{len(inferences)} verdict(s) read off the mechanisms, because the "
              "Sensible Test cell was blank:")
        for entry in sorted(inferences, key=lambda e: (e["annotator"], e["number"])):
            named = ", ".join(entry["mechanisms"]) or "no mechanism"
            print(f"  {entry['annotator']:<8} argument {entry['number']:<4} "
                  f"-> {entry['verdict']:<4} ({named})")
            if entry["unjustified"]:
                print(f"           still incomplete: {', '.join(entry['unjustified'])} "
                      "has no justification")
    if unresolved:
        print(f"\n{len(unresolved)} row(s) have no verdict and nothing to read one off; "
              "they import as drafts:")
        for entry in sorted(unresolved, key=lambda e: (e["annotator"], e["number"])):
            print(f"  {entry['annotator']:<8} argument {entry['number']}")

    if problems:
        print("\nPROBLEMS — nothing was written:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    # Who overlaps with whom, because that is the thing this data is for and
    # the thing that is tedious to check after the fact.
    print()
    seen = {e["username"]: set(e["numbers"]) for e in written}
    for first in sorted(seen):
        for second in sorted(seen):
            if first < second and seen[first] & seen[second]:
                print(f"  {first} and {second} both annotated "
                      f"{len(seen[first] & seen[second])} argument(s)")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    for entry in written:
        directory = output / entry["username"]
        state_file = directory / "user_state.json"
        if state_file.exists() and not args.overwrite:
            print(f"\n{state_file} already exists; pass --overwrite to replace it")
            return 1
        directory.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(entry["state"], ensure_ascii=False))
        print(f"\nwrote {state_file}")

    print("\nStart the study server to pick these up; the console reads them now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
