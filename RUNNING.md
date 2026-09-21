# VLMD Subtask 1 — Cognitive Mechanism Classification

Running at http://localhost:8765

## Starting and stopping it

```bash
cd /Users/emraan/Desktop/potato-annotation-tool
source .venv/bin/activate
nohup python scripts/serve.py > server.log 2>&1 &

# stop it
pkill -f scripts/serve.py
```

Boot takes 10-20 seconds. It is ready when `curl -s -o /dev/null -w '%{http_code}' \
http://localhost:8765` prints 200. Startup messages go to `server.log`; two of
them are worth reading, and they are near the top:

```
guidelines   guidelines.md, 113 lines, rendered into the banner on every argument
drafts       on; 11 question(s) required to finish an argument, none required to leave one
```

**Start it with `scripts/serve.py`, not with `potato start config.yaml`.** The
guidelines and the drafts are both set up in that script rather than in
`config.yaml`, so `potato start` boots a study with an empty instructions banner
and no drafts. It also picks port 8765, which matters — something else on this
machine holds 8000 on 127.0.0.1, and Potato binds 0.0.0.0, loses the race, and
answers 404 on every request with nothing in the log to say why.

Ignore the `NotOpenSSLWarning` on every command. macOS system Python links
LibreSSL; it does not affect a local server.

## Telling somebody where to start

The corpus is served meme by meme in the order of `args_0911.json`, and each
meme's arguments are read bottom up — the ones built out of visual elements and
background first, the meme's root claim C0 last. Every annotator is handed all
1,621 arguments in that one order. So:

* the top of every argument reads **Argument 500 of 1621**;
* the navbar reads **Instance #500**;
* typing **500** into the navbar's **Go to** box goes there;
* `item_number_to_source_index` in `data/manifest.json` says which line of
  `args_0911.json` that is (the two are not the same number — each meme is
  turned bottom up), and `source_index` is a column in the export.

The number on screen is the same for everybody, always. "Start at 500, stop at 800" is the whole
assignment mechanism — nothing in the software divides the work up, and nothing
stops two people doing the same stretch. The backend's **Who is where**
table is how you see where everybody got to.

## Save draft, and Submit

There are two buttons at the bottom of every argument where Potato has one.

**Submit** ends the argument. If anything required is missing it refuses, says
which questions in a message, and outlines each of them with *needs an answer*
— including a justification typed under 25 characters. The right-arrow key does
the same thing.

**Save draft** leaves the argument unfinished and goes to the next one. It never
refuses. What has been answered is kept.

What else they see, and what to tell them:

* the badge at the top of the page reads **In Progress** on an argument they
  have started and not finished, and **Labeled** when it is done;
* **Progress** counts finished arguments only — a draft does not count;
* the two arrow buttons beside the counter step to the argument before and the
  argument after — one at a time, through everything, finished arguments
  included. The badge is what tells them what they landed on. They grey out at
  argument 1 and at argument 1621; there is no wrap-around.

A draft is not finished work. `python scripts/export_annotations.py --strict`
fails on one (`incomplete_sensible_test`), which is the backstop: nothing gets
handed downstream with half-answered arguments in it. The backend's **Who is
where** table shows how many drafts each person has outstanding.

## The annotation tool backend — http://127.0.0.1:8766

It is a **separate server from the study**, and it is not running unless you
start it. It does not come up with `serve.py` and there is no link to it from
the annotation pages — deliberately, since it hands out every annotation in the
study.

```bash
cd /Users/emraan/Desktop/potato-annotation-tool
source .venv/bin/activate
python scripts/annotation_backend.py       # then open http://127.0.0.1:8766
```

Leave that terminal running while you use it; Ctrl-C stops it. `--port 9000` if
8766 is taken. It has four tabs, for the things Potato's own dashboard does not
do, or does not do readably — get the data out, read the annotations
themselves, read the agreement, and read the flags.

It reads `annotation_output/` directly, so it works with the study server
stopped. It changes nothing: no config is rewritten, no assignment is made,
nothing is sent to the study server. It binds loopback only; `--host 0.0.0.0`
works but then every request has to carry the admin key, since the page hands
out every annotation in the study. Open it once as `?key=...` and the page
puts the key on its own links from there — the downloads included, which is
what stops the Download tab's buttons coming back *Forbidden*.

**Download** has the CSV, the JSONL, the validation report, `flags.csv`,
`agreement.json` and a zip of the raw `annotation_output/` — and the **Who is
where** table: per annotator, **Completed**, **Drafts**, **Flags**.

**Completed** is every argument they have finished, written as ranges —
`1–199, 201–500` — with the total beside it. Ranges rather than a first and a
last number, because those two cannot tell you whether the stretch between them
was actually done: somebody given 1 to 500 who skipped two hundred in the
middle reads as "from 1, reached 500" either way. A gap in the ranges is a gap
in the work, and it is what to read before handing the next stretch over.
Drafts are not in it — a draft is not finished. To see *which* arguments are
drafts or flags, filter the Annotations tab by that annotator.

