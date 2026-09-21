#!/usr/bin/env python3
"""
Inter-annotator agreement, computed from `annotation_output/` on disk.

Potato's own report at `/admin/iaa` only looks at items whose per-item
annotator cap is 2 or more, and this study sets no cap: work is handed out by
argument number, between people, so every coefficient there is `null` however
much two annotators have actually read the same arguments. This computes the
same coefficients over the overlap that is genuinely on disk -- any argument
two or more people have annotated -- which is the only overlap this study has.

The coefficients themselves are Potato's, imported from
`potato.server_utils.iaa`, so a number here and a number there mean the same
thing. What differs is only which items they are computed over.

Each question is scored on the arguments where at least two annotators gave it
an answer; `scope` on every question says what that means for that question,
and it is shown on screen, because "kappa 0.62" over four arguments and over
four hundred are not the same claim.
"""

from __future__ import annotations

import warnings
from collections import Counter
from itertools import combinations
from math import isnan

MECHANISMS = [
    "Affective Hijacking",
    "Episodic Overgeneralization",
    "Spurious Causal Correlation",
    "Satirical Trivialization",
    "Esoteric Code Activation",
    "Spurious Legitimacy Signaling",
]

VERDICTS = ["Pass", "Fail"]


# --- what each question's answer is, and which arguments it is scored on ------
#
# A value of None means "this annotator did not answer this question on this
# argument", and the argument drops out of that question's scoring for them.
# It is not a category: counting an unanswered question as a category of its
# own would let two people who have both not got there yet agree perfectly.

def _verdict(r):
    return r["sensible_test"]


def _primary(r):
    return r["primary_mechanism"] or None


def _secondary(r):
    # Only on an argument they failed. On a Pass the set is empty by
    # construction, and scoring those would report agreement about a question
    # neither person was asked.
    return frozenset(r["secondary_mechanisms"]) if r["sensible_test"] == "Fail" else None


def _flag(r):
    return ("flagged" if r["flag_for_discussion"] else "not flagged") \
        if r["sensible_test"] is not None else None


QUESTIONS = [
    {"name": "sensible_test", "title": "Verdict (Sensible Test)", "kind": "nominal",
     "labels": VERDICTS, "value": _verdict,
     "scope": "arguments both annotators answered the Sensible Test on"},
    {"name": "primary_mechanism", "title": "Primary mechanism", "kind": "nominal",
     "labels": MECHANISMS, "value": _primary,
     "scope": "arguments both annotators named a primary mechanism on — which "
              "means both failed it, so it is agreement about which mechanism, "
              "not about whether there is one"},
    {"name": "secondary_mechanisms", "title": "Secondary mechanisms", "kind": "multilabel",
     "labels": MECHANISMS, "value": _secondary,
     "scope": "arguments both annotators failed; an empty set is an answer here, "
              "so choosing no secondary mechanism counts"},
    {"name": "flag_for_discussion", "title": "Flagged for discussion", "kind": "nominal",
     "labels": ["flagged", "not flagged"], "value": _flag,
     "scope": "arguments both annotators finished; low agreement here is normal "
              "and is not a problem with the codebook"},
]

# Landis & Koch, which is a convention and not a threshold anybody has to meet.
BANDS = [(0.0, "poor"), (0.20, "slight"), (0.40, "fair"),
         (0.60, "moderate"), (0.80, "substantial"), (1.01, "almost perfect")]


def band(value):
    if value is None:
        return None
    for upper, name in BANDS:
        if value < upper:
            return name
    return "almost perfect"


def _num(value):
    """A coefficient, or None where it is undefined. NaN is not valid JSON and
    an undefined coefficient is the normal case early in a study."""
    if value is None:
        return None
    value = float(value)
    return None if isnan(value) else round(value, 4)


# --- the report ----------------------------------------------------------------

