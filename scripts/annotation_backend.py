#!/usr/bin/env python3
"""
The annotation tool backend: the things running this study needs that Potato's
own admin dashboard does not do, or does not do readably.

    python scripts/annotation_backend.py                  # http://127.0.0.1:8766
    python scripts/annotation_backend.py --port 9000

  Annotations every annotation there is, grouped by argument and shown side by
              side per annotator, with the argument itself. Filter it by
              annotator, verdict, mechanism, argument number or free text, and
              take the selection away as a CSV. Where two people annotated the
              same argument it says whether they agree.
  Agreement   the inter-annotator agreement coefficients -- Krippendorff's
              alpha, Fleiss' and Cohen's kappa, alpha-MASI -- per question,
              per pair of annotators, with the confusion between them. Potato
              computes the same coefficients at /admin/iaa but only over items
              carrying an annotator cap of 2 or more, and this study sets no
              cap, so that page is all nulls however much overlap there is.
              This computes them over the overlap that is actually on disk.
  Download    the downstream CSV/JSONL and the validation report, generated on
              the click -- not Potato's sparse scheme-and-label export, which
              carries no item fields and no timestamps. Plus a zip of the raw
              annotation_output/. Alongside them, who has done what and how far
              through args_0911.json each person has got, which is the number
              you quote when you hand the next stretch to somebody.
  Flags       every argument somebody ticked `flag_for_discussion`, with the
              argument itself, so they can be read and
              adjudicated in one pass rather than grepped out of a CSV.

Everything here is computed from the files on disk -- `data/instances.json` and
`annotation_output/*/user_state.json` -- so it works with the study server
stopped, which is when you will want it. It changes nothing: no config is
rewritten, no assignment is made, nothing is sent to the study server. Who
annotates which stretch of the corpus is settled between people, by number.

Potato's own dashboard at http://localhost:8765/admin is still there and still
has the live view of the study running -- who is logged in, how long each
argument is taking, the behavioural and integrity reports. This does not
duplicate it.

Loopback only unless you pass --host. On any other interface every request must
carry the study's admin key (`X-API-Key`, or `?key=` for a browser), since this
page hands out every annotation in the study.
"""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement as iaa            # noqa: E402  (needs the path above)
import export_annotations as export  # noqa: E402

INSTANCES = ROOT / "data" / "instances.json"
OUTPUT = ROOT / "annotation_output"
KEY_FILE = ROOT / "admin_api_key.txt"


# --- reading the study off disk ----------------------------------------------

def load_items() -> dict:
    return {i["id"]: i for i in json.loads(INSTANCES.read_text())}


def load_records() -> tuple[list[dict], list[dict], dict]:
    items = load_items()
    if not OUTPUT.exists():
        return [], [], items
    records, problems = export.collect(ROOT, items)
    return records, problems, items


def as_ranges(numbers: set[int]) -> str:
    """{1,2,3,5,9,10} -> "1–3, 5, 9–10".

    A lowest and a highest number could not say whether the stretch between
    them was done: somebody told to take 1–500 who skipped 200 arguments in
    the middle read as "from 1, reached 500", which is the answer to a
    different question. The ranges are the picture, so a gap is visible as a
    gap.
    """
    if not numbers:
        return ""
    runs: list[list[int]] = []
    for number in sorted(numbers):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ", ".join(str(lo) if lo == hi else f"{lo}–{hi}" for lo, hi in runs)


def progress(records: list[dict], items: dict) -> list[dict]:
    """Per annotator: which arguments are finished, and what is outstanding.

    `completed` is the argument numbers whose Sensible Test came out either
    way, as ranges -- what to read to see whether a stretch was really covered,
    and what to quote when handing the next one out. An argument left as a
    draft has no verdict, is not completed, and is counted under `drafts`.
    """
    by_annotator: dict[str, dict] = {}
    for record in records:
        row = by_annotator.setdefault(record["annotator_id"], {
            "annotator": record["annotator_id"], "finished": 0, "drafts": 0,
            "flagged": 0, "completed_numbers": set()})
        if record["sensible_test"] is None:
            row["drafts"] += 1
        else:
            row["finished"] += 1
            number = record.get("item_number")
            if number is not None:
                row["completed_numbers"].add(number)
        if record["flag_for_discussion"]:
            row["flagged"] += 1

    rows = []
    for row in sorted(by_annotator.values(), key=lambda r: r["annotator"]):
        numbers = row.pop("completed_numbers")
        rows.append({**row, "completed": as_ranges(numbers)})
    return rows


# --- browsing the annotations -------------------------------------------------

PER_PAGE = 25


def read_filters(args) -> dict:
    """The Annotations pane's query string, as a dict the rest of this file reads.

    Everything is optional and an absent filter is a filter that passes, so a
    bare /api/annotations is the whole study in argument order.
    """
    def number(name):
        try:
            return int((args.get(name) or "").strip())
        except ValueError:
            return None

    return {
        "annotator": (args.get("annotator") or "").strip(),
        "verdict": (args.get("verdict") or "").strip(),     # Pass | Fail | draft
        "mechanism": (args.get("mechanism") or "").strip(),
        "flagged": args.get("flagged") == "1",
        "overlap": (args.get("overlap") or "").strip(),     # shared | differ
        "lo": number("from"),
        "hi": number("to"),
        "q": (args.get("q") or "").strip().casefold(),
        "sort": (args.get("sort") or "number").strip(),     # number | recent
    }


def matches(record: dict, f: dict) -> bool:
    """One annotation against the filters. `overlap` is not here: it is a
    property of the argument, so it is applied to the groups further down."""
    if f["annotator"] and record["annotator_id"] != f["annotator"]:
        return False
    if f["verdict"] and (record["sensible_test"] or "draft") != f["verdict"]:
        return False
    if f["mechanism"] and f["mechanism"] != record["primary_mechanism"] \
            and f["mechanism"] not in record["secondary_mechanisms"]:
        return False
    if f["flagged"] and not record["flag_for_discussion"]:
        return False
    number = record.get("item_number")
    if f["lo"] is not None and (number is None or number < f["lo"]):
        return False
    if f["hi"] is not None and (number is None or number > f["hi"]):
        return False
    if f["q"]:
        hay = " ".join(str(v) for v in (
            record["instance_id"], record["meme_id"], record["relation"],
            record["annotator_id"], record["primary_mechanism"],
            record["primary_justification"], record["notes"],
            *record["secondary_mechanisms"],
            *record["secondary_justifications"].values()) if v).casefold()
        if f["q"] not in hay:
            return False
    return True