Arguments start collapsed everywhere on this console; click *the argument* to
read one.

**Annotations** is every annotation there is, under the argument it is about.
Where two people annotated the same argument their answers sit side by side and
the argument is marked **agree**, or **differ on** whichever of verdict, primary
mechanism and secondary mechanisms they did not answer the same way. Each block
is one person's three checks, their mechanisms with the justification they typed,
their notes, and when they saved it; the argument itself is under *the argument*.

Filter it by annotator, by verdict (Pass / Fail / draft), by mechanism — primary
or secondary — by argument number range, or by free text over the justifications,
notes and meme ids. **Overlap** narrows it to arguments more than one person is
in, or to just the ones they read differently, which is the adjudication queue
that the agreement report only gives you a number for. A filter picks
*annotations*: an argument is listed when one of its annotations matches and
shows those, so filtering to one person gives one block per argument rather than
a comparison with somebody who was filtered out. **These annotations as CSV** is
`annotations.csv` with whatever is on screen applied — the same columns, so it
goes straight into the same spreadsheet.

**Agreement** is the inter-annotator agreement: Krippendorff's α, Fleiss' and
Cohen's κ, α-MASI and the plain observed agreement, per question and per pair
of annotators, with a confusion table showing which two mechanisms each pair
swapped for each other. This is the page to read rather than `/admin/iaa` —
see below for why that one is all nulls.

Every number is shown beside what it was computed on, because a coefficient on
its own is unreadable: the same 0.61 is a finding on four hundred arguments and
noise on four. Each question is scored only on the arguments where two or more
people answered *that question*, and the line under each row says what that
means for it. A coefficient that is undefined says why — no overlap yet, or one
answer used throughout — instead of printing nothing.

**It is empty until two people annotate the same arguments**, and it says so in
those words. Nothing in the software creates overlap; "start at 500, stop at
800" to two annotators is what creates it. `agreement.json` on the Download tab
is the same report, as it goes into a write-up.

**Flags** is every argument somebody ticked **flag for discussion** on, with
the argument itself. It is a queue, not a workflow: somebody still has to read
it.

**A note is not a flag.** Notes are ordinary annotation content: they are a
column in the export and they sit beside their annotation on the Annotations
tab, where the free-text filter searches them. They do not put an argument in
this queue and they are not in `flags.csv`. Asking for attention is the
checkbox, and only the checkbox — a queue that fills up with people thinking
aloud is a queue nobody reads.

## Potato's own admin pages

`http://localhost:8765/admin` is the dashboard and opens in a browser; it asks
for the admin key, which the server writes into `admin_api_key.txt` on its
first boot, and keeps it for the session. It is the **live view of the study
running** — Overview, Annotators, Instances, Questions, Behavioral, Devices,
Config: who is logged in, how far along, how long each argument is taking, how
the answers are distributed. Use it while people are working; use the backend
on 8766 to read and get out what they produced.

The JSON APIs underneath it need the key as a header, so they are curl'd rather
than opened:

```bash
curl -H "X-API-Key: $(cat admin_api_key.txt)" http://localhost:8765/admin/iaa
```

**`/admin/iaa` will be all nulls, and that is not a fault.** It only scores
items whose per-item annotator cap is 2 or more, and this study sets no cap —
work is handed out by argument number, between people — so it reports nothing
however much two annotators have actually read the same arguments. The
**Agreement** tab on the backend computes the same coefficients, from the same
Potato code, over the overlap that is really on disk. Read that one.

## Where the data is

`annotation_output` -- one directory per annotator, rewritten by the server as
people work. Read it, copy it, back it up; do not edit it in place.

## Accounts

