#!/usr/bin/env python3
"""
Build Potato instances for VLMD Subtask 1 (Cognitive Mechanism Classification).

One instance per argument: a claim plus ONLY its immediate children.

    python scripts/build_instances.py                    # rebuild from args_0911.json
    python scripts/build_instances.py --check            # rebuild nothing, just diff ids
    python scripts/build_instances.py --source other.json

THE ORDER IS THE SOURCE FILE'S ORDER, MEME BY MEME, LEAVES FIRST INSIDE EACH
MEME. The memes come in the order they first appear in args_0911.json (their
arguments are contiguous there). Inside a meme the arguments are turned bottom
up, so an argument built only out of visual elements and background is read
before the argument that then uses its claim as a child, and the meme's root
claim C0 is read last -- by which point the annotator has already seen what
holds it up.

The number printed on the argument is its position in THAT order, which is what
the navbar's "Go to" box takes, so "start at 500" still means one thing to
everybody. The line it came from in args_0911.json travels with it as
`source_index`, and the manifest carries the whole number -> line map, so the
order can still be checked against the source file without running the study.

Rerunnable by design. The source file is regenerated upstream, so every id is a
pure function of the argument's own content and the build writes a manifest that
the next build diffs itself against. An id that disappears between two builds is
an id whose annotations are now orphaned, and the build says so loudly rather
than letting it pass.

Outputs
    data/instances.json   the Potato data file, in reading order
    data/manifest.json    counts, hashes, the full id list, and the id diff
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Human-readable badge per child/claim type, in the order they are displayed.
TYPE_LABEL = {
    "element": "Visual element",
    "background": "Background",
    "premise": "Premise",
    "claim": "Claim",
}
TYPE_ORDER = ["element", "background", "premise", "claim"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def child_signature(children: list[dict]) -> str:
    """The child-id tuple, as a stable string. Used to break base-id collisions."""
    return "-".join(str(c["id"]) for c in sorted(children, key=lambda c: c["id"]))


def assign_ids(args: list[dict]) -> tuple[list[str], dict]:
    """
    Stable instance ids, and a report on how they were arrived at.

    The requested form is `{image_stem}::{claim.id}`. On the 2026-09-11 source
    that form is NOT unique: 52 (image, claim.id) pairs carry two arguments each
    -- the same claim supported twice by disjoint child sets -- so 1621 arguments
    collapse to 1566 ids and 55 arguments would be silently dropped by Potato's
    loader, which keys items by id.

    So: the bare form where it is unique, and `{stem}::{claim.id}::{child-ids}`
    for the members of a colliding group. Both are pure functions of the
    argument's own content, which is what makes them survive a rebuild. The
    suffix appearing or disappearing IS a possible id change if upstream adds or
    removes a sibling argument, so the manifest diff below is not optional.
    """
    groups: dict[tuple[str, int], list[int]] = defaultdict(list)
    for i, arg in enumerate(args):
        stem = Path(arg["image"]).stem
        groups[(stem, arg["claim"]["id"])].append(i)

    ids: list[str | None] = [None] * len(args)
    collisions = []
    contradictory = []
    for (stem, claim_id), members in groups.items():
        base = f"{stem}::{claim_id}"
        if len(members) == 1:
            ids[members[0]] = base
            continue
        collisions.append(base)

        # Second tier: the child-id set. Two arguments over one claim normally
        # differ by which children they use.
        by_sig: dict[str, list[int]] = defaultdict(list)
        for i in members:
            by_sig[child_signature(args[i]["children"]) or "nochildren"].append(i)

        for sig, same_sig in by_sig.items():
            if len(same_sig) == 1:
                ids[same_sig[0]] = f"{base}::{sig}"
                continue
            # Third tier, and it should not be reachable: the same claim and the
            # same children twice over. On the 2026-09-11 source it is reachable
            # exactly once, because one (claim, child) edge is labelled BOTH
            # `support` and `attack`. That is an upstream contradiction, not two
            # arguments -- see `contradictory_relation_groups` in the manifest.
            contradictory.append(
                {
                    "base_id": f"{base}::{sig}",
                    "relations": sorted(args[i]["relation"] for i in same_sig),
                    "claim": args[same_sig[0]]["claim"]["content"],
                }
            )
            for i in same_sig:
                ids[i] = f"{base}::{sig}::{args[i]['relation']}"

    resolved = [i for i in ids if i is not None]
    dupes = [k for k, v in Counter(resolved).items() if v > 1]
    if dupes:
        raise SystemExit(
            f"FATAL: {len(dupes)} instance ids are still not unique after "
            f"collision handling, e.g. {dupes[:3]}. Two arguments share an "
            f"image, a claim id, a child-id set AND a relation; the source "
            f"needs a real id field."
        )

    n_bare = sum(1 for m in groups.values() if len(m) == 1)
    return resolved, {
        "n_colliding_base_ids": len(collisions),
        "n_arguments_given_a_suffix": len(resolved) - n_bare,
        "colliding_base_ids": sorted(collisions),
        "n_contradictory_relation_groups": len(contradictory),
        "contradictory_relation_groups": contradictory,
    }


def reading_order(args: list[dict]) -> list[int]:
    """
    Source indices in the order the study serves them: memes in the order they
    first appear, and inside a meme the arguments bottom up.

    An argument's height is the longest chain of claim-children below it that
    are themselves arguments of the same meme; a leaf argument -- one whose
    children are all elements, background or claims nobody argued for -- has
    height 0. Sorting a meme by ascending height puts every argument after the
    arguments that supply its claim-children, however deep the meme nests, and
    the root claim C0 last.

    Ties -- siblings at the same height -- are broken by reverse source order,
    so a meme whose source runs root-first comes out as an exact reversal, which
    is the cheapest thing to eyeball against args_0911.json.

    Reversal alone is NOT enough, which is why the height is computed: on the
    2026-09-11 source 196 (claim, child) edges run the other way in the file,
    and a plain reversal would read those parents before their children.
    """
    by_meme: dict[str, list[int]] = defaultdict(list)
    for i, arg in enumerate(args):
        by_meme[arg["image"]].append(i)

    ordered: list[int] = []
    for members in by_meme.values():
        args_by_claim: dict[int, list[int]] = defaultdict(list)
        for i in members:
            args_by_claim[args[i]["claim"]["id"]].append(i)

        height: dict[int, int] = {}

        def compute(i: int, stack: frozenset[int]) -> int:
            if i in height:
                return height[i]
            if i in stack:
                # A claim cycle inside one meme. Upstream bug; don't hang on it.
                return 0
            best = 0
            for child in args[i]["children"]:
                if child["type"] != "claim":
                    continue
                for j in args_by_claim.get(child["id"], ()):
                    best = max(best, compute(j, stack | {i}) + 1)
            height[i] = best
            return best

        for i in members:
            compute(i, frozenset())
        ordered.extend(sorted(members, key=lambda i: (height[i], -i)))

    return ordered


def order_violations(args: list[dict], order: list[int]) -> list[dict]:
    """Arguments served before an argument for one of their claim-children."""
    position = {i: p for p, i in enumerate(order)}
    first_arg_for: dict[tuple[str, int], int] = {}
    for i in order:
        first_arg_for.setdefault((args[i]["image"], args[i]["claim"]["id"]), position[i])

    bad = []
    for i in order:
        for child in args[i]["children"]:
            if child["type"] != "claim":
                continue
            child_pos = first_arg_for.get((args[i]["image"], child["id"]))
            if child_pos is not None and child_pos > position[i]:
                bad.append(
                    {
                        "image": args[i]["image"],
                        "claim_id": args[i]["claim"]["id"],
                        "child_claim_id": child["id"],
                        "parent_item_number": position[i] + 1,
                        "child_item_number": child_pos + 1,
                    }
                )
    return bad


def render_argument_html(arg: dict, number: int, total: int) -> str:
    """
    The argument's number, the claim, and its immediate children as a badged list.

    The number is first and it is the number in args_0911.json, so "resume from
    712" can be said out loud and checked on screen. Potato's own navbar shows
    the same number -- every annotator is handed the whole corpus in this order,
    so their position in it and this number are the same thing.

    This is the whole item: the meme image is not shown, so the `Visual element`
    children -- which describe in words what is in the image -- are the only
    account of it the annotator gets. They are rendered first for that reason.

    Every piece of content is escaped: child text routinely quotes the meme's own
    overlaid text, which brings quotes, ampersands and the occasional angle
    bracket with it.
    """
    claim = arg["claim"]
    relation = arg.get("relation", "support")
    children = sorted(arg["children"], key=lambda c: (TYPE_ORDER.index(c["type"]) if c["type"] in TYPE_ORDER else 9, c["id"]))

    if relation == "attack":
        rel_line = (
            '<div class="arg-relation arg-relation-attack">'
            "These children are used to make the reader <b>reject</b> the claim "
            "(relation: attack)</div>"
        )
    else:
        rel_line = (
            '<div class="arg-relation arg-relation-support">'
            "These children are used to make the reader <b>accept</b> the claim "
            "(relation: support)</div>"
        )

    claim_badge = TYPE_LABEL.get(claim["type"], claim["type"].title())
    parts = [
        '<div class="arg">',
        f'<div class="arg-number">Argument {number} of {total}</div>',
        rel_line,
        '<div class="arg-claim">',
        f'<div class="arg-head"><span class="badge badge-{html.escape(claim["type"])}">'
        f"{html.escape(claim_badge)}</span>"
        f'<span class="arg-nid">#{claim["id"]}</span>'
        '<span class="arg-role">CLAIM</span></div>',
        f'<div class="arg-text">{html.escape(claim["content"])}</div>',
        "</div>",
        f'<div class="arg-children-head">Children ({len(children)})</div>',
        '<div class="arg-children"><ul>',
    ]
    for child in children:
        badge = TYPE_LABEL.get(child["type"], child["type"].title())
        parts.append(
            "<li>"
            f'<span class="badge badge-{html.escape(child["type"])}">{html.escape(badge)}</span>'
            f'<span class="arg-nid">#{child["id"]}</span>'
            f'<span class="arg-text">{html.escape(child["content"])}</span>'
            "</li>"
        )
    parts += ["</ul></div>", "</div>"]
    return "".join(parts)


def build(source: Path) -> tuple[list[dict], dict]:
    args = json.loads(source.read_text())
    if not isinstance(args, list):
        raise SystemExit(f"FATAL: {source} is not a JSON array.")

    ids, id_report = assign_ids(args)
    total = len(args)

    order = reading_order(args)
    violations = order_violations(args, order)

    instances = []
    for position, index in enumerate(order):
        iid, arg = ids[index], args[index]
        claim = arg["claim"]
        children = sorted(arg["children"], key=lambda c: c["id"])
        instances.append(
            {
                "id": iid,
                # `source_index` is the 0-based line in args_0911.json;
                # `item_number` is the 1-based number shown on the argument,
                # typed into "Go to", and served by `fixed_order`. They differ:
                # each meme's arguments are read bottom up.
                "source_index": index,
                "item_number": position + 1,
                # Carried for joining and for the export, never displayed:
                # the meme image is not part of this task.
                "image_file": arg["image"],
                "meme_id": Path(arg["image"]).stem,
                "relation": arg["relation"],
                "claim_id": claim["id"],
                "claim_type": claim["type"],
                "claim_content": claim["content"],
                "children": children,
                "n_children": len(children),
                "child_types": sorted({c["type"] for c in children}),
                "argument_html": render_argument_html(arg, position + 1, total),
            }
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": source.name,
        "source_sha256": sha256_file(source),
        "n_source_arguments": len(args),
        "n_instances": len(instances),
        "n_memes": len({i["meme_id"] for i in instances}),
        "order": ("memes in source order; each meme's arguments bottom up "
                  "(leaves first, root claim C0 last)"),
        "order_violations": violations,
        "n_order_violations": len(violations),
        "item_number_to_source_index": [i["source_index"] for i in instances],
        "relation_counts": dict(Counter(i["relation"] for i in instances)),
        "children_per_argument": dict(sorted(Counter(i["n_children"] for i in instances).items())),
        "id_list_sha256": sha256_text("\n".join(i["id"] for i in instances)),
        "ids": [i["id"] for i in instances],
        **id_report,
    }
    return instances, manifest


def diff_ids(new: dict, previous_path: Path) -> dict | None:
    if not previous_path.exists():
        return None
    try:
        old = json.loads(previous_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    old_ids, new_ids = set(old.get("ids", [])), set(new["ids"])
    return {
        "previous_generated_at": old.get("generated_at"),
        "previous_source_sha256": old.get("source_sha256"),
        "source_changed": old.get("source_sha256") != new["source_sha256"],
        "added": sorted(new_ids - old_ids),
        "removed": sorted(old_ids - new_ids),
        "unchanged": len(old_ids & new_ids),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=str(ROOT / "args_0911.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "instances.json"))
    ap.add_argument("--manifest", default=str(ROOT / "data" / "manifest.json"))
    ap.add_argument("--check", action="store_true",
                    help="Build in memory and report the id diff; write nothing")
    args = ap.parse_args()

    source, out, manifest_path = Path(args.source), Path(args.out), Path(args.manifest)
    instances, manifest = build(source)
    manifest["id_diff_vs_previous_build"] = diff_ids(manifest, manifest_path)

    print(f"source      {source.name}  sha256={manifest['source_sha256'][:12]}")
    print(f"arguments   {manifest['n_source_arguments']}")
    print(f"instances   {manifest['n_instances']}  over {manifest['n_memes']} memes")
    print(f"order       memes in {source.name} order; each meme bottom up, "
          f"root claim C0 last")
    print(f"order check {manifest['n_order_violations']} arguments served before "
          f"an argument for one of their claim-children")
    print(f"colliding base ids  {manifest['n_colliding_base_ids']} "
          f"(disambiguated with the child-id set)")

    diff = manifest["id_diff_vs_previous_build"]
    if diff is None:
        print("id diff     no previous manifest; nothing to compare")
    elif not diff["source_changed"]:
        print("id diff     source unchanged since the last build")
    else:
        print(f"id diff     +{len(diff['added'])} new, -{len(diff['removed'])} gone, "
              f"{diff['unchanged']} unchanged")
        if diff["removed"]:
            print("  !! ANNOTATIONS AGAINST THESE IDS ARE NOW ORPHANED:")
            for iid in diff["removed"][:20]:
                print(f"       {iid}")
            if len(diff["removed"]) > 20:
                print(f"       ... and {len(diff['removed']) - 20} more")

    if args.check:
        print("\n--check: nothing written")
        return 1 if (diff and diff["removed"]) else 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(instances, indent=1, ensure_ascii=False))
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    print(f"\nwrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
