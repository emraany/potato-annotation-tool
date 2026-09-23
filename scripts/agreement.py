#!/usr/bin/env python3
"""
Agreement between two annotators, over one stretch of argument numbers.

Everything here is computed from the annotations on disk. There is no model and
no external call: the findings at the top of the page are fixed sentences with
computed numbers dropped into them, and every number on the page comes out of
`report()`.

    report(records, pair=("aima", "ryan"), lo=1, hi=100)

`records` are what `export_annotations.collect` returns. `report` is pure --
same records, same pair, same range, same answer -- which is what makes it
testable, and tests/test_agreement.py is where the two Week 2 pairs are checked
number by number.

WHAT THIS ANSWERS, and what it deliberately does not. The question is "where do
these two people part company, and is it the same thing every time". So: one
pair at a time, one mechanism set per annotator per argument, and five mutually
exclusive ways two sets can differ. Not a battery of coefficients over every
annotator at once -- that page said `0.42, fair` and nobody could act on it.

Krippendorff's alpha, Fleiss' kappa, alpha-MASI, mean Jaccard and the Landis &
Koch bands were all here and are all gone. Cohen's kappa stays, on the Pass/Fail
question only, because it is the one number that answers "is this better than
two people guessing with these two people's habits".

MECHANISM SETS ARE FLAT. One set per annotator per argument, no primary and no
secondary -- see DESIGN.md. `mechanism_set` still splits a delimited string and
drops "None", because a CSV fixture and a pre-flatten export both arrive that
way.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

CANONICAL = {m.casefold(): m for m in MECHANISMS}

VERDICTS = ("Pass", "Fail")

# An argument's category is one of these, and exactly one. In this order:
# agreement first, then the ways they part company, worst last.
CATEGORIES = [
    ("identical", "Identical sets"),
    ("one_extra", "Overlap, one has extra"),
    ("both_extra", "Overlap, both have extras"),
    ("one_none", "One said None"),
    ("no_shared", "No shared mechanism"),
]

# Under this many shared arguments the page says so and drops the findings: a
# rule that fires on three arguments is a coincidence with a sentence round it.
THIN = 20

# The first four rules that fire are shown. A fifth would be scrolled past.
MAX_FINDINGS = 4


# --- reading one annotation ---------------------------------------------------

def mechanism_set(value) -> frozenset:
    """One annotator's mechanisms on one argument, as a set.

    Takes the list the store holds, or a delimited string (";" or ","), which
    is how a CSV fixture and a pre-flatten export both arrive. "None" in any
    casing is the absence of a mechanism and not a mechanism; names are matched
    case-insensitively and come back in the codebook's casing, so two spellings
    of one mechanism cannot read as a disagreement.
    """
    if value is None:
        return frozenset()
    parts = value if isinstance(value, (list, tuple, set, frozenset)) \
        else str(value).replace(";", ",").split(",")
    out = set()
    for part in parts:
        name = str(part).strip()
        if not name or name.casefold() == "none":
            continue
        out.add(CANONICAL.get(name.casefold(), name))
    return frozenset(out)


def verdict(record) -> str | None:
    """Pass, Fail, or None where the Sensible Test was left unanswered."""
    value = record.get("sensible_test")
    return value if value in VERDICTS else None


def category(first: frozenset, second: frozenset) -> str:
    """How two mechanism sets differ. Exactly one category per argument.

    Two empty sets are `identical`: both said there is no mechanism here, which
    is agreement, and on a healthy pair it is the commonest kind.
    """
    if first == second:
        return "identical"
    if first & second:
        return "one_extra" if (first > second or second > first) else "both_extra"
    if not first or not second:
        return "one_none"
    return "no_shared"


# --- the arguments two people share -------------------------------------------

def by_argument(records: list) -> dict:
    """instance_id -> {annotator: record}."""
    out: dict = {}
    for record in records:
        out.setdefault(record["instance_id"], {})[record["annotator_id"]] = record
    return out


def shared_pairs(records: list) -> list:
    """Every pair with at least one argument in common, most shared first.

    This fills the pair selector, so it carries the count each label shows --
    and the first entry is the pair the page opens on.
    """
    counts: Counter = Counter()
    for per_annotator in by_argument(records).values():
        for pair in combinations(sorted(per_annotator), 2):
            counts[pair] += 1
    return [{"annotators": list(pair), "n_shared": n}
            for pair, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def shared_arguments(records: list, first: str, second: str) -> list:
    """Every argument both annotated, in argument order, carrying both records.

    Argument order is `item_number` -- the number printed on the argument, the
    number in the navbar, and the number people hand work over by. An argument
    with no number (annotated, then dropped by a rebuild) sorts to the end and
    falls outside every range.
    """
    out = []
    for instance_id, per_annotator in by_argument(records).items():
        if first in per_annotator and second in per_annotator:
            out.append({
                "instance_id": instance_id,
                "number": per_annotator[first].get("item_number"),
                "meme_id": per_annotator[first].get("meme_id"),
                "records": (per_annotator[first], per_annotator[second]),
            })
    out.sort(key=lambda a: (a["number"] is None, a["number"], a["instance_id"]))
    return out


def span(arguments: list) -> tuple:
    """The lowest and highest argument number in a list, or (None, None)."""
    numbers = [a["number"] for a in arguments if a["number"] is not None]
    return (min(numbers), max(numbers)) if numbers else (None, None)


def in_range(arguments: list, lo, hi) -> list:
    if lo is None and hi is None:
        return list(arguments)
    kept = []
    for argument in arguments:
        number = argument["number"]
        if number is None:
            continue
        if lo is not None and number < lo:
            continue
        if hi is not None and number > hi:
            continue
        kept.append(argument)
    return kept


# --- the numbers --------------------------------------------------------------

def cohen_kappa(pairs: list) -> dict:
    """Pass/Fail agreement, corrected for these two people's own habits.

    `kappa` is None where it is undefined -- no arguments, or chance agreement
    of 1, which is what happens when one label is all either of them ever used.
    The page prints an em dash there rather than a number, because there is
    nothing for a correction to correct.
    """
    n = len(pairs)
    if not n:
        return {"n": 0, "matches": 0, "observed": None, "chance": None, "kappa": None}

    matches = sum(1 for left, right in pairs if left == right)
    observed = matches / n
    chance = sum((sum(1 for left, _ in pairs if left == v) / n)
                 * (sum(1 for _, right in pairs if right == v) / n)
                 for v in VERDICTS)
    kappa = None if chance == 1 else (observed - chance) / (1 - chance)
    return {"n": n, "matches": matches, "observed": observed,
            "chance": chance, "kappa": kappa}


def positive_agreement(sets: list) -> list:
    """Per mechanism: how often each used it, how often both did, and
    `2 x both / (A + B)`.

    Positive specific agreement, not a kappa per mechanism: the question is
    "when one of them reaches for this label, does the other agree", and the
    arguments where neither used it -- the overwhelming majority -- say nothing
    about that. A kappa counts those as agreement and reads high for a label
    nobody uses.

    Weakest first, which is the order worth reading.
    """
    rows = []
    for mechanism in MECHANISMS:
        a = sum(1 for first, _ in sets if mechanism in first)
        b = sum(1 for _, second in sets if mechanism in second)
        both = sum(1 for first, second in sets
                   if mechanism in first and mechanism in second)
        rows.append({
            "mechanism": mechanism, "a": a, "b": b, "both": both,
            "agreement": None if a + b == 0 else 2 * both / (a + b),
        })
    rows.sort(key=lambda r: (r["agreement"] is None, r["agreement"], r["mechanism"]))
    return rows


def swaps(sets: list) -> list:
    """Which of A's mechanisms turn up where B used which of theirs.

    Only over the arguments with NO mechanism in common: those are the ones
    where the two of them are naming the same move differently, which is the
    thing a confusion matrix over a set-valued answer could never say cleanly.
    Every cross pair is counted, so one annotator's two labels against the
    other's one is two pairs.
    """
    counts: Counter = Counter()
    for first, second in sets:
        if category(first, second) != "no_shared":
            continue
        for mine in first:
            for theirs in second:
                counts[(mine, theirs)] += 1
    return [{"a": a, "b": b, "n": n}
            for (a, b), n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


# --- the findings -------------------------------------------------------------
#
# Six rules, in priority order; the first four that fire are shown. Each is a
# fixed lead sentence and a fixed sentence of numbers -- nothing is generated,
# and a rule that does not fire prints nothing rather than hedging.
#
# A rule's thresholds are the whole of its judgement, so they live here and not
# spread through the page.

def findings(data: dict) -> list:
    """The first four rules that fire, as {rule, lead, numbers}."""
    a, b = data["pair"]
    cats = data["categories"]
    counts = {m["mechanism"]: (m["a"], m["b"], m["both"]) for m in data["mechanisms"]}
    out, named = [], set()

    # 1. Two mechanisms being used for the same thing.
    top = data["swaps"][0] if data["swaps"] else None
    if top and top["n"] >= 5:
        m1, m2 = top["a"], top["b"]
        a1, b1 = counts[m1][0], counts[m1][1]
        a2, b2 = counts[m2][0], counts[m2][1]
        named |= {m1, m2}
        out.append({
            "rule": "swap",
            "lead": f"{a} and {b} may be using {m1} and {m2} for the same thing.",
            "numbers": (f"On {top['n']} no-shared arguments, {a} chose {m1} where "
                        f"{b} chose {m2}. {a} used {m1} {a1} times to {b}'s {b1}; "
                        f"{b} used {m2} {b2} times to {a}'s {a2}."),
        })

    # 2. One of them is consistently the one with more labels.
    partial = cats["one_extra"] + cats["both_extra"]
    extras = {who: n for who, n in data["extras_by"].items() if n}
    if partial >= 3 and extras:
        who, k = max(extras.items(), key=lambda kv: kv[1])
        if k / partial >= 0.75:
            other = b if who == a else a
            out.append({
                "rule": "extras",
                "lead": "Disagreement is about quantity, not identity.",
                "numbers": (f"In {k} of {partial} partial matches, {who} has {other}'s "
                            f"full set plus extras. {a} used {data['labels'][0]} labels "
                            f"to {b}'s {data['labels'][1]}."),
            })

    # 3. A mechanism they both reach for, never on the same argument.
    never = [(m, x, y) for m, (x, y, both) in counts.items() if x and y and not both]
    if never:
        mechanism, x, y = max(never, key=lambda t: t[1] + t[2])
        named.add(mechanism)
        out.append({
            "rule": "never_matched",
            "lead": f"{mechanism} never matched.",
            "numbers": f"{a} used it {x} times, {b} {y} times, never on the same argument.",
        })

    # 4. One of them reaches for a mechanism far more often than the other.
    #    Mechanisms already named above are skipped: the reader has just been
    #    told why those counts differ.
    gaps = []
    for mechanism, (x, y, _) in counts.items():
        if mechanism in named or abs(x - y) < 5:
            continue
        ratio = float("inf") if min(x, y) == 0 else max(x, y) / min(x, y)
        if ratio >= 2:
            gaps.append((abs(x - y), mechanism, x, y))
    if gaps:
        _, mechanism, x, y = max(gaps)
        out.append({
            "rule": "imbalance",
            "lead": f"{mechanism} is the biggest usage gap.",
            "numbers": f"{a} used it {x} times, {b} {y}.",
        })

    # 5. How much of the disagreement is about the mechanism at all.
    total = data["n_mechanism_arguments"]
    no_shared = cats["no_shared"]
    if total:
        share = no_shared / total
        if share >= 0.20 or share <= 0.10:
            out.append({
                "rule": "confusion",
                "lead": ("This is real mechanism confusion." if share >= 0.20
                         else "Real mechanism confusion is rare."),
                "numbers": (f"{no_shared} of {total} arguments have both annotators "
                            "flagging mechanisms with nothing in common."),
            })

    # 6. Whether the Pass/Fail splits are really the same disagreement again.
    splits = data["passfail_splits"]
    if splits["n"] >= 3 and splits["one_none"] / splits["n"] >= 0.75:
        who, p = splits["passing"]
        out.append({
            "rule": "passfail",
            "lead": "Pass/Fail splits are None splits.",
            "numbers": (f"{splits['one_none']} of {splits['n']} Pass/Fail splits are "
                        "arguments where one annotator passed with no mechanism and "
                        f"the other flagged one. {who} is the one passing in {p} of them."),
        })

    return out[:MAX_FINDINGS]


# --- the report ---------------------------------------------------------------

def report(records: list, pair=None, lo=None, hi=None) -> dict:
    """Every number on the agreement page, for one pair over one range.

    `pair` defaults to the two annotators sharing the most arguments, and the
    range to the whole span of what that pair shares. A `lo` above `hi` is
    swapped rather than refused, and both are clamped to the pair's span, so a
    hand-edited URL cannot produce an empty page that does not explain itself.
    """
    pairs = shared_pairs(records)
    if pair is None and pairs:
        pair = tuple(pairs[0]["annotators"])
    if pair is None:
        return {
            "pairs": [], "pair": None, "arguments": [], "findings": [],
            "empty": "Nobody has annotated the same argument as anybody else yet. "
                     "Give the same stretch of argument numbers to a second annotator.",
        }

    first, second = pair
    everything = shared_arguments(records, first, second)
    low, high = span(everything)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    if low is not None:
        lo = low if lo is None else max(low, min(lo, high))
        hi = high if hi is None else min(high, max(hi, lo))

    arguments = in_range(everything, lo, hi)
    sets = [(mechanism_set(a["records"][0].get("mechanisms")),
             mechanism_set(a["records"][1].get("mechanisms"))) for a in arguments]

    cats = {name: 0 for name, _ in CATEGORIES}
    extras_by: Counter = Counter({first: 0, second: 0})
    detail = []
    for argument, (mine, theirs) in zip(arguments, sets):
        kind = category(mine, theirs)
        cats[kind] += 1
        if kind == "one_extra":
            extras_by[first if mine > theirs else second] += 1
        left, right = argument["records"]
        detail.append({
            "instance_id": argument["instance_id"],
            "number": argument["number"],
            "meme_id": argument["meme_id"],
            "category": kind,
            "shared": sorted(mine & theirs, key=MECHANISMS.index),
            "a": {"verdict": verdict(left),
                  "mechanisms": sorted(mine, key=MECHANISMS.index)},
            "b": {"verdict": verdict(right),
                  "mechanisms": sorted(theirs, key=MECHANISMS.index)},
        })

    # Pass/Fail is scored on its own arguments. One annotator leaving the
    # Sensible Test blank takes that argument out of Pass/Fail and leaves it in
    # the mechanism numbers, because they did answer the mechanism question.
    verdicts = [(verdict(a["records"][0]), verdict(a["records"][1])) for a in arguments]
    answered = [(x, y) for x, y in verdicts if x and y]
    kappa = cohen_kappa(answered)

    splits = [(d, x) for d, (x, y) in zip(detail, verdicts) if x and y and x != y]
    passing: Counter = Counter()
    for d, x in splits:
        if d["category"] == "one_none":
            passing[first if x == "Pass" else second] += 1

    data = {
        "pairs": pairs,
        "pair": [first, second],
        "range": {"from": lo, "to": hi, "span": [low, high],
                  "n_in_range": len(arguments), "n_shared": len(everything)},
        "n_mechanism_arguments": len(arguments),
        "n_passfail_arguments": kappa["n"],
        "thin": len(arguments) < THIN,
        "categories": cats,
        "extras_by": dict(extras_by),
        "labels": [sum(len(m) for m, _ in sets), sum(len(t) for _, t in sets)],
        "identical_share": cats["identical"] / len(arguments) if arguments else None,
        "share_one_share": ((cats["identical"] + cats["one_extra"] + cats["both_extra"])
                            / len(arguments)) if arguments else None,
        "passfail": {
            **kappa,
            "matrix": matrix(answered),
            "fail_rate": [fail_rate(answered, 0), fail_rate(answered, 1)],
        },
        "passfail_splits": {
            "n": len(splits),
            "one_none": sum(1 for d, _ in splits if d["category"] == "one_none"),
            "passing": list(max(passing.items(), key=lambda kv: kv[1])) if passing
                       else [first, 0],
        },
        "mechanisms": positive_agreement(sets),
        "swaps": swaps(sets),
        "arguments": detail,
    }
    data["findings"] = [] if data["thin"] else findings(data)
    if not arguments:
        data["empty"] = ("No shared arguments in this range. Widen the range or "
                         "assign the same arguments to a second annotator.")
    return data


def matrix(pairs: list) -> list:
    """The 2x2: first annotator down the side, second across the top."""
    counts = Counter(pairs)
    return [[counts.get((row, column), 0) for column in VERDICTS] for row in VERDICTS]


def fail_rate(pairs: list, side: int):
    """How often one of them failed an argument, over the ones both answered."""
    if not pairs:
        return None
    return sum(1 for pair in pairs if pair[side] == "Fail") / len(pairs)
