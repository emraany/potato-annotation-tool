#!/usr/bin/env python3
"""
Who was given which stretch of the corpus.

    annotation_output/assignments.json

      {"version": 1,
       "assignments": {
         "ey1220": [{"from": 1, "to": 320, "label": "batch A",
                     "by": "console", "at": "2026-09-22T14:02:11Z"}]}}

This is an OVERLAY, and that is the whole design. Potato still hands every
annotator all 1,621 arguments in one fixed order -- nothing here touches
`assignment_strategy`, `max_annotations_per_user` or the per-item cap, because
every one of those hands out a block whose first item becomes that annotator's
argument 1 and the global numbering is what this study is built on (DESIGN.md,
*Assignment*). What an entry in this file changes is what the annotator is
SHOWN -- their own progress, their own remaining count, where the arrows stop --
and what the console can say about coverage. It changes nothing about what they
are able to reach: `Go to` still reaches every argument, and an argument outside
somebody's batch still saves.

An annotator with no entry, or with an empty list, is unassigned, and unassigned
means exactly today's behaviour: the whole corpus, no band on screen, nothing
different. So this ships dark, and a missing or unreadable file degrades to the
study as it runs now rather than breaking it.

Ranges are ARGUMENT NUMBERS -- the number printed on the argument, typed into
`Go to`, quoted in the console, 1-based. They are kept as a list and appended
to, never merged: two ranges is how somebody gets a second batch without losing
the record of the first, and an overlapping range on a second annotator is how
an agreement set is collected.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "annotation_output" / "assignments.json"

VERSION = 1


def empty() -> dict:
    return {"version": VERSION, "assignments": {}}


def _clean(data) -> dict:
    """Whatever is on disk, as the shape the rest of this module promises.

    A file that has been hand-edited into nonsense reads as "nobody is
    assigned" rather than raising: an annotator must not be locked out of the
    study by a typo in an admin's file.
    """
    if not isinstance(data, dict):
        return empty()
    raw = data.get("assignments")
    if not isinstance(raw, dict):
        return empty()

    assignments: dict[str, list[dict]] = {}
    for netid, ranges in raw.items():
        if not isinstance(netid, str) or not isinstance(ranges, list):
            continue
        kept = []
        for entry in ranges:
            if not isinstance(entry, dict):
                continue
            try:
                low, high = int(entry["from"]), int(entry["to"])
            except (KeyError, TypeError, ValueError):
                continue
            if low > high:
                low, high = high, low
            kept.append({**entry, "from": max(1, low), "to": max(1, high)})
        if kept:
            assignments[netid] = kept
    cleaned = {"version": VERSION, "assignments": assignments}
    if data.get("updated"):
        cleaned["updated"] = data["updated"]
    return cleaned


def load(path: Path = FILE) -> dict:
    if not path.exists():
        return empty()
    try:
        return _clean(json.loads(path.read_text()))
    except (OSError, ValueError):
        return empty()


def save(data: dict, path: Path = FILE) -> None:
    """Write it the way the annotation files are written: atomically.

    The study server reads this file on request while the console writes it,
    and the two are separate processes. A half-written file read in between
    would read as "nobody is assigned", which is a study that quietly loses
    everyone's band mid-shift.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({**_clean(data), "updated": _now()}, indent=1, ensure_ascii=False)
    handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".assignments-")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(body + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- reading an assignment ----------------------------------------------------

def ranges_for(data: dict, netid: str) -> list[dict]:
    return list((data.get("assignments") or {}).get((netid or "").lower(), []))


def numbers_for(data: dict, netid: str, total: int) -> set[int]:
    """Every argument number in somebody's batch, clamped to the corpus.

    An empty set means unassigned -- NOT "assigned nothing". The two are the
    same shape and different studies, and every caller treats the empty set as
    "show them the whole corpus, as before".
    """
    numbers: set[int] = set()
    for entry in ranges_for(data, netid):
        numbers.update(range(max(1, entry["from"]), min(total, entry["to"]) + 1))
    return numbers


def as_ranges(numbers) -> str:
    """{1,2,3,5,9,10} -> "1-3, 5, 9-10". Gaps stay visible as gaps."""
    runs: list[list[int]] = []
    for number in sorted(numbers):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(str(lo) if lo == hi else f"{lo}–{hi}" for lo, hi in runs)


# --- writing one -------------------------------------------------------------

def add(data: dict, netid: str, low: int, high: int, total: int,
        label: str = "", by: str = "console") -> dict:
    netid = (netid or "").strip().lower()
    if not netid:
        raise ValueError("no annotator")
    low, high = int(low), int(high)
    if low > high:
        low, high = high, low
    low, high = max(1, low), min(total, high)
    if low > total:
        raise ValueError(f"argument {low} is past the end of the corpus ({total})")

    entry = {"from": low, "to": high, "label": (label or "").strip(),
             "by": by, "at": _now()}
    data.setdefault("assignments", {}).setdefault(netid, []).append(entry)
    return entry


def remove(data: dict, netid: str, index: int) -> bool:
    netid = (netid or "").strip().lower()
    ranges = (data.get("assignments") or {}).get(netid) or []
    if not 0 <= index < len(ranges):
        return False
    ranges.pop(index)
    if not ranges:
        data["assignments"].pop(netid, None)
    return True


# --- coverage -----------------------------------------------------------------

def per_number(data: dict, total: int) -> list[int]:
    """How many annotators hold each argument, indexed 0..total-1.

    0 is nobody, 1 is covered once, 2 or more is a deliberate agreement
    stretch -- and the only place in this study where an agreement coefficient
    has anything to be computed over.
    """
    counts = [0] * total
    for netid in (data.get("assignments") or {}):
        for number in numbers_for(data, netid, total):
            counts[number - 1] += 1
    return counts


# --- meme boundaries ----------------------------------------------------------

def snap(low: int, high: int, meme_of: list[str]) -> "tuple[int, int]":
    """Widen a range to whole memes.

    A meme's arguments are read bottom up as a unit -- a child argument is on
    screen before the argument that uses its claim -- so splitting one meme
    across two people costs both of them the context the ordering was built to
    give. The range only ever grows, never shrinks: a stretch somebody was
    told to do is not quietly taken off them by a rounding rule.
    """
    total = len(meme_of)
    low, high = max(1, min(low, total)), max(1, min(high, total))
    while low > 1 and meme_of[low - 2] == meme_of[low - 1]:
        low -= 1
    while high < total and meme_of[high] == meme_of[high - 1]:
        high += 1
    return low, high