def by_item(records: list[dict]) -> dict:
    """instance_id -> {annotator_id: record}."""
    items: dict[str, dict] = {}
    for record in records:
        items.setdefault(record["instance_id"], {})[record["annotator_id"]] = record
    return items


def answers_for(question: dict, items: dict) -> dict:
    """instance_id -> {annotator: answer}, over the arguments this question is
    scored on: at least two annotators, each having answered it."""
    out = {}
    for instance_id, per_annotator in items.items():
        given = {annotator: question["value"](record)
                 for annotator, record in per_annotator.items()}
        given = {a: v for a, v in given.items() if v is not None}
        if len(given) >= 2:
            out[instance_id] = given
    return out


def _pairs(answers: dict) -> list[tuple[str, str, list, list]]:
    """Every pair of annotators, with their answers aligned on the arguments
    they both answered. Aligning on the item and not on position is the whole
    correctness of this: two people who skipped different arguments have lists
    of different lengths, and comparing them by index compares answers about
    different arguments."""
    annotators = sorted({a for given in answers.values() for a in given})
    out = []
    for first, second in combinations(annotators, 2):
        shared = sorted(i for i, given in answers.items()
                        if first in given and second in given)
        if shared:
            out.append((first, second,
                        [answers[i][first] for i in shared],
                        [answers[i][second] for i in shared]))
    return out


def observed_agreement(pairs) -> float | None:
    """The plain number: out of every pair of answers about one argument, how
    many are the same. It is what people actually want to know, and it is the
    number the kappas correct for chance."""
    same = total = 0
    for _, _, left, right in pairs:
        same += sum(1 for a, b in zip(left, right) if a == b)
        total += len(left)
    return same / total if total else None


def nominal_metrics(answers: dict) -> dict:
    from potato.server_utils.iaa.alpha import krippendorff_alpha
    from potato.server_utils.iaa.nominal import cohen_kappa, fleiss_kappa

    pairs = _pairs(answers)
    long_format = [(annotator, instance_id, value)
                   for instance_id, given in answers.items()
                   for annotator, value in given.items()]

    kappas = []
    # sklearn warns once per call when a pair of annotators used a single
    # category between them, which is a normal state of affairs here and says
    # nothing the report does not already say under "undefined". Scoped, so
    # the rest of the process keeps its warnings.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for _, _, left, right in pairs:
            value = _num(cohen_kappa(left, right))
            if value is not None:
                kappas.append(value)

    return {
        "observed_agreement": _num(observed_agreement(pairs)),
        "krippendorff_alpha": _num(krippendorff_alpha(long_format, "nominal")),
        "fleiss_kappa": _num(fleiss_kappa([Counter(g.values()) for g in answers.values()])),
        "mean_cohen_kappa": _num(sum(kappas) / len(kappas)) if kappas else None,
        "headline": "krippendorff_alpha",
    }


def multilabel_metrics(answers: dict) -> dict:
    from potato.server_utils.iaa.multilabel import alpha_masi, mean_jaccard

    pairs = _pairs(answers)
    long_format = [(annotator, instance_id, value)
                   for instance_id, given in answers.items()
                   for annotator, value in given.items()]
    by_user: dict[str, dict] = {}
    for instance_id, given in answers.items():
        for annotator, value in given.items():
            by_user.setdefault(annotator, {})[instance_id] = value

    return {
        "exact_match": _num(observed_agreement(pairs)),
        "alpha_masi": _num(alpha_masi(long_format)),
        "mean_jaccard": _num(mean_jaccard(by_user)) if len(by_user) > 1 else None,
        "headline": "alpha_masi",
    }


