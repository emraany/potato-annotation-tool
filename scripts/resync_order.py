#!/usr/bin/env python3
"""
Put every annotator back on the corpus order in data/instances.json.

    python scripts/resync_order.py --check     # report, write nothing
    python scripts/resync_order.py             # rewrite, after a .bak

WHY THIS EXISTS.  An annotator's order is not read from data/instances.json on
every page. Potato hands out the whole corpus once, when the account first
reaches the annotation phase, and stores that list in
`annotation_output/<user>/user_state.json` as `instance_id_ordering`. From then
on that stored list is the order, and rebuilding data/instances.json does not
touch it. So an account created before build_instances.py learned to read each
meme bottom up still gets the old order -- the root claim C0 first, before the
arguments that hold it up -- which is exactly what the order was changed to
prevent. This is the one-line fix, and it is needed once per account that
predates the change.

WHAT IT CHANGES, AND WHAT IT DOES NOT.  `instance_id_ordering` is replaced with
the order of data/instances.json, and `current_instance_index` is moved so the
annotator stays on the argument they were on -- the same id, at its new number.
Annotations, drafts, behavioural data and everything else in the file are keyed
by instance id, not by position, so they are untouched and stay attached to
their arguments.

STOP THE SERVER FIRST.  A running study holds every user state in memory and
writes it back on the next save, over anything changed underneath it. The
script refuses to write while it can see something listening on the study port.

An id in a user state that is no longer in data/instances.json is an orphan: an
argument that was assigned, possibly annotated, and has since gone out of the
source file. It is dropped from the ordering, reported by name, and its
annotations are left in place in the file rather than deleted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def port_is_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


def resync(state: dict, canonical: list[str]) -> dict:
    """The new ordering and index, plus what moved. Does not mutate `state`."""
    old = list(state.get("instance_id_ordering", []))
    old_set = set(old)
    canon_set = set(canonical)

    # Only what this annotator was actually assigned, in canonical order. On
    # this study that is the whole corpus, but an account with a smaller
    # `max_assignments` keeps its own subset -- reordered, not enlarged.
    new = [iid for iid in canonical if iid in old_set]
    orphans = [iid for iid in old if iid not in canon_set]
    missing = [iid for iid in canonical if iid not in old_set]

    old_index = state.get("current_instance_index", 0)
    current_id = old[old_index] if 0 <= old_index < len(old) else None
    new_index = new.index(current_id) if current_id in new else 0

    labeled = {k for k, v in state.get("instance_id_to_label_to_value", {}).items() if v}
    return {
        "ordering": new,
        "index": new_index,
        "changed": new != old or new_index != old_index,
        "n_moved": sum(1 for a, b in zip(old, new) if a != b),
        "orphans": orphans,
        "n_unassigned": len(missing),
        "current_id": current_id,
        "old_index": old_index,
        "orphans_with_annotations": sorted(set(orphans) & labeled),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--instances", default=str(ROOT / "data" / "instances.json"))
    ap.add_argument("--output-dir", default=str(ROOT / "annotation_output"))
    ap.add_argument("--user", action="append",
                    help="Only this annotator; repeatable. Default: all of them.")
    ap.add_argument("--check", action="store_true", help="Report and write nothing")
    ap.add_argument("--port", type=int, default=8765,
                    help="Study port to check is free before writing (default 8765)")
    ap.add_argument("--force", action="store_true",
                    help="Write even though something is listening on --port")
    args = ap.parse_args()

    canonical = [i["id"] for i in json.loads(Path(args.instances).read_text())]
    print(f"canonical   {len(canonical)} instances from {args.instances}")
    print(f"            first: {canonical[0]}")

    states = sorted(Path(args.output_dir).glob("*/user_state.json"))
    if args.user:
        wanted = set(args.user)
        states = [p for p in states if p.parent.name in wanted]
    if not states:
        print("no user states found; nothing to do")
        return 0

    if not args.check and port_is_open(args.port) and not args.force:
        print(f"\nREFUSING TO WRITE: something is listening on port {args.port}.")
        print("A running study writes its in-memory user states back over these")
        print("files. Stop it, run this again, then start it.")
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    n_written = 0
    for path in states:
        state = json.loads(path.read_text())
        r = resync(state, canonical)
        user = path.parent.name
        head = r["ordering"][0] if r["ordering"] else "(none)"
        print(f"\n{user}")
        print(f"  assigned  {len(r['ordering'])}  first now: {head}")
        if r["orphans"]:
            print(f"  ORPHANS   {len(r['orphans'])} assigned ids are no longer in "
                  f"instances.json, dropped from the ordering:")
            for iid in r["orphans"][:10]:
                print(f"              {iid}")
            if r["orphans_with_annotations"]:
                print(f"  !! {len(r['orphans_with_annotations'])} of them carry "
                      f"annotations, left in the file: "
                      f"{r['orphans_with_annotations'][:5]}")
        if r["n_unassigned"]:
            print(f"  note      {r['n_unassigned']} instances were never assigned to "
                  f"this account; not added")
        if not r["changed"]:
            print("  order     already canonical; nothing to do")
            continue
        print(f"  order     {r['n_moved']} positions change")
        print(f"  position  was #{r['old_index'] + 1} ({r['current_id']}), "
              f"now #{r['index'] + 1} -- same argument")
        if args.check:
            continue

        backup = path.with_suffix(f".json.{stamp}.bak")
        shutil.copy2(path, backup)
        state["instance_id_ordering"] = r["ordering"]
        state["current_instance_index"] = r["index"]
        path.write_text(json.dumps(state, indent=2))
        print(f"  wrote     {path}  (backup {backup.name})")
        n_written += 1

    if args.check:
        print("\n--check: nothing written")
    else:
        print(f"\nrewrote {n_written} user state(s). Start the study again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
