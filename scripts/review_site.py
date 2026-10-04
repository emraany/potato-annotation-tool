#!/usr/bin/env python3
"""
The review site: two annotators who disagreed, one shared screen, one answer.

    python scripts/review_site.py                  # http://127.0.0.1:8767
    python scripts/review_site.py --port 9000

Its own site, on purpose. It is not Potato -- the study site is the annotators'
-- and it is not the backend console, which is the study owner's and hands out
every annotation there is. This one can do exactly two things: show a pair the
arguments they both annotated, and save how they resolved one.

SIGNING IN is two NetIDs: the pair. Both have to be annotators on disk, and
they have to share at least one argument, or there is nothing to resolve. The
pair is the account -- the queue is their shared arguments and every decision
records both names. As on the study site, the NetIDs are the whole credential:
anybody who knows both can sign in as that pair. That is the same trade the
study already makes (see scripts/accounts.py), made again for the same reason.

THE MAP is the pair's to-do list and nothing more: the tasks the study owner
assigned them on the backend console (a stretch of argument numbers and which
colours in it), each argument in the colour the Agreement tab's map gives it --
same function behind both, agreement.category. Agreement is not on it, and
neither is a disagreement nobody assigned. A pair with no task sees a page
saying so. `Next unresolved` walks their tasks in order.

THE PAGE, top to bottom: the map; the header saying how they differ; the
argument, collapsible; the two of them side by side, each with their verdict,
mechanisms and justifications; and under that the answer they settle on.

EACH ANSWER opens on what both of them agreed: the shared mechanisms ticked,
the verdict filled in if they gave the same one. Every mechanism only one of
them ticked is outlined and labelled with who, unticked, one click from being
added. Nothing reaches gold that nobody decided to put there.

WHAT IT WRITES is annotation_output/review/decisions.json and nothing else.
See scripts/review.py for that file, the one-decision-per-argument rule and
how gold is built from it. The gold download is on the backend console, not
here.

Loopback by default. Off loopback (Railway) the session cookie is marked
Secure, so it only travels over HTTPS.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, session

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agreement as iaa   # noqa: E402
import review            # noqa: E402
from accounts import clean_netid  # noqa: E402

ROOT = review.ROOT


# --- what a pair can see ------------------------------------------------------

def study():
    items = review.load_items()
    records = review.load_records(items)
    return items, records, iaa.by_argument(records)


def shared_by(by_argument: dict, pair: list[str]) -> list[str]:
    a, b = pair
    return [i for i, per in by_argument.items() if a in per and b in per]


def assigned(pair: list[str], items: dict, by_argument: dict) -> dict:
    """instance_id -> the first task of this pair's that covers it."""
    out = {}
    for task in review.tasks_for(pair):
        for row in review.task_arguments(task, items, by_argument):
            out.setdefault(row["instance_id"], (task, row))
    return out


def queue(pair: list[str]) -> dict:
    """The pair's map: their tasks, each with the arguments it covers.

    Only what has been handed to them. Agreement is not on it, and neither is
    any disagreement nobody assigned -- the map is the to-do list, not the
    corpus.
    """
    items, _, by_argument = study()
    decisions = review.load_decisions()
    tasks, seen = [], set()
    for task in review.tasks_for(pair):
        rows = []
        for row in review.task_arguments(task, items, by_argument):
            if row["instance_id"] in seen:     # two overlapping tasks: once
                continue
            seen.add(row["instance_id"])
            decision = decisions.get(row["instance_id"])
            # Resolved, but somebody has changed their answer since: back on
            # the to-do list until the pair look again and save.
            changed = (review.changed_since(decision, review.of_pair(
                           by_argument[row["instance_id"]], pair)) if decision else [])
            rows.append({**row, "resolved": bool(decision) and not changed,
                         "changed": changed,
                         "resolved_by": decision.get("resolved_by") if decision else None})
        tasks.append({"id": task["id"], "label": task.get("label") or "",
                      "from": task["from"], "to": task["to"],
                      "colours": task["colours"], "arguments": rows})
    every = [r for t in tasks for r in t["arguments"]]
    return {
        "pair": pair,
        "tasks": tasks,
        "to_resolve": len(every),
        "resolved": sum(1 for r in every if r["resolved"]),
    }