**Registration is open.** Anyone with the URL opens it, picks the **Register**
tab, chooses a username and a password, and is on argument 1 straight away.
There is nothing to do per annotator: no roster to edit, no redeploy, no
invitation. What you send someone is the URL and where to start ("you take 1 to
500") -- see *Telling somebody where to start* above.

The username is a username, not an email address -- the HTML field is named
`email`, which is Potato's, not ours, and the landing page says so. Whatever
they type is the name in the export, in **Who is where** and in the agreement
report, so tell people to use something you will recognise. Passwords are their
own; they are stored as PBKDF2-HMAC-SHA256 with a per-user salt and nobody,
including you, can read them back.

Three things the registration form refuses, all deliberately:

* a username somebody has already registered (*Duplicate user*);
* a username that already owns an `annotation_output/<name>/` directory but has
  no account -- that combination means the roster file and the output directory
  have gone out of step, and registering would hand a stranger somebody else's
  work, so it says to ask the administrator instead;
* an empty username or password.

`annotation_output/user_config.json` holds the logins -- one JSON object per
line -- and is what makes them survive a restart. On Railway it is on the
volume. **Do not delete it once the study is live**: every annotator would be
locked out, and because of the second refusal above they could not simply
register again.

To remove an account, delete its line from `annotation_output/user_config.json`
*and* its `annotation_output/<user>/` directory. Doing only the first leaves the
annotations in the export; doing only the second leaves an account that can
still log in.

**The corpus is writable by anyone who has the link.** That is what open
registration means, and it is the trade for not administering accounts. Nothing
distinguishes a colleague from a stranger except the name they typed, so read
the console's **Who is where** table now and again and look for a name you do
not know; removing one is the two deletions above. To close the study to named
people instead, in `config.yaml`:

```yaml
user_config:
  allow_all_users: false
  users:
    - someone@example.com      # only these names may register
```

and redeploy. Do not set `allow_all_users: false` without a `users:` list and
with an empty `user_config.json` -- that locks out everybody, you included.

## Rebuilding anything

```bash
python scripts/build_instances.py           # args_0911.json -> data/instances.json + manifest
python scripts/build_instances.py --check   # diff ids against the last build, write nothing
```

Rerunnable; writes into `data/`. Do not hand-edit its output. Restart the
server after a rebuild, and **delete `layouts/` first** — Potato caches the
generated form there and only regenerates it on a hash mismatch.

**A rebuild does not reorder anybody already annotating.** Potato hands out the
corpus once, when an account first reaches the annotation phase, and stores that
list in `annotation_output/<user>/user_state.json`; from then on the stored list
is that person's order and `data/instances.json` is not consulted again. So if
the order changes — as it did when each meme became bottom-up, root claim C0
last — existing accounts keep the old one until:

```bash
# with the server STOPPED; it writes its in-memory user states back over these
python scripts/resync_order.py --check      # report, write nothing
python scripts/resync_order.py              # rewrite, after a .bak per user
```

It replaces `instance_id_ordering` with the order of `data/instances.json` and
keeps each annotator on the argument they were on — the same id, at its new
number. Annotations are keyed by instance id, not by position, so nothing moves
off its argument. Assigned ids that have left `data/instances.json` since are
dropped from the ordering and named in the output, their annotations left in
place. It refuses to write while something is listening on 8765.

`static/study.js` needs no build either. `serve.py` inlines it into every page
— it is what shuts the guidelines banner and what makes Save draft and Submit
two buttons. Edit it and restart; there is no cache to clear, but a hard reload
in the browser saves confusion. If it fails to load, the page falls back to
Potato's single `Next` and `walk_study.py` says so.

`guidelines.md` needs no build. `scripts/serve.py` reads it at boot and renders
it into the banner above every argument; edit it and restart. It is the only
place the six mechanism definitions live. Two things to know about editing it:

* **No HTML in it, and no markdown tables.** Potato renders the banner as
  markdown only when the text contains no block-level HTML tag, and its
  markdown renderer has no table support. A `<table>` or a `|---|` in there
  prints the whole document as source text on every page.
* The boot warns if the six mechanism names stop matching the six labels in
  `config.yaml`, or if one of the three Sensible Test questions has drifted
  from the wording on screen. Warnings, not failures — read `server.log`.

## Getting the data out

Potato's own `csv`/`jsonl` auto-export lands in `annotation_output/exports/` and
is a safety net, not the deliverable: one sparse column per scheme-and-label, no
item fields, no timestamps. The real export is:

```bash
python scripts/export_annotations.py --strict
```

which writes `annotation_output/export/annotations.csv`, `annotations.jsonl` and
`validation_report.json`, and exits non-zero if any constraint was violated.
Run it before handing anything to anyone. `--self-test` runs the validator
against synthetic violations if you want to confirm it still fires.

## Checking the study is still sound

```bash
python scripts/walk_study.py --url http://localhost:8765     # end-to-end, all branches, drafts
python scripts/capture_ui.py --url http://localhost:8765     # full-page screenshots into ui/
```

Both register a throwaway annotator. Delete it afterwards — the entry in
`user_config.json` **and** its `annotation_output/<user>/` directory — or its
answers count toward agreement.

## Two things not to change

- **Do not add a `layout:` block to config.yaml.** It breaks `display_logic`
  and puts every conditional question permanently on screen, silently. The long
  comment above `annotation_schemes` has the measurements.
- **Do not rename a scheme.** Renaming one after annotation starts makes the old
  answers invisible to the admin view, the agreement report and the export, with
  no error anywhere.

## Before you publish anything

Stop the server, take a copy of `annotation_output`, and keep it somewhere the
server cannot rewrite.

`DESIGN.md` has the reasoning; `QUESTIONS.md` has what is still open.

_Rewritten 2026-09-20._