def select(records: list[dict], f: dict) -> list[dict]:
    """The annotations the filters keep, flat and in argument order.

    Shares the group stage's `overlap` handling so the CSV download and the
    pane can never disagree about what is selected.
    """
    return [r for group in group_arguments(records, f) for r in group["annotations"]]


def group_arguments(records: list[dict], f: dict) -> list[dict]:
    """The selected annotations, grouped by the argument they are about.

    An argument is here if at least one of its annotations passed the filters,
    and it carries those annotations only -- so filtering by annotator gives
    one block per argument, not a comparison with somebody who was filtered
    out. `differs` names the fields two annotators on the same argument did not
    answer the same way, which is the only reason to look at them side by side.
    """
    groups: dict[str, dict] = {}
    for record in records:
        if not matches(record, f):
            continue
        group = groups.get(record["instance_id"])
        if group is None:
            group = groups[record["instance_id"]] = {
                "instance_id": record["instance_id"],
                "item_number": record["item_number"],
                "meme_id": record["meme_id"],
                "relation": record["relation"],
                "claim_id": record["claim_id"],
                "annotations": [],
            }
        group["annotations"].append(record)

    out = []
    for group in groups.values():
        annotations = sorted(group["annotations"], key=lambda r: r["annotator_id"])
        group["annotations"] = annotations
        group["n_annotators"] = len(annotations)
        group["flagged"] = any(r["flag_for_discussion"] for r in annotations)
        group["differs"] = [
            field for field in ("sensible_test", "primary_mechanism")
            if len({json.dumps(r[field]) for r in annotations}) > 1
        ] + (["secondary_mechanisms"] if len(
            {tuple(r["secondary_mechanisms"]) for r in annotations}) > 1 else [])
        group["last_saved_utc"] = max(
            (r["last_saved_utc"] for r in annotations if r["last_saved_utc"]), default=None)
        if f["overlap"] == "shared" and group["n_annotators"] < 2:
            continue
        if f["overlap"] == "differ" and not (group["n_annotators"] > 1 and group["differs"]):
            continue
        out.append(group)

    if f["sort"] == "recent":
        out.sort(key=lambda g: (g["last_saved_utc"] or "", g["item_number"] or 0), reverse=True)
    else:
        out.sort(key=lambda g: (g["item_number"] is None, g["item_number"], g["instance_id"]))
    return out


def flag_rows(records: list[dict], items: dict) -> list[dict]:
    """Only what somebody actually ticked `flag_for_discussion` on.

    A note is not a flag. Notes are ordinary annotation content -- they sit in
    the export and beside their annotation on the Annotations tab -- and
    treating one as a queue item made the queue mostly people thinking aloud,
    which is the surest way to stop it being read. Asking for attention is the
    checkbox, and only the checkbox.
    """
    rows = []
    for record in records:
        if not record["flag_for_discussion"]:
            continue
        item = items.get(record["instance_id"], {})
        rows.append({
            **{k: record[k] for k in (
                "instance_id", "item_number", "annotator_id", "meme_id", "relation",
                "sensible_test", "primary_mechanism", "primary_justification",
                "flag_for_discussion", "last_saved_utc")},
            "secondary_mechanisms": "|".join(record["secondary_mechanisms"]),
            "argument_html": item.get("argument_html", ""),
        })
    rows.sort(key=lambda r: (r["item_number"] is None, r["item_number"]))
    return rows


# --- the page -----------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Annotation tool backend — VLMD Subtask 1</title>
<link rel="stylesheet" href="/study.css">
<style>
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:0 22px 70px; background:#f7f7fb; color:#23232e;
  font:15px/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; }