def argument(pair: list[str], instance_id: str):
    items, _, by_argument = study()
    if instance_id not in assigned(pair, items, by_argument):
        return None
    # The two signed in and nobody else, whoever else annotated it.
    per = review.of_pair(by_argument[instance_id], pair)
    item = items.get(instance_id, {})
    decision = review.load_decisions().get(instance_id)
    if decision:
        decision = {k: v for k, v in decision.items() if k != "history"} | {
            "n_earlier": len(decision.get("history") or []),
            "changed": review.changed_since(decision, per)}
    return {
        "instance_id": instance_id,
        "number": item.get("item_number"),
        "meme_id": item.get("meme_id"),
        "relation": item.get("relation"),
        "argument_html": item.get("argument_html", ""),
        "claim": item.get("claim_content") or "",
        "header": review.header(per),
        "annotations": [
            {"annotator": who,
             "sensible_test": per[who]["sensible_test"],
             "mechanisms": per[who]["mechanisms"],
             "justifications": per[who]["justifications"],
             "notes": per[who]["notes"],
             "flag": per[who]["flag_for_discussion"]}
            for who in pair],
        "decision": decision,
        "mechanisms": review.MECHANISMS,
        "min_chars": review.export.MIN_JUSTIFICATION_CHARS,
    }


# --- the app ------------------------------------------------------------------

def create_app(exposed: bool) -> Flask:
    app = Flask(__name__)
    # A fixed key keeps a pair signed in across a restart; without one they
    # sign in again, which costs two NetIDs and nothing else.
    app.secret_key = (os.environ.get("REVIEW_SECRET_KEY")
                      or os.environ.get("POTATO_SECRET_KEY")
                      or secrets.token_hex(32))
    app.config.update(SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=exposed,
                      SESSION_COOKIE_NAME="review_session")

    @app.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(RuntimeError)
    def unreadable(error):
        # review.load_decisions / load_tasks refuse a corrupt file rather than
        # read it as empty. Say so in a form the page can show.
        return jsonify({"error": str(error)}), 500

    def signed_in():
        pair = session.get("pair")
        return pair if isinstance(pair, list) and len(pair) == 2 else None

    @app.route("/")
    def index():
        if not signed_in():
            return Response(login_page(), mimetype="text/html")
        return Response(PAGE, mimetype="text/html")

    @app.route("/login", methods=["POST"])
    def login():
        typed = [request.form.get("first", ""), request.form.get("second", "")]
        netids = [clean_netid(t) for t in typed]
        if not all(netids):
            bad = next(t for t, n in zip(typed, netids) if not n)
            return Response(login_page(f"“{bad.strip()}” does not look like a NetID.", typed),
                            mimetype="text/html", status=400)
        if netids[0] == netids[1]:
            return Response(login_page("Those are the same NetID. Sign in as the two people "
                                       "who are resolving together.", typed),
                            mimetype="text/html", status=400)
        _, records, by_argument = study()
        known = review.annotators(records)
        missing = [n for n in netids if n not in known]
        if missing:
            why = ("is a test account, not an annotator" if missing[0] in review.NOT_ANNOTATORS
                   else "has not annotated anything")
            return Response(login_page(f"{missing[0]} {why}.", typed),
                            mimetype="text/html", status=400)
        pair = sorted(netids)
        if not shared_by(by_argument, pair):
            return Response(login_page(f"{pair[0]} and {pair[1]} have not annotated any of "
                                       "the same arguments, so there is nothing to resolve.",
                                       typed), mimetype="text/html", status=400)
        session.clear()
        session["pair"] = pair
        return redirect("/")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect("/")

    @app.route("/api/queue")
    def api_queue():
        pair = signed_in()
        if not pair:
            return jsonify({"error": "signed out"}), 401
        return jsonify(queue(pair))

    @app.route("/api/argument/<path:instance_id>")
    def api_argument(instance_id):
        pair = signed_in()
        if not pair:
            return jsonify({"error": "signed out"}), 401
        data = argument(pair, instance_id)
        if data is None:
            return jsonify({"error": "this argument is not assigned to you two"}), 404
        return jsonify(data)

    @app.route("/api/argument/<path:instance_id>", methods=["POST"])
    def api_resolve(instance_id):
        pair = signed_in()
        if not pair:
            return jsonify({"error": "signed out"}), 401
        if not request.is_json:
            return jsonify({"error": "expected JSON"}), 415
        items, _, by_argument = study()
        if instance_id not in assigned(pair, items, by_argument):
            return jsonify({"error": "this argument is not assigned to you two"}), 404
        per = review.of_pair(by_argument[instance_id], pair)
        body = request.get_json(silent=True) or {}
        decision = {
            "sensible_test": body.get("sensible_test"),
            "mechanisms": [m for m in (body.get("mechanisms") or []) if isinstance(m, str)],
            "justifications": {k: v for k, v in (body.get("justifications") or {}).items()
                               if isinstance(k, str) and isinstance(v, str)},
            "note": body.get("note") if isinstance(body.get("note"), str) else "",
        }
        problems = review.validate(decision)
        if problems:
            return jsonify({"problems": problems}), 400
        record = review.save(instance_id, decision, pair, per, items.get(instance_id, {}))
        return jsonify({"ok": True, "resolved_at": record["resolved_at"]})

    @app.route("/study.css")
    def study_css():
        """The annotation interface's stylesheet, so the argument reads the way
        it read when they annotated it."""
        path = ROOT / "static" / "study.css"
        return Response(path.read_text() if path.exists() else "", mimetype="text/css")

    return app