def confusion(question: dict, answers: dict, first: str, second: str) -> dict | None:
    """One pair's answers to one single-choice question, as a table: what the
    first annotator said down the side, what the second said across the top.
    Each shared argument is counted once, so the off-diagonal cells are the
    disagreements themselves and not a symmetrised shadow of them."""
    if question["kind"] != "nominal":
        return None
    counts: Counter = Counter()
    for given in answers.values():
        if first in given and second in given:
            counts[(given[first], given[second])] += 1
    if not counts:
        return None
    seen = {v for cell in counts for v in cell}
    # Declared order first -- Pass before Fail, the mechanisms in codebook
    # order -- then anything the data has that the codebook does not.
    labels = [l for l in question["labels"] if l in seen] + sorted(seen - set(question["labels"]))
    return {
        "labels": labels,
        "rows": [[counts.get((row, column), 0) for column in labels] for row in labels],
        "n": sum(counts.values()),
        "n_agree": sum(counts[(l, l)] for l in labels),
    }


def why_undefined(question: dict, answers: dict, headline) -> str | None:
    """A coefficient corrects agreement for the agreement chance alone would
    produce, and that correction is undefined when there is nothing to correct
    -- no overlap at all, or one category used throughout. Saying so is the
    whole difference between this page and a table of nulls."""
    if headline is not None:
        return None
    if not answers:
        return ("no argument has been answered here by two people yet — hand the "
                "same stretch of argument numbers to a second annotator and this fills in")
    used = {value for given in answers.values() for value in given.values()}
    if len(used) == 1:
        only = next(iter(used))
        only = "the same set" if isinstance(only, frozenset) else f"\u201c{only}\u201d"
        return (f"every answer given was {only}, so there is no variation for a "
                "chance-corrected coefficient to measure; the observed agreement "
                "beside it is the whole story")
    return "undefined on this much data"


def report(records: list[dict]) -> dict:
    """Every coefficient, every pair, over whatever overlap is on disk."""
    items = by_item(records)
    shared_items = {i: per for i, per in items.items() if len(per) >= 2}
    annotators = sorted({r["annotator_id"] for r in records})
    pair_names = [list(p) for p in combinations(
        sorted({a for per in shared_items.values() for a in per}), 2)]

    questions, confusions = [], {}
    for question in QUESTIONS:
        answers = answers_for(question, shared_items)
        metrics = (nominal_metrics(answers) if question["kind"] == "nominal"
                   else multilabel_metrics(answers)) if answers else {}
        headline = metrics.get(metrics.get("headline")) if metrics else None
        differing = sum(1 for given in answers.values() if len(set(given.values())) > 1)
        questions.append({
            "name": question["name"],
            "title": question["title"],
            "kind": question["kind"],
            "scope": question["scope"],
            "n_arguments": len(answers),
            "n_annotators": len({a for given in answers.values() for a in given}),
            "n_differ": differing,
            "headline": headline,
            "band": band(headline),
            "undefined_because": why_undefined(question, answers, headline),
            "metrics": {k: v for k, v in metrics.items() if k != "headline"},
        })
        for first, second in pair_names:
            table = confusion(question, answers, first, second)
            if table:
                confusions.setdefault(f"{first}|{second}", {})[question["name"]] = table

    pairs = []
    for first, second in pair_names:
        shared = sorted(i for i, per in shared_items.items() if first in per and second in per)
        per_question = {}
        for question in QUESTIONS:
            answers = answers_for(question, shared_items)
            both = [answers[i] for i in answers if first in answers[i] and second in answers[i]]
            if not both:
                continue
            agree = sum(1 for given in both if given[first] == given[second])
            per_question[question["name"]] = {
                "n": len(both), "agree": agree,
                "observed_agreement": _num(agree / len(both)),
            }
        pairs.append({"annotators": [first, second], "n_shared": len(shared),
                      "questions": per_question})

    return {
        "n_arguments_annotated": len(items),
        "n_shared_arguments": len(shared_items),
        "n_annotators": len(annotators),
        "annotators": annotators,
        "n_differ": sum(1 for per in shared_items.values()
                        if len({(r["sensible_test"], r["primary_mechanism"],
                                 tuple(sorted(r["secondary_mechanisms"])))
                                for r in per.values()}) > 1),
        "questions": questions,
        "pairs": pairs,
        "confusions": confusions,
    }