main { max-width: 74rem; margin: 0 auto; }
h1 { font-size:1.35rem; margin:0; }
header { padding:1.7rem 0 1rem; display:flex; align-items:baseline; gap:1rem; flex-wrap:wrap; }
header .sub { color:#6a6a80; font-size:0.9rem; }
nav { display:flex; gap:4px; border-bottom:1px solid #e0e0ea; margin-bottom:1.3rem; }
nav button { border:1px solid transparent; border-bottom:none; background:none; cursor:pointer;
  padding:9px 15px; font-size:0.93rem; color:#4a4a68; border-radius:5px 5px 0 0; }
nav button.on { background:#fff; border-color:#e0e0ea; color:#23232e; font-weight:600;
  margin-bottom:-1px; }
section { display:none; } section.on { display:block; }
.card { background:#fff; border:1px solid #e4e4ee; border-radius:6px; padding:16px 18px;
  margin-bottom:14px; }
.card h2 { font-size:1rem; margin:0 0 0.2rem; }
.card p.hint { color:#63637c; font-size:0.88rem; margin:0.25rem 0 0.8rem; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
.tile { background:#fff; border:1px solid #e4e4ee; border-radius:6px; padding:11px 15px; min-width:8.5rem; }
.tile .n { font-size:1.45rem; font-weight:650; font-variant-numeric:tabular-nums; }
.tile .k { font-size:0.72rem; letter-spacing:0.07em; text-transform:uppercase; color:#6a6a80; }
table { border-collapse:collapse; width:100%; font-size:0.9rem; }
th,td { text-align:left; padding:7px 9px; border-bottom:1px solid #eeeef4; vertical-align:top; }
th { font-size:0.7rem; letter-spacing:0.07em; text-transform:uppercase; color:#6a6a80; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
a.btn { display:inline-block; padding:7px 13px; margin:0 6px 6px 0; border-radius:5px;
  border:1px solid #c3c3dc; background:#f2f2fa; color:#33336b; font-size:0.88rem; font-weight:600;
  text-decoration:none; }
a.btn:hover { background:#e7e7f6; }
.pill { font-size:0.7rem; font-weight:700; letter-spacing:0.05em; text-transform:uppercase;
  padding:2px 7px; border-radius:3px; }
.pill-flag { background:#fbeaea; color:#6d2020; }
details.arg summary { cursor:pointer; color:#4a4a7a; font-size:0.85rem; }
details.arg .arg { margin-top:8px; }
.note-text { white-space:pre-wrap; }
.muted { color:#8a8a9e; }
.num { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.8rem; color:#6a6a80; }

/* --- the Annotations pane --------------------------------------------------
 * Two annotators on one argument go side by side, because the only reason to
 * look at them together is to see where they part company. One annotator gets
 * the full width; the grid does both without a second layout. */
.filters { display:flex; gap:10px 14px; flex-wrap:wrap; align-items:flex-end; }
.filters label { display:flex; flex-direction:column; gap:3px;
  font-size:0.7rem; letter-spacing:0.07em; text-transform:uppercase; color:#6a6a80; }
.filters select, .filters input { font:inherit; font-size:0.88rem; padding:5px 7px;
  border:1px solid #d3d3e2; border-radius:4px; background:#fff; color:#23232e; }
.filters input.n { width:5.5rem; }
.filters input.q { width:15rem; }
.filters .check { flex-direction:row; align-items:center; gap:6px; text-transform:none;
  letter-spacing:0; font-size:0.85rem; color:#4a4a68; padding-bottom:6px; }
.filters button { font:inherit; font-size:0.85rem; padding:6px 12px; border-radius:5px;
  border:1px solid #c3c3dc; background:#f2f2fa; color:#33336b; cursor:pointer; }
.filters button:hover { background:#e7e7f6; }
.result-line { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap;
  margin:14px 0 4px; font-size:0.88rem; color:#63637c; }
.grp-head { display:flex; align-items:baseline; gap:9px; flex-wrap:wrap;
  padding-bottom:9px; border-bottom:1px solid #eeeef4; margin-bottom:11px; }
.anns { display:grid; gap:11px; grid-template-columns:repeat(auto-fit, minmax(21rem, 1fr)); }
.ann { border:1px solid #ebebf3; border-radius:5px; padding:10px 12px; background:#fcfcfe; }
.ann-head { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin-bottom:7px; }
.ann-who { font-weight:650; }
.ann dl { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; margin:0 0 7px;
  font-size:0.86rem; }
.ann dt { color:#6a6a80; }
.ann dd { margin:0; }
.mech { margin:7px 0 0; font-size:0.9rem; }
.mech b { font-weight:650; }
.mech .why { color:#3a3a52; }
.mech.secondary { color:#4a4a68; font-size:0.86rem; padding-left:11px;
  border-left:2px solid #e2e2ec; }
.pill-pass { background:#e8f1ea; color:#23503a; }
.pill-fail { background:#fbeaea; color:#6d2020; }
.pill-draft { background:#fdf0e0; color:#7a4508; }
.pill-differ { background:#fdf0e0; color:#7a4508; }
.pill-agree { background:#e8f1ea; color:#23503a; }
.pager { display:flex; gap:7px; align-items:center; margin:14px 0 0; }
.pager button { font:inherit; font-size:0.85rem; padding:5px 11px; border-radius:5px;
  border:1px solid #c3c3dc; background:#f2f2fa; color:#33336b; cursor:pointer; }
.pager button:disabled { opacity:0.4; cursor:default; }

/* --- the Agreement pane ----------------------------------------------------
 * A coefficient on its own is unreadable: the same 0.61 is a finding on four
 * hundred arguments and noise on four. So every number is shown beside what it
 * was computed on, and an undefined one says why it is undefined instead of
 * printing null. */
table.iaa td { border-bottom:none; padding-top:11px; padding-bottom:2px; }
table.iaa tr.scope td { padding:0 9px 11px; border-bottom:1px solid #eeeef4;
  color:#6a6a80; font-size:0.82rem; }
table.iaa tr.scope td b { color:#4a4a68; font-weight:600; }
table.iaa .q { font-weight:650; }
.headline { display:flex; align-items:baseline; gap:7px; justify-content:flex-end; }
.headline .v { font-size:1.15rem; font-weight:650; font-variant-numeric:tabular-nums; }
.headline .v.none { color:#a0a0b4; font-weight:500; font-size:1rem; }
.band { font-size:0.62rem; font-weight:700; letter-spacing:0.05em; text-transform:uppercase;
  padding:2px 6px; border-radius:3px; white-space:nowrap; }
.band-poor, .band-slight { background:#fbeaea; color:#6d2020; }
.band-fair { background:#fdf0e0; color:#7a4508; }
.band-moderate { background:#fdf8e0; color:#6b5906; }
.band-substantial, .band-almost.perfect, .band-almost-perfect { background:#e8f1ea; color:#23503a; }
.metric-head { font-weight:600; color:#4a4a68; }
th.raw, .tile .k.raw { text-transform:none; letter-spacing:0.02em; font-size:0.78rem; }
.matrix { border-collapse:collapse; margin-top:11px; font-size:0.86rem; }
.matrix th, .matrix td { border:1px solid #e8e8f0; padding:6px 10px; text-align:center;
  font-variant-numeric:tabular-nums; }
.matrix th { font-size:0.72rem; letter-spacing:0.03em; text-transform:none; color:#4a4a68;
  background:#fafaff; font-weight:600; max-width:8.5rem; }
.matrix th.side { text-align:right; }
.matrix td.same { background:#eef5f0; color:#23503a; font-weight:650; }
.matrix td.differ { background:#fdf0e0; color:#7a4508; font-weight:650; }
.matrix td.zero { color:#c4c4d4; }
.matrix caption { caption-side:top; text-align:left; color:#6a6a80; font-size:0.82rem;
  padding-bottom:4px; }
.axis { color:#6a6a80; font-size:0.8rem; margin:10px 0 0; }
.empty { text-align:center; padding:26px 18px; color:#5a5a72; }
.empty .big { font-size:1.05rem; font-weight:600; color:#33334d; margin-bottom:6px; }
</style></head><body><main>
<header>
  <h1>Annotation tool backend</h1>
  <span class="sub">VLMD Subtask 1</span>
</header>
<nav>
  <button class="on" data-pane="download">Download</button>
  <button data-pane="annotations">Annotations</button>
  <button data-pane="agreement">Agreement</button>
  <button data-pane="flags">Flags</button>
</nav>

<section id="download" class="on">
  <div class="tiles" id="tiles"></div>
  <div class="card">
    <h2>Who is where</h2>
    <p class="hint">Argument numbers are the numbers in <code>args_0911.json</code> and the
    numbers on screen. <b>Completed</b> is every argument they have finished, as ranges, so a
    stretch somebody skipped shows up as a gap rather than hiding inside a first-and-last
    pair &mdash; it is what to read before handing the next stretch over. A <b>draft</b> is an
    argument they left part-answered; it is not finished work, it is not in Completed, and the
    export refuses it under <code>--strict</code>. To see <i>which</i> ones are drafts or
    flags, filter the Annotations tab by that annotator.</p>
    <table><thead><tr><th>Annotator</th><th>Completed</th><th class="n">Drafts</th>
      <th class="n">Flags</th></tr></thead><tbody id="who"></tbody></table>
  </div>
  <div class="card">
    <h2>Download</h2>
    <p class="hint">Generated from <code>annotation_output/</code> on the click. The CSV and
    the JSONL are the downstream schema; the report is every constraint violation, with ids.</p>
    <a class="btn" href="/download/annotations.csv">annotations.csv</a>
    <a class="btn" href="/download/annotations.jsonl">annotations.jsonl</a>
    <a class="btn" href="/download/validation_report.json">validation_report.json</a>
    <a class="btn" href="/download/agreement.json">agreement.json</a>
    <a class="btn" href="/download/annotation_output.zip">annotation_output.zip</a>
    <p class="hint" id="problem-line"></p>
  </div>
</section>

<section id="annotations">
  <div class="card">
    <h2>Annotations by argument</h2>
    <p class="hint">Every annotation there is, under the argument it is about. Where two
    people annotated the same argument they sit side by side and the argument is marked
    <b>differ</b> if their verdict or their mechanisms are not the same. A filter picks
    <i>annotations</i>: an argument is listed when one of its annotations matches, showing
    those. <b>Shared / disagreements</b> then narrows that to arguments more than one
    person is in.</p>
    <div class="filters" id="filters">
      <label>Annotator<select id="f-annotator"><option value="">everybody</option></select></label>
      <label>Verdict<select id="f-verdict">
        <option value="">any</option><option value="Pass">Pass</option>
        <option value="Fail">Fail</option><option value="draft">draft</option></select></label>
      <label>Mechanism<select id="f-mechanism"><option value="">any</option></select></label>
      <label>Overlap<select id="f-overlap">
        <option value="">all arguments</option>
        <option value="shared">2+ annotators</option>
        <option value="differ">disagreements</option></select></label>
      <label>Argument from<input class="n" id="f-from" type="number" min="1" placeholder="1"></label>
      <label>to<input class="n" id="f-to" type="number" min="1" placeholder="1621"></label>
      <label>Text<input class="q" id="f-q" type="search"
        placeholder="justifications, notes, meme"></label>
      <label>Order<select id="f-sort">
        <option value="number">argument number</option>
        <option value="recent">most recently saved</option></select></label>
      <label class="check"><input id="f-flagged" type="checkbox"> flagged only</label>
      <button id="f-clear" type="button">Clear</button>
    </div>
    <div class="result-line">
      <span id="ann-count" class="muted">Loading&hellip;</span>
      <a class="btn" id="ann-csv" href="/download/selection.csv">these annotations as CSV</a>
    </div>
  </div>
  <div id="ann-rows"></div>
  <div class="pager" id="ann-pager"></div>
</section>

<section id="agreement">
  <div id="iaa-empty"></div>
  <div id="iaa-body" hidden>
    <div class="tiles" id="iaa-tiles"></div>
    <div class="card">
      <h2>Agreement by question</h2>
      <p class="hint">Computed over the arguments two or more people have actually annotated,
      question by question. <b>Krippendorff&rsquo;s &alpha;</b> is the headline because it is the
      one that copes with annotators having answered different arguments, which is what
      happens when work is handed out by number; <b>observed</b> beside it is the plain
      proportion of answers that match, uncorrected for the agreement chance alone would
      produce. Read them together with <b>arguments</b>: a coefficient over a handful of
      arguments is not yet a finding. Bands are Landis &amp; Koch, a convention and not a
      threshold anybody has to meet. A &kappa; near zero beside a high <b>observed</b> is not
      a contradiction and usually not a problem: when nearly every argument gets the same
      answer, chance agreement is already almost total and there is little left for a
      chance-corrected coefficient to credit.</p>
      <table class="iaa"><thead><tr><th>Question</th><th class="n">Arguments</th>
        <th class="n">Differ</th><th class="n">Observed</th>
        <th class="n raw">Krippendorff &alpha;</th><th class="n raw">Fleiss &kappa;</th>
        <th class="n raw">Cohen &kappa;</th></tr></thead><tbody id="iaa-nominal"></tbody></table>
    </div>
    <div class="card">
      <h2>Secondary mechanisms</h2>
      <p class="hint">A multi-select is not scored like a single choice: two people who both
      chose three mechanisms and share two of them have not simply disagreed.
      <b>&alpha;-MASI</b> is Krippendorff&rsquo;s &alpha; with a set distance that counts a
      subset as closer than an unrelated set; <b>Jaccard</b> is the mean overlap of the two
      sets; <b>exact match</b> is how often the sets are identical.</p>
      <table class="iaa"><thead><tr><th>Question</th><th class="n">Arguments</th>
        <th class="n">Differ</th><th class="n">Exact match</th><th class="n raw">&alpha;-MASI</th>
        <th class="n">Mean Jaccard</th></tr></thead><tbody id="iaa-multi"></tbody></table>
    </div>
    <div class="card">
      <h2>Between each pair</h2>
      <p class="hint">Plain observed agreement per pair of annotators, on the arguments that
      pair both answered. This is the table that says whether one person is the odd one out;
      the coefficients above pool everybody.</p>
      <div style="overflow-x:auto"><table id="iaa-pairs"></table></div>
    </div>
    <div class="card">
      <h2>Where they part company</h2>
      <p class="hint">Every shared answer to one question, counted once: what the first
      annotator said down the side, what the second said across the top. The diagonal is
      agreement; everything off it is a pair of arguments worth reading. This is the table
      that tells you <i>which</i> two mechanisms are being confused for each other, which is
      the thing a single coefficient cannot.</p>
      <div class="filters">
        <label>Pair<select id="iaa-pair"></select></label>
        <label>Question<select id="iaa-question"></select></label>
      </div>
      <div id="iaa-matrix"></div>
      <p class="axis"><a class="btn" href="#" id="iaa-to-disagreements">read the disagreements
        in Annotations</a></p>
    </div>
  </div>
</section>

<section id="flags">
  <div class="card">
    <h2>Flagged arguments</h2>
    <p class="hint">Arguments somebody ticked <b>flag for discussion</b> on, in argument order.
    A queue, not a workflow: somebody still has to read it. Notes are not flags &mdash; they
    are on the Annotations tab, beside the annotation they belong to.</p>
    <a class="btn" href="/download/flags.csv">flags.csv</a>
    <div id="flag-rows"><p class="muted">Loading&hellip;</p></div>
  </div>
</section>
</main>
<script>
const KEY = new URLSearchParams(location.search).get('key');
const url = p => p + (KEY ? (p.includes('?') ? '&' : '?') + 'key=' + encodeURIComponent(KEY) : '');
const get = p => fetch(url(p)).then(r => r.json());

// Every link on the page carries the key too, not just the fetches. On a
// console reached through its own domain the key lives in the address bar and
// nowhere else: a browser does not replay it onto an <a href> the way url()
// does for fetch(), so the six Download buttons and flags.csv all came back
// 403. Rewriting at boot means a link added later is covered without anybody
// remembering this. Anchors that are not paths (the '#' jump to Annotations)
// are left alone.
document.querySelectorAll('a[href^="/"]').forEach(
  a => a.setAttribute('href', url(a.getAttribute('href'))));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

document.querySelectorAll('nav button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button').forEach(x => x.classList.toggle('on', x === b));
  document.querySelectorAll('section').forEach(s => s.classList.toggle('on', s.id === b.dataset.pane));
  if (b.dataset.pane === 'flags') loadFlags();
  if (b.dataset.pane === 'annotations') loadAnnotations(1);
  if (b.dataset.pane === 'agreement') loadAgreement();
});

function tile(n, k, cls) {
  // `cls` is for a label carrying a Greek letter: the uppercasing the others
  // get turns an alpha into a capital A, which reads as a grade.
  return `<div class="tile"><div class="n">${n}</div>
    <div class="k ${cls || ''}">${k}</div></div>`;
}

async function loadSummary() {
  const d = await get('/api/summary');
  document.getElementById('tiles').innerHTML =
    tile(d.covered.toLocaleString() + '/' + d.arguments.toLocaleString(), 'arguments covered')
    + tile(d.annotations.toLocaleString(), 'annotations')
    + tile(d.drafts.toLocaleString(), 'drafts')
    + tile(d.annotators, 'annotators')
    + tile(d.verdicts.Pass, 'pass') + tile(d.verdicts.Fail, 'fail')
    + tile(d.flags, 'flags')
    + tile(d.problems, 'export problems');
  document.getElementById('problem-line').textContent = d.problems
    ? d.problems + ' constraint violation(s) — they are listed in validation_report.json'
    : 'No constraint violations in what has been annotated so far.';
  document.getElementById('who').innerHTML = d.who.length
    ? d.who.map(a => `<tr><td>${esc(a.annotator)}</td>
        <td><span class="num">${esc(a.completed || '—')}</span>${
          a.finished ? ` <span class="muted">(${a.finished})</span>` : ''}</td>
        <td class="n">${a.drafts}</td><td class="n">${a.flagged}</td></tr>`).join('')
    : '<tr><td colspan="4" class="muted">Nobody has annotated anything yet.</td></tr>';
}

// --- Annotations --------------------------------------------------------------

const FILTERS = {annotator:'f-annotator', verdict:'f-verdict', mechanism:'f-mechanism',
  overlap:'f-overlap', from:'f-from', to:'f-to', q:'f-q', sort:'f-sort'};
let annPage = 1, annReady = false;

function annQuery() {
  const q = new URLSearchParams();
  for (const [name, id] of Object.entries(FILTERS)) {
    const v = document.getElementById(id).value.trim();
    if (v) q.set(name, v);
  }
  if (document.getElementById('f-flagged').checked) q.set('flagged', '1');
  return q;
}

function duration(ms) {
  if (!ms) return '';
  const s = Math.round(ms / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
}

const verdictPill = v =>
  `<span class="pill pill-${v ? v.toLowerCase() : 'draft'}">${esc(v ?? 'draft')}</span>`;

function annotation(a) {
  const secondaries = (a.secondary_mechanisms || []).map(m =>
    `<p class="mech secondary"><b>${esc(m)}</b> &mdash;
       <span class="why">${esc(a.secondary_justifications[m] ?? '')}</span></p>`).join('');
  return `<div class="ann">
    <div class="ann-head">
      <span class="ann-who">${esc(a.annotator_id)}</span>
      ${verdictPill(a.sensible_test)}
      ${a.flag_for_discussion ? '<span class="pill pill-flag">flag</span>' : ''}
      <span class="muted">${esc(a.last_saved_utc ?? '—')}${
        a.time_on_item_ms ? ' &middot; ' + duration(a.time_on_item_ms) : ''}</span>
    </div>
    ${a.primary_mechanism
      ? `<p class="mech"><b>${esc(a.primary_mechanism)}</b> &mdash;
           <span class="why">${esc(a.primary_justification ?? '')}</span></p>`
      : '<p class="mech muted">no mechanism</p>'}
    ${secondaries}
    ${a.notes ? `<p class="mech note-text"><b>Notes:</b> ${esc(a.notes)}</p>` : ''}
  </div>`;
}

const FIELD_NAMES = {sensible_test:'verdict', primary_mechanism:'primary mechanism',
  secondary_mechanisms:'secondary mechanisms'};

function argumentGroup(g) {
  const differ = g.n_annotators > 1
    ? (g.differs.length
        ? `<span class="pill pill-differ">differ on ${
             g.differs.map(f => FIELD_NAMES[f]).join(', ')}</span>`
        : '<span class="pill pill-agree">agree</span>')
    : '';
  return `<div class="card">
    <div class="grp-head">
      <span class="num">Argument ${g.item_number ?? '—'}</span>
      <span class="muted">${esc(g.instance_id)} &middot; ${esc(g.meme_id)}
        &middot; ${esc(g.relation)}</span>
      ${differ}
      ${g.n_annotators > 1 ? `<span class="muted">${g.n_annotators} annotators</span>` : ''}
    </div>
    <div class="anns">${g.annotations.map(annotation).join('')}</div>
    <details class="arg">
      <summary>the argument</summary>${g.argument_html}</details>
  </div>`;
}

async function loadAnnotations(page) {
  annPage = page;
  const query = annQuery();
  document.getElementById('ann-csv').href = url('/download/selection.csv?' + query);
  query.set('page', page);
  const d = await get('/api/annotations?' + query);

  if (!annReady) {  // the two dropdowns the study itself decides the contents of
    const annotator = document.getElementById('f-annotator');
    annotator.innerHTML = '<option value="">everybody</option>' +
      d.annotators.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
    document.getElementById('f-mechanism').innerHTML = '<option value="">any</option>' +
      d.mechanisms.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    annReady = true;
  }

  document.getElementById('ann-count').textContent = d.n_arguments
    ? `${d.n_annotations.toLocaleString()} annotation(s) on ${
        d.n_arguments.toLocaleString()} argument(s)` +
      (d.n_annotations < d.total_annotations
        ? ` — filtered from ${d.total_annotations.toLocaleString()}` : '')
    : 'Nothing matches these filters.';

  // Arguments start collapsed, always. They used to spring open whenever the
  // filters narrowed to three or fewer, which meant the page reflowed into
  // something different as you typed and a small study never saw the
  // collapsed form at all.
  document.getElementById('ann-rows').innerHTML =
    d.groups.map(argumentGroup).join('');

  document.getElementById('ann-pager').innerHTML = d.pages > 1
    ? `<button ${d.page <= 1 ? 'disabled' : ''} data-page="${d.page - 1}">Previous</button>
       <span class="muted">page ${d.page} of ${d.pages}</span>
       <button ${d.page >= d.pages ? 'disabled' : ''} data-page="${d.page + 1}">Next</button>`
    : '';
  document.querySelectorAll('#ann-pager button').forEach(b =>
    b.onclick = () => { loadAnnotations(Number(b.dataset.page)); window.scrollTo(0, 0); });
}

document.querySelectorAll('#filters select, #filters input').forEach(el => {
  const rerun = () => loadAnnotations(1);
  el.addEventListener('change', rerun);
  if (el.type === 'search' || el.type === 'number') {
    let timer;
    el.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(rerun, 350); });
  }
});
document.getElementById('f-clear').onclick = () => {
  Object.values(FILTERS).forEach(id => {
    const el = document.getElementById(id);
    el.value = id === 'f-sort' ? 'number' : '';
  });
  document.getElementById('f-flagged').checked = false;
  loadAnnotations(1);
};

// --- Agreement ----------------------------------------------------------------

const SHORT = {sensible_test:'Verdict', primary_mechanism:'Primary',
  secondary_mechanisms:'Secondary', flag_for_discussion:'Flag'};

let iaa = null;

const coef = v => (v === null || v === undefined) ? '&mdash;' : v.toFixed(2);
const pct = v => (v === null || v === undefined) ? '&mdash;' : Math.round(v * 100) + '%';

function headline(q) {
  if (q.headline === null || q.headline === undefined)
    return '<div class="headline"><span class="v none">&mdash;</span></div>';
  return `<div class="headline"><span class="v">${q.headline.toFixed(2)}</span>
    <span class="band band-${esc(q.band).replace(/ /g, '-')}">${esc(q.band)}</span></div>`;
}

function scopeRow(q, span) {
  const why = q.undefined_because
    ? ` <b>Undefined:</b> ${esc(q.undefined_because)}.` : '';
  return `<tr class="scope"><td colspan="${span}"><b>Scored on</b> ${esc(q.scope)}.${why}</td></tr>`;
}

function questionRow(q) {
  const m = q.metrics;
  return `<tr><td class="q">${esc(q.title)}</td>
    <td class="n">${q.n_arguments}</td><td class="n">${q.n_differ}</td>
    <td class="n">${pct(m.observed_agreement)}</td>
    <td class="n">${headline(q)}</td>
    <td class="n">${coef(m.fleiss_kappa)}</td>
    <td class="n">${coef(m.mean_cohen_kappa)}</td></tr>` + scopeRow(q, 7);
}

function multiRow(q) {
  const m = q.metrics;
  return `<tr><td class="q">${esc(q.title)}</td>
    <td class="n">${q.n_arguments}</td><td class="n">${q.n_differ}</td>
    <td class="n">${pct(m.exact_match)}</td>
    <td class="n">${headline(q)}</td>
    <td class="n">${coef(m.mean_jaccard)}</td></tr>` + scopeRow(q, 6);
}

function pairsTable(d) {
  const names = d.questions.map(q => q.name);
  const head = `<thead><tr><th>Pair</th><th class="n">Shared arguments</th>` +
    names.map(n => `<th class="n">${esc(SHORT[n] || n)}</th>`).join('') + `</tr></thead>`;
  const body = d.pairs.map(p => `<tr>
    <td>${esc(p.annotators[0])} &middot; ${esc(p.annotators[1])}</td>
    <td class="n">${p.n_shared}</td>` +
    names.map(n => {
      const cell = p.questions[n];
      return cell
        ? `<td class="n">${pct(cell.observed_agreement)}<br>
             <span class="muted num">${cell.agree}/${cell.n}</span></td>`
        : '<td class="n muted">&mdash;</td>';
    }).join('') + '</tr>').join('');
  return head + '<tbody>' + body + '</tbody>';
}

function renderMatrix() {
  const box = document.getElementById('iaa-matrix');
  const pairKey = document.getElementById('iaa-pair').value;
  const question = document.getElementById('iaa-question').value;
  const table = ((iaa.confusions[pairKey] || {})[question]);
  if (!table) { box.innerHTML = '<p class="muted">Nothing shared on this question yet.</p>'; return; }
  const [first, second] = pairKey.split('|');
  const title = (iaa.questions.find(q => q.name === question) || {}).title || question;
  box.innerHTML = `<table class="matrix">
    <caption>${esc(title)} &mdash; ${table.n} shared answer(s), ${table.n_agree} the same.
      Rows: <b>${esc(first)}</b>. Columns: <b>${esc(second)}</b>.</caption>
    <tr><th></th>${table.labels.map(l => `<th>${esc(l)}</th>`).join('')}</tr>
    ${table.rows.map((row, i) => `<tr><th class="side">${esc(table.labels[i])}</th>` +
      row.map((n, j) => `<td class="${n === 0 ? 'zero' : (i === j ? 'same' : 'differ')}">${n}</td>`)
        .join('') + '</tr>').join('')}
  </table>`;
}

async function loadAgreement() {
  // Recomputed on every visit, like the other panes: somebody is annotating
  // while this is open. The two dropdowns are the only state worth keeping,
  // so they are put back if the pair and the question are still there.
  const was = {pair: (document.getElementById('iaa-pair') || {}).value,
               question: (document.getElementById('iaa-question') || {}).value};
  iaa = await get('/api/agreement');
  const body = document.getElementById('iaa-body');
  const empty = document.getElementById('iaa-empty');

  if (!iaa.n_shared_arguments) {
    body.hidden = true;
    empty.innerHTML = `<div class="card empty">
      <div class="big">No two people have annotated the same argument yet.</div>
      <p>Agreement is a property of overlap, and there is none: ${iaa.n_annotators}
      annotator(s) have between them annotated ${iaa.n_arguments_annotated.toLocaleString()}
      argument(s), each one once. Hand the same stretch of argument numbers to a second
      annotator &mdash; &ldquo;start at 500, stop at 800&rdquo; to both &mdash; and every
      coefficient on this page fills in by itself.</p>
      <p class="muted">Potato&rsquo;s own report at <code>/admin/iaa</code> says the same
      thing in nulls.</p></div>`;
    return;
  }
  empty.innerHTML = '';
  body.hidden = false;

  const verdict = iaa.questions.find(q => q.name === 'sensible_test') || {};
  const primary = iaa.questions.find(q => q.name === 'primary_mechanism') || {};
  document.getElementById('iaa-tiles').innerHTML =
    tile(iaa.n_shared_arguments.toLocaleString(), 'shared arguments')
    + tile(iaa.n_differ.toLocaleString(), 'read differently')
    + tile(iaa.n_annotators, 'annotators')
    + tile(iaa.pairs.length, 'pairs')
    + tile(coef(verdict.headline), 'verdict &alpha;', 'raw')
    + tile(coef(primary.headline), 'mechanism &alpha;', 'raw');

  document.getElementById('iaa-nominal').innerHTML =
    iaa.questions.filter(q => q.kind === 'nominal').map(questionRow).join('');
  document.getElementById('iaa-multi').innerHTML =
    iaa.questions.filter(q => q.kind === 'multilabel').map(multiRow).join('');
  document.getElementById('iaa-pairs').innerHTML = pairsTable(iaa);

  const pairSelect = document.getElementById('iaa-pair');
  const questionSelect = document.getElementById('iaa-question');
  const keys = Object.keys(iaa.confusions);
  pairSelect.innerHTML = keys.map(k =>
    `<option value="${esc(k)}">${esc(k.replace('|', ' \u00b7 '))}</option>`).join('');
  if (keys.includes(was.pair)) pairSelect.value = was.pair;
  const fillQuestions = () => {
    // Codebook order, not whatever order the JSON came back in, and opened on
    // the mechanism: which one got picked instead of which is the question
    // this table exists to answer.
    const available = iaa.questions.filter(q => (iaa.confusions[pairSelect.value] || {})[q.name]);
    questionSelect.innerHTML = available.map(q =>
      `<option value="${esc(q.name)}">${esc(q.title)}</option>`).join('');
    const names = available.map(q => q.name);
    const want = names.includes(was.question) ? was.question
      : (names.includes('primary_mechanism') ? 'primary_mechanism' : names[0]);
    if (want) questionSelect.value = want;
  };
  fillQuestions();
  pairSelect.onchange = () => { fillQuestions(); renderMatrix(); };
  questionSelect.onchange = renderMatrix;
  if (keys.length) renderMatrix();

  document.getElementById('iaa-to-disagreements').onclick = ev => {
    ev.preventDefault();
    document.getElementById('f-overlap').value = 'differ';
    document.querySelector('nav button[data-pane="annotations"]').click();
  };
}

async function loadFlags() {
  const d = await get('/api/flags');
  const box = document.getElementById('flag-rows');
  if (!d.rows.length) { box.innerHTML = '<p class="muted">Nothing flagged.</p>'; return; }
  box.innerHTML = d.rows.map(r => `<div class="card">
    <div><span class="num">Argument ${r.item_number ?? '—'}</span>
      <span class="pill pill-flag">flag</span>
      <span class="muted">${esc(r.instance_id)} &middot; ${esc(r.annotator_id)}
      &middot; ${esc(r.relation)} &middot; ${esc(r.sensible_test ?? 'draft')}</span></div>
    ${r.primary_mechanism ? `<p><b>${esc(r.primary_mechanism)}</b>${r.secondary_mechanisms ? ' + ' + esc(r.secondary_mechanisms) : ''}<br>
      ${esc(r.primary_justification)}</p>` : ''}
    <details class="arg"><summary>the argument</summary>${r.argument_html}</details>
  </div>`).join('');
}

loadSummary();
</script></body></html>
"""


# --- the app ------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)

    @app.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index():
        return Response(PAGE, mimetype="text/html")

    @app.route("/api/summary")
    def api_summary():
        records, problems, items = load_records()
        verdicts = {"Pass": 0, "Fail": 0}
        for record in records:
            if record["sensible_test"] in verdicts:
                verdicts[record["sensible_test"]] += 1
        # Arguments, not annotations: two people finishing the same argument is
        # one argument covered and two annotations recorded, and a tile reading
        # "12/1621" that meant annotations would overstate coverage by exactly
        # the amount of overlap.
        return jsonify({
            "arguments": len(items),
            "covered": len({r["instance_id"] for r in records
                            if r["sensible_test"] is not None}),
            "annotations": sum(verdicts.values()),
            "drafts": sum(1 for r in records if r["sensible_test"] is None),
            "annotators": len({r["annotator_id"] for r in records}),
            "verdicts": verdicts,
            "flags": len(flag_rows(records, items)),
            "problems": len(problems),
            "who": progress(records, items),
        })

    @app.route("/api/annotations")
    def api_annotations():
        records, _, items = load_records()
        f = read_filters(request.args)
        groups = group_arguments(records, f)

        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        pages = max(1, -(-len(groups) // PER_PAGE))
        page = min(page, pages)
        shown = groups[(page - 1) * PER_PAGE: page * PER_PAGE]

        # The argument itself is ~1.2 kB and there are 1,621 of them, so it
        # goes out for the page being looked at and not for the selection.
        for group in shown:
            group["argument_html"] = items.get(
                group["instance_id"], {}).get("argument_html", "")

        return jsonify({
            "groups": shown,
            "page": page,
            "pages": pages,
            "per_page": PER_PAGE,
            "n_arguments": len(groups),
            "n_annotations": sum(g["n_annotators"] for g in groups),
            "total_annotations": len(records),
            "annotators": sorted({r["annotator_id"] for r in records}),
            "mechanisms": export.MECHANISMS,
        })

    @app.route("/api/agreement")
    def api_agreement():
        records, _, _ = load_records()
        return jsonify(iaa.report(records))

    @app.route("/api/flags")
    def api_flags():
        records, _, items = load_records()
        return jsonify({"rows": flag_rows(records, items)})

    # --- downloads ------------------------------------------------------------

    @app.route("/download/annotations.csv")
    def download_csv():
        records, _, _ = load_records()
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=export.CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(export.csv_row(record))
        return attachment(buffer.getvalue(), "annotations.csv", "text/csv")

    @app.route("/download/annotations.jsonl")
    def download_jsonl():
        records, _, _ = load_records()
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        return attachment(body, "annotations.jsonl", "application/x-ndjson")

    @app.route("/download/validation_report.json")
    def download_report():
        records, problems, _ = load_records()
        by_kind: dict[str, int] = {}
        for problem in problems:
            by_kind[problem["kind"]] = by_kind.get(problem["kind"], 0) + 1
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_records": len(records),
            "n_annotators": len({r["annotator_id"] for r in records}),
            "n_arguments": len({r["instance_id"] for r in records}),
            "n_problems": len(problems),
            "problems_by_kind": by_kind,
            "problems": problems,
        }
        return attachment(json.dumps(report, indent=1, ensure_ascii=False),
                          "validation_report.json", "application/json")

    @app.route("/download/agreement.json")
    def download_agreement():
        """Every coefficient, every pair and every confusion table, as the page
        shows them -- so the number that goes in the write-up and the number on
        screen came out of the same computation."""
        records, _, _ = load_records()
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **iaa.report(records),
        }
        return attachment(json.dumps(report, indent=1, ensure_ascii=False),
                          "agreement.json", "application/json")

    @app.route("/download/flags.csv")
    def download_flags():
        records, _, items = load_records()
        rows = flag_rows(records, items)
        columns = ["item_number", "instance_id", "meme_id", "relation", "annotator_id",
                   "sensible_test", "primary_mechanism", "primary_justification",
                   "secondary_mechanisms", "flag_for_discussion", "last_saved_utc"]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return attachment(buffer.getvalue(), "flags.csv", "text/csv")

    @app.route("/download/selection.csv")
    def download_selection():
        """Whatever the Annotations pane is showing, in the downstream schema.

        Same columns as annotations.csv -- it is that file with the pane's
        filters applied, so a stretch handed to one person, or every argument
        two people read differently, comes out ready to work on.
        """
        records, _, _ = load_records()
        rows = select(records, read_filters(request.args))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=export.CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for record in rows:
            writer.writerow(export.csv_row(record))
        return attachment(buffer.getvalue(), "selection.csv", "text/csv")

    @app.route("/study.css")
    def study_css():
        """The annotation interface's stylesheet, so an argument shown here --
        in Annotations and in Flags -- reads the way it read on screen."""
        path = ROOT / "static" / "study.css"
        if not path.exists():
            return Response("", mimetype="text/css")
        return Response(path.read_text(), mimetype="text/css")

    @app.route("/download/annotation_output.zip")
    def download_archive():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(OUTPUT.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT))
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return attachment(buffer.getvalue(), f"annotation_output-{stamp}.zip",
                          "application/zip")

    def attachment(body, filename: str, mimetype: str) -> Response:
        return Response(body, mimetype=mimetype, headers={
            "Content-Disposition": f'attachment; filename="{filename}"'})

    return app


def guard(app: Flask, exposed: bool) -> None:
    """On a non-loopback bind, every request carries the study's admin key."""
    if not exposed:
        return

    @app.before_request
    def require_key():
        # The stylesheet is exempt: a browser does not put the key on a <link>,
        # and it is the annotation interface's CSS, not anybody's annotations.
        if request.path == "/study.css":
            return None
        expected = KEY_FILE.read_text().strip() if KEY_FILE.exists() else None
        supplied = request.headers.get("X-API-Key") or request.args.get("key")
        if not expected:
            abort(503, "no admin key to check against; start the study server once, "
                       "which writes admin_api_key.txt")
        if supplied != expected:
            abort(403, "this console is exposed off-loopback and needs the study's "
                       "admin key: append ?key=<admin_api_key.txt> to the URL")


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()

    app = create_app()
    exposed = not is_loopback(args.host)
    guard(app, exposed)

    print(f"annotation tool backend   http://{args.host}:{args.port}")
    if exposed:
        print("EXPOSED off loopback: every request needs the admin key from "
              "admin_api_key.txt, as ?key=...")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