# --- the pages ----------------------------------------------------------------

STYLE = r"""
:root { color-scheme: light;
  --ink:#23232e; --muted:#6a6a80; --line:#e4e4ee; --bg:#f7f7fb; --card:#fff;
  --accent:#33336b; --accent-bg:#f2f2fa;
  --identical:#d8e9de; --identical-ink:#2f6b45;
  --one_extra:#fbe3c2; --one_extra-ink:#9a5a12;
  --both_extra:#f6e6a8; --both_extra-ink:#7d6a0c;
  --one_none:#dcd9f4; --one_none-ink:#4e43a6;
  --no_shared:#f5c9c9; --no_shared-ink:#9c3434; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
button { font:inherit; cursor:pointer; }
.muted { color:var(--muted); }
.cat-identical { background:var(--identical); } .cat-one_extra { background:var(--one_extra); }
.cat-both_extra { background:var(--both_extra); } .cat-one_none { background:var(--one_none); }
.cat-no_shared { background:var(--no_shared); }
"""

LOGIN = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resolve Disagreements</title><style>""" + STYLE + r"""
main { max-width:420px; margin:12vh auto 0; padding:0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:26px 26px 22px; }
h1 { font-size:1.25rem; margin:0 0 4px; }
p.sub { margin:0 0 18px; color:var(--muted); font-size:0.92rem; }
label { display:block; font-size:0.78rem; letter-spacing:0.05em; text-transform:uppercase;
  color:var(--muted); margin:12px 0 4px; }
input { width:100%; padding:9px 11px; font:inherit; border:1px solid #d3d3e2; border-radius:5px; }
input:focus { outline:2px solid #8a8ac0; outline-offset:0; border-color:#8a8ac0; }
.plus { text-align:center; color:var(--muted); margin:8px 0 -4px; font-size:1.1rem; }
button { width:100%; margin-top:20px; padding:10px; border:none; border-radius:5px;
  background:var(--accent); color:#fff; font-weight:600; }
.err { background:#fbeaea; color:#6d2020; border-radius:5px; padding:9px 11px; font-size:0.9rem; margin-bottom:6px; }
.strip { display:flex; height:6px; border-radius:3px; overflow:hidden; margin:0 0 18px; }
.strip i { flex:1; }
</style></head><body><main><div class="card">
<div class="strip"><i class="cat-identical"></i><i class="cat-one_extra"></i>
<i class="cat-both_extra"></i><i class="cat-one_none"></i><i class="cat-no_shared"></i></div>
<h1>Resolve disagreements</h1>
<p class="sub">Sign in as the two annotators resolving together. You will see the
arguments you both annotated, and where you parted company.</p>
__ERROR__
<form method="post" action="/login">
  <label for="first">First NetID</label>
  <input id="first" name="first" value="__FIRST__" autocomplete="off" autocapitalize="none" required autofocus>
  <div class="plus">+</div>
  <label for="second">Second NetID</label>
  <input id="second" name="second" value="__SECOND__" autocomplete="off" autocapitalize="none" required>
  <button type="submit">Start reviewing</button>
</form></div></main></body></html>"""


def login_page(error: str = "", typed=("", "")) -> str:
    from html import escape
    return (LOGIN
            .replace("__ERROR__", f'<div class="err">{escape(error)}</div>' if error else "")
            .replace("__FIRST__", escape(typed[0].strip()))
            .replace("__SECOND__", escape(typed[1].strip())))


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resolve Disagreements</title>
<link rel="stylesheet" href="/study.css">
<style>""" + STYLE + r"""
header.top { position:sticky; top:0; z-index:5; background:#fff; border-bottom:1px solid var(--line);
  display:flex; align-items:center; gap:14px; padding:10px 22px; flex-wrap:wrap; }
header.top h1 { font-size:1rem; margin:0; }
.pair { font-weight:600; color:var(--accent); }
.count { color:var(--muted); font-size:0.9rem; }
.bar { width:120px; height:7px; background:#ececf4; border-radius:4px; overflow:hidden; display:inline-block;
  vertical-align:middle; margin-left:6px; }
.bar i { display:block; height:100%; background:#5b9a6f; }
.spacer { flex:1; }
.btn { border:1px solid #c3c3dc; background:var(--accent-bg); color:var(--accent); border-radius:5px;
  padding:7px 13px; font-weight:600; font-size:0.9rem; }
.btn:hover { background:#e7e7f6; }
.btn.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
.btn.primary:hover { background:#28285a; }
.btn.quiet { background:none; border-color:transparent; color:var(--muted); font-weight:500; }
.btn.small { font-size:0.78rem; padding:3px 9px; }
main { max-width:1000px; margin:0 auto; padding:18px 16px 80px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px 18px; margin-bottom:16px; }
h2 { font-size:0.78rem; letter-spacing:0.07em; text-transform:uppercase; color:var(--muted); margin:0 0 10px; }

/* the map: one row per task, only what was assigned */
.task { display:flex; gap:12px; align-items:flex-start; padding:6px 0; }
.task + .task { border-top:1px solid #f0f0f6; }
.task .lab { flex:0 0 180px; font-size:0.82rem; color:#4a4a68; line-height:1.3; }
.task .lab b { display:block; color:var(--ink); }
.task .blocks { display:flex; flex-wrap:wrap; gap:4px; }
.blocks button { min-width:34px; height:28px; border:1px solid rgba(0,0,0,0.08); border-radius:4px;
  font-size:0.74rem; color:#2a2a3a; padding:0 5px; position:relative; }
.blocks button.done { opacity:0.55; }
.blocks button.done::after { content:"✓"; position:absolute; top:-7px; right:-5px; font-size:0.7rem;
  background:#2f6b45; color:#fff; border-radius:50%; width:14px; height:14px; line-height:14px; text-align:center; }
.blocks button.on { outline:2px solid var(--accent); outline-offset:1px; opacity:1; }
.blocks button.split { box-shadow:inset 0 -3px 0 #9c3434; }
.blocks button.changed::after { content:"!"; position:absolute; top:-7px; right:-5px; font-size:0.7rem; font-weight:700;
  background:#9a5a12; color:#fff; border-radius:50%; width:14px; height:14px; line-height:14px; text-align:center; }
@media (max-width: 640px) { .task { flex-direction:column; gap:4px; } .task .lab { flex:none; } }

/* the header */
.head { border-radius:8px; padding:14px 18px; margin-bottom:16px; border-left:6px solid; }
.head .kind { font-weight:700; font-size:1.05rem; }
.head .where { font-size:0.85rem; opacity:0.8; margin-left:8px; }
.head p { margin:6px 0 0; font-size:1rem; }
.head .vline { margin-top:8px; padding-top:8px; border-top:1px solid rgba(0,0,0,0.1); font-weight:600; }
.head .resolved.changed { display:block; background:#fff3d6; color:#6b3f06; }
.head .resolved { margin-top:10px; font-size:0.88rem; background:rgba(255,255,255,0.65); border-radius:5px;
  padding:6px 10px; display:inline-block; }

/* the argument, collapsible */
details.argument { padding:0; }
details.argument > summary { list-style:none; cursor:pointer; padding:13px 18px; display:flex; align-items:center; gap:8px; }
details.argument > summary::-webkit-details-marker { display:none; }
details.argument > summary::before { content:"▸"; color:var(--muted); transition:transform .15s; }
details.argument[open] > summary::before { transform:rotate(90deg); }
details.argument > summary h2 { margin:0; }
details.argument .claim-peek { color:var(--muted); font-size:0.88rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
details.argument[open] .claim-peek { display:none; }
details.argument .inner { padding:0 18px 16px; }

/* the two of them */
.halves { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }
@media (max-width: 700px) { .halves { grid-template-columns:1fr; } }
.side { margin:0; }
.side .who { font-weight:700; font-size:1rem; }
.pill { display:inline-block; font-size:0.75rem; font-weight:600; border-radius:10px; padding:1px 8px; margin-left:6px; vertical-align:2px; }
.pill-pass { background:#e8f1ea; color:#23503a; } .pill-fail { background:#fbeaea; color:#6d2020; }
.pill-draft { background:#fdf0e0; color:#7a4508; } .pill-flag { background:#fbeaea; color:#6d2020; }
.side .m { border:1px solid #ebebf3; border-radius:6px; padding:9px 11px; margin-top:10px; }
.side .m.only { border:2px solid #e0a24a; background:#fffaf2; }
.side .m .mn { font-weight:600; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.side .m .mn .btn { margin-left:auto; }
.side .m p { margin:5px 0 0; font-size:0.92rem; color:#33334a; }
.side .nomech { margin-top:10px; color:var(--muted); }
.side .note { margin-top:10px; font-size:0.88rem; color:#4a4a68; }
.tag { font-size:0.72rem; border-radius:10px; padding:1px 8px; font-weight:600; }
.tag.both { background:#e8f1ea; color:#23503a; }
.tag.only { background:#fdebd0; color:#8a4c06; }
.tag.nobody { color:var(--muted); font-weight:500; }

/* the answer */
.answer { max-width:760px; margin:0 auto; border-top:4px solid var(--accent); }
.answer h3 { margin:0 0 14px; font-size:1.05rem; }
.verdict { display:flex; gap:10px; }
.verdict label { flex:1; border:1px solid #dcdce8; border-radius:6px; padding:9px 12px; cursor:pointer; }
.verdict label:has(input:checked) { border-color:var(--accent); background:var(--accent-bg); }
.verdict .tags { display:block; font-size:0.78rem; color:var(--muted); margin-top:2px; }
.mech { border:1px solid #e6e6ef; border-radius:6px; margin-bottom:8px; background:#fff; }
.mech.extra { border:2px solid #e0a24a; background:#fffaf2; }
.mech.extra.on { background:#fff; }
.mech.none { border-style:dashed; }
.mech .top { display:flex; align-items:center; gap:10px; padding:9px 12px; cursor:pointer; flex-wrap:wrap; }
.mech .top input { width:17px; height:17px; }
.mech .name { font-weight:600; }
.mech.none .name { font-weight:500; color:#4a4a68; }
.mech .why { padding:0 12px 12px 39px; display:none; }
.mech.on .why { display:block; }
textarea { width:100%; font:inherit; font-size:0.92rem; border:1px solid #d3d3e2; border-radius:5px; padding:7px 9px;
  resize:vertical; min-height:58px; }
textarea.flash { animation:flash 0.9s; }
@keyframes flash { from { background:#fff3d6; } to { background:#fff; } }
.disabled-note { font-size:0.88rem; color:var(--muted); margin:0 0 8px; }
.mechs.off .mech { opacity:0.45; pointer-events:none; }
.problems { background:#fbeaea; color:#6d2020; border-radius:6px; padding:9px 12px; margin:10px 0; font-size:0.9rem; }
.problems ul { margin:0; padding-left:18px; }
.savebar { display:flex; gap:10px; align-items:center; justify-content:flex-end; margin-top:14px; }
.empty { text-align:center; padding:40px 18px; color:#5a5a72; }
.empty .big { font-size:1.15rem; font-weight:600; color:#33334d; margin-bottom:6px; }
</style></head><body>
<header class="top">
  <h1>Resolve disagreements</h1>
  <span class="pair" id="pair"></span>
  <span class="count" id="count"></span>
  <span class="spacer"></span>
  <button class="btn primary" id="next">Next unresolved &rarr;</button>
  <form method="post" action="/logout" style="margin:0"><button class="btn quiet">Sign out</button></form>
</header>
<main>
  <div class="card" id="map-card"><div id="map"></div></div>
  <div id="body"><p class="muted">Loading&hellip;</p></div>
</main>
<script>
const NAMES = {identical:'green', one_extra:'orange', both_extra:'yellow', one_none:'purple', no_shared:'red'};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
// The header sentences mark names with **double stars**; everything else is text.
const bold = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
const api = (p, opts) => fetch(p, opts).then(async r => {
  if (r.status === 401) { dirty = false; location.reload(); throw new Error('signed out'); }
  let data;
  try { data = await r.json(); } catch (e) { data = {error: `The server answered ${r.status}. Try again, or tell the study owner.`}; }
  return {ok: r.ok, data};
});

// Typing into the answer and then leaving it -- a block on the map, Next, a
// reload -- would lose what was typed. Ask first.
let dirty = false;
const leaveOk = () => !dirty || confirm('Your answer for this argument is not saved. Leave it anyway?');
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
// Whether the argument is open or folded is remembered for this browser only.
const store = {
  get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} },
};

let Q = null, A = null, current = null;
const every = () => Q.tasks.flatMap(t => t.arguments);

async function loadQueue() {
  Q = (await api('/api/queue')).data;
  document.getElementById('pair').textContent = Q.pair.join(' + ');
  const pct = Q.to_resolve ? Math.round(100 * Q.resolved / Q.to_resolve) : 0;
  document.getElementById('count').innerHTML = Q.to_resolve
    ? `${Q.resolved} of ${Q.to_resolve} resolved <span class="bar"><i style="width:${pct}%"></i></span>` : '';
  drawMap();
}

function drawMap() {
  const box = document.getElementById('map');
  if (!Q.tasks.length) { box.innerHTML = ''; document.getElementById('map-card').hidden = true; return; }
  box.innerHTML = Q.tasks.map(t => {
    const done = t.arguments.filter(a => a.resolved).length;
    return `<div class="task"><div class="lab"><b>${esc(t.label || `Arguments ${t.from}–${t.to}`)}</b>
        ${t.label ? `${t.from}–${t.to} · ` : ''}${t.colours.map(c => NAMES[c]).join(', ')}<br>${done} of ${t.arguments.length} done</div>
      <div class="blocks">${t.arguments.length ? t.arguments.map(a => `<button class="cat-${a.category}${
        a.resolved ? ' done' : ''}${a.changed.length ? ' changed' : ''}${a.hidden ? ' split' : ''}${
        a.instance_id === current ? ' on' : ''}" data-id="${esc(a.instance_id)}" title="Argument ${a.number}${
        a.resolved ? ' — resolved' : a.changed.length ? ' — changed since resolved' : ''}">${a.number}</button>`).join('')
        : '<span class="muted" style="font-size:0.85rem">Nothing in this stretch disagrees in those colours.</span>'}</div></div>`;
  }).join('');
  box.querySelectorAll('button').forEach(b => b.onclick = () => leaveOk() && open(b.dataset.id));
}

function nextUnresolved(after) {
  const list = every();
  const start = Math.max(0, list.findIndex(a => a.instance_id === after) + 1);
  for (let k = 0; k < list.length; k++) {
    const a = list[(start + k) % list.length];
    if (!a.resolved && a.instance_id !== after) return a.instance_id;
  }
  return null;
}

document.getElementById('next').onclick = () => {
  if (!leaveOk()) return;
  const id = nextUnresolved(current);
  if (id) open(id); else allDone();
};

function allDone() {
  current = null; dirty = false; drawMap();
  history.replaceState(null, '', location.pathname);
  document.getElementById('next').hidden = !Q.to_resolve;
  document.getElementById('body').innerHTML = Q.tasks.length && Q.to_resolve
    ? `<div class="card empty"><div class="big">Everything assigned to you two is resolved.</div>
       <p>Click any block above to look at one again or change it.</p></div>`
    : `<div class="card empty"><div class="big">Nothing has been assigned to you two yet.</div>
       <p>When arguments are handed to ${esc(Q.pair.join(' and '))} to resolve, they will appear here.</p></div>`;
}

async function open(id) {
  const r = await api('/api/argument/' + encodeURIComponent(id));
  if (!r.ok) { document.getElementById('body').innerHTML =
    `<div class="card empty"><div class="big">${esc(r.data.error)}</div></div>`; return; }
  A = r.data; current = id; dirty = false;
  history.replaceState(null, '', '#' + encodeURIComponent(id));
  drawMap();
  draw();
  window.scrollTo({top: 0});
}

const pill = v => `<span class="pill pill-${v ? v.toLowerCase() : 'draft'}">${esc(v ?? 'unfinished')}</span>`;

function draw() {
  const h = A.header, d = A.decision;
  const people = A.annotations;
  const has = (p, m) => (p.mechanisms || []).includes(m);
  const byWho = m => people.filter(p => has(p, m)).map(p => p.annotator);

  // Where the answer starts: the saved decision if there is one, otherwise what
  // everybody agreed on -- the shared mechanisms, and the verdict if it was
  // unanimous. An extra is never pre-ticked.
  const verdicts = new Set(people.map(p => p.sensible_test));
  const startVerdict = d ? d.sensible_test
    : (verdicts.size === 1 && people[0].sensible_test) ? people[0].sensible_test : null;
  const startMechs = new Set(d ? d.mechanisms : h.common);
  if (!d && startVerdict === 'Pass') startMechs.clear();

  const changed = d && d.changed.length ? `<div class="resolved changed">⚠ <b>${esc(d.changed.join(' and '))}</b>
    changed ${d.changed.length === 1 ? 'their answer' : 'their answers'} after this was resolved. What they say now is
    shown below. Look again, and save to settle it.</div>` : '';
  const resolved = d ? `<div class="resolved">✓ Resolved by <b>${esc(d.resolved_by.join(' + '))}</b>,
    ${esc(new Date(d.resolved_at).toLocaleString())}${d.n_earlier ? ` · changed ${d.n_earlier} time${
    d.n_earlier === 1 ? '' : 's'} before` : ''}. Saving again keeps this version in the history.</div>` : '';

  // One half per person: their verdict, then each mechanism they ticked with
  // the justification they wrote for it. A mechanism only they ticked is
  // outlined, the same way the answer below outlines it.
  const side = p => {
    const mechs = A.mechanisms.filter(m => has(p, m));
    return `<div class="card side">
      <div class="who">${esc(p.annotator)}${pill(p.sensible_test)}${p.flag ? '<span class="pill pill-flag">flagged</span>' : ''}</div>
      ${mechs.length ? mechs.map(m => {
        const only = byWho(m).length < people.length;
        const text = p.justifications && p.justifications[m];
        return `<div class="m${only ? ' only' : ''}"><div class="mn">${esc(m)}
          <span class="tag ${only ? 'only' : 'both'}">${only ? 'only ' + esc(p.annotator) : (people.length === 2 ? 'both' : 'everyone')}</span>
          ${text ? `<button type="button" class="btn small" data-use="${esc(m)}" data-text="${esc(text)}">Use this</button>` : ''}</div>
          <p>${text ? esc(text) : '<span class="muted">no justification written</span>'}</p></div>`;
      }).join('') : '<p class="nomech">No mechanism.</p>'}
      ${p.notes ? `<p class="note"><b>Note:</b> ${esc(p.notes)}</p>` : ''}
    </div>`;
  };

  const verdictTags = v => {
    const who = people.filter(p => p.sensible_test === v).map(p => p.annotator);
    return who.length ? 'said by ' + who.map(esc).join(', ') : 'nobody';
  };

  const mechRow = m => {
    const by = byWho(m);
    const kind = by.length === 0 ? 'none' : (by.length === people.length ? 'both' : 'extra');
    const tag = kind === 'both' ? `<span class="tag both">${people.length === 2 ? 'both' : 'everyone'}</span>`
      : kind === 'extra' ? `<span class="tag only">only ${by.map(esc).join(', ')}</span>`
      : '<span class="tag nobody">nobody picked this</span>';
    const written = people.map(p => p.justifications && p.justifications[m]).filter(Boolean);
    const start = d && d.justifications[m] ? d.justifications[m] : (written.length === 1 ? written[0] : '');
    return `<div class="mech ${kind}${startMechs.has(m) ? ' on' : ''}" data-m="${esc(m)}">
      <label class="top"><input type="checkbox" ${startMechs.has(m) ? 'checked' : ''}>
        <span class="name">${esc(m)}</span>${tag}</label>
      <div class="why"><textarea data-just="${esc(m)}" placeholder="The justification that goes into gold. Press “Use this” above, or write it here.">${esc(start)}</textarea></div>
    </div>`;
  };

  const peek = A.claim;
  const folded = store.get('argument-folded') === '1';

  document.getElementById('body').innerHTML = `
    <div class="head cat-${h.category}" style="border-color:var(--${h.category}-ink)">
      <span class="kind" style="color:var(--${h.category}-ink)">${esc(h.title)}</span>
      <span class="where">Argument ${A.number ?? '—'}</span>
      ${h.lines.map(l => `<p>${bold(l)}</p>`).join('')}
      ${h.verdict_line ? `<p class="vline">${bold(h.verdict_line)}</p>` : ''}
      ${h.rule_lines.length ? `<div class="vline"><p style="margin:0">Against the guidelines:</p>${
        h.rule_lines.map(l => `<p style="margin:2px 0 0;font-weight:500">${bold(l)}</p>`).join('')}</div>` : ''}
      ${changed}
      ${resolved}
    </div>
    <details class="card argument" id="argument" ${folded ? '' : 'open'}>
      <summary><h2>The argument</h2><span class="claim-peek">${esc(peek)}</span></summary>
      <div class="inner">${A.argument_html}</div>
    </details>
    <div class="halves">${people.map(side).join('')}</div>
    <div class="card answer" id="form">
      <h3>Your answer</h3>
      <h2>Sensible Test</h2>
      <div class="verdict">
        ${['Fail', 'Pass'].map(v => `<label><input type="radio" name="verdict" value="${v}" ${
          startVerdict === v ? 'checked' : ''}> <b>${v}</b><span class="tags">${verdictTags(v)}</span></label>`).join('')}
      </div>
      <h2 style="margin-top:18px">Mechanisms</h2>
      <p class="disabled-note" id="pass-note" hidden>A Pass has no mechanisms.</p>
      <div class="mechs" id="mechs">${A.mechanisms.map(mechRow).join('')}</div>
      <h2 style="margin-top:18px">Resolution note</h2>
      <textarea id="note" placeholder="Optional: why you settled it this way. Useful when the guidelines need changing.">${esc(d ? d.note : '')}</textarea>
      <div id="problems"></div>
      <div class="savebar">
        <button class="btn primary" id="save">${d ? 'Save changes' : 'Save'} &amp; next &rarr;</button>
      </div>
    </div>`;
  wire();
}

function verdict() {
  const v = document.querySelector('input[name=verdict]:checked');
  return v ? v.value : null;
}

function syncPass() {
  const pass = verdict() === 'Pass';
  document.getElementById('mechs').classList.toggle('off', pass);
  document.getElementById('pass-note').hidden = !pass;
}

function wire() {
  const form = document.getElementById('form');
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; });
  document.getElementById('argument').addEventListener('toggle', e =>
    store.set('argument-folded', e.target.open ? '0' : '1'));
  document.querySelectorAll('.mech').forEach(row => {
    const box = row.querySelector('input');
    box.onchange = () => row.classList.toggle('on', box.checked);
  });
  // "Use this" on either half ticks that mechanism in the answer and puts the
  // justification in its box, where it can still be edited.
  document.querySelectorAll('[data-use]').forEach(b => b.onclick = () => {
    const row = document.querySelector(`.mech[data-m="${CSS.escape(b.dataset.use)}"]`);
    row.querySelector('input').checked = true;
    row.classList.add('on');
    const box = row.querySelector('textarea');
    box.value = b.dataset.text;
    box.classList.remove('flash'); void box.offsetWidth; box.classList.add('flash');
    dirty = true;
  });
  document.querySelectorAll('input[name=verdict]').forEach(r => r.onchange = syncPass);
  syncPass();
  document.getElementById('save').onclick = save;
}

async function save() {
  const v = verdict();
  const mechanisms = v === 'Pass' ? [] :
    [...document.querySelectorAll('.mech')].filter(r => r.querySelector('input').checked).map(r => r.dataset.m);
  const justifications = {};
  mechanisms.forEach(m => {
    justifications[m] = document.querySelector(`textarea[data-just="${CSS.escape(m)}"]`).value;
  });
  const button = document.getElementById('save');
  button.disabled = true;
  const r = await api('/api/argument/' + encodeURIComponent(current), {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sensible_test: v, mechanisms, justifications,
                          note: document.getElementById('note').value})});
  button.disabled = false;
  if (!r.ok) {
    document.getElementById('problems').innerHTML = `<div class="problems"><ul>${
      (r.data.problems || [r.data.error]).map(p => `<li>${esc(p)}</li>`).join('')}</ul></div>`;
    return;
  }
  const done = current;
  dirty = false;
  await loadQueue();
  const id = nextUnresolved(done);
  if (id) open(id); else allDone();
}

(async () => {
  await loadQueue();
  const fromHash = decodeURIComponent(location.hash.slice(1));
  const start = (fromHash && every().some(a => a.instance_id === fromHash)) ? fromHash : nextUnresolved(null);
  if (start) open(start); else allDone();
})();
</script></body></html>"""


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8767)
    args = ap.parse_args()

    exposed = not is_loopback(args.host)
    app = create_app(exposed)
    print(f"review site   http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
