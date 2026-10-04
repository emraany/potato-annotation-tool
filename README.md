# VLMD Subtask 1 — Cognitive Mechanism Annotation

Annotation study for cognitive mechanism classification over 1,621 meme
arguments. Built on [Potato](https://potatoannotator.com/) 2.9.3, deployed on
Railway with a Postgres mirror.

## Requirements

- Python 3.9
- `pip install -r requirements.txt`
- Playwright browsers, for the test scripts only: `playwright install chromium`

## Quick start

```bash
source .venv/bin/activate
python scripts/build_instances.py                     # args_0911.json -> data/
nohup python scripts/serve.py > server.log 2>&1 &     # http://localhost:8765
python scripts/annotation_backend.py                  # http://127.0.0.1:8766
python scripts/review_site.py                         # http://127.0.0.1:8767
```

Boot takes 10–20 seconds. Use `scripts/serve.py`, not `potato start
config.yaml`: the guidelines banner, the draft mechanism and NetID sign-in are
applied in that script.

Annotators sign in with a NetID. No password, no registration.

## Project structure

```
config.yaml              Task definition: schemes, display logic, sign-in, keys
guidelines.md            Annotator guidelines, rendered into the interface
args_0911.json           Source corpus
data/                    Built corpus: instances.json, manifest.json
static/                  Stylesheet and client script (inlined at serve time)
tests/                   Agreement tests and the Week 2 fixtures they check against
layouts/                 Generated form cache; regenerated on a config change
annotation_output/       Annotation store, one directory per annotator
  assignments.json       Who was given which stretch of the corpus
scripts/                 See below
```

## Scripts

| Script | Purpose |
|---|---|
| `build_instances.py` | Builds the corpus and manifest. `--check` diffs without writing |
| `serve.py` | Study server. `--port` defaults to 8765 |
| `accounts.py` | NetID validation; closes the unused password endpoints |
| `assignments.py` | The assignment overlay: reads and writes `assignments.json` |
| `export_annotations.py` | CSV, JSONL and validation report. `--strict`, `--self-test` |
| `migrate_flatten_mechanisms.py` | One-off: folds pre-flatten primary/secondary answers into `mechanisms`. `--dry-run`, `--self-test` |
| `import_annotations.py` | Loads annotations done outside the study into `annotation_output/`. `--dry-run`, `--overwrite`, `--no-infer` |
| `agreement.py` | The agreement report: pure functions, one pair and one argument range |
| `annotation_backend.py` | Admin console. `--port`, `--host` |
| `review.py` | Resolving disagreements: colours, headers, the decisions file, gold |
| `review_site.py` | The review site: a pair signs in with both NetIDs and resolves their disagreements. `--port` defaults to 8767 |
| `mirror_to_postgres.py` | One mirroring pass. `--verify`, `--dry-run` |
| `mirror_loop.py` | Runs the mirror every 15 minutes |
| `start.sh` | Container entry point; starts all three processes |
| `pull_annotations.sh` | Copies deployed annotations to a local working copy |
| `walk_study.py` | End-to-end test of every branch |
| `capture_ui.py` | Screenshots of each interface state |
| `verify_deploy.py` | Volume and session check against a deployment |
| `resync_order.py` | Re-aligns annotators after a corpus rebuild |

## Resolving disagreements

The console's **Review** tab hands a pair of annotators a task: a stretch of
argument numbers and which colours from the Agreement map to resolve in it.
The pair sign in to the **review site** together, with both NetIDs, and see
their tasks and nothing else. Each argument opens with a sentence saying how
they differ, the argument (collapsible), the two of them side by side with
their mechanisms and justifications, and under that the answer they settle on
-- the mechanisms they both ticked already ticked, every extra outlined and
labelled with who added it.

Decisions go to `annotation_output/review/decisions.json` and tasks to
`annotation_output/review/assignments.json`; nobody's annotations are changed.
One decision per argument, whichever pair gets there first; reopening keeps the
earlier version in its history.

The Review tab also shows each pair's progress, what they settled on task by
task, and downloads **gold**: one row per argument, `agreed` or `resolved`.
An argument only counts as agreed if every answer also keeps the guidelines'
rules; agreeing on, say, a Fail with no mechanism goes to review instead. If an
annotator changes their answer after a pair resolved it, the decision stays in
gold marked `changed_since_resolved` and goes back on the pair's map.
Test accounts (`demo1`–`demo3`, or `REVIEW_EXCLUDE`) are left out throughout.

On Railway the review site runs in the same container as the study (`start.sh`,
port 8767, its own domain). The Postgres mirror copies review data into
`review_decision`, `review_decision_history` and `review_task`, and rebuilds
`gold` and `gold_justification` on every pass.

## Handing out work

The console's **Assign** tab gives somebody a stretch of argument numbers. It
writes `annotation_output/assignments.json` and nothing else — the study server
re-reads that file on its next page render, so no restart is involved.

An assignment changes what an annotator is *shown*, never what they can reach:
a band under their navbar with their stretch, how much of it is finished, how
much is left and a button to the next argument in it they have not done; the
navbar arrows stop at the ends of the stretch; and they land on the first
unfinished argument of it rather than on argument 1. `Go to` still reaches all
1,621 and an argument outside the stretch still saves — give the same stretch
to two people and that is your agreement set.

An annotator with nothing assigned gets the study exactly as it ran before:
whole corpus, no band. Deleting the file puts everybody back there.

## Getting the data out

```bash
python scripts/export_annotations.py --strict
```

Writes `annotations.csv`, `annotations.jsonl` and `validation_report.json` to
`annotation_output/export/`, exiting non-zero on any constraint violation. Run
this before handing anything downstream. Potato's own auto-export in
`annotation_output/exports/` is a safety net, not the deliverable.

Agreement is on the console's **Agreement** tab: one pair of annotators over
one stretch of argument numbers, with Cohen's κ on the Sensible Test, the five
ways two mechanism sets can differ, and findings in sentences. Both selections
are in the URL, so the view is a link you can send to the two people it is
about — and `agreement.json` from the Download tab takes the same query string,
so the file and the screen are one computation.

Potato's `/admin/iaa` returns null for this study and is expected to; the
console works off the overlap actually on disk.

## Tests

```bash
python -m unittest discover tests
```

`tests/test_agreement.py` checks the agreement report against the two Week 2
pairs in `tests/fixtures/` — every count, every coefficient, every
per-mechanism number and the findings in order — plus the edges the fixtures
do not have. `export_annotations.py --self-test` and
`migrate_flatten_mechanisms.py --self-test` cover the export's constraints and
the flatten migration; `walk_study.py` drives the real interface end to end
against a running server.

## Deployment

One Railway service running three processes, plus Postgres.

| | |
|---|---|
| Study | `study-production-90a4.up.railway.app` |
| Console | `study-production-8fd9.up.railway.app` (port 8766, needs `?key=`) |
| Volume | `annotation_output/`, 5 GB |
| Replicas | **1, always** |

```bash
railway up --service study            # deploy
railway logs --service study          # logs
railway ssh --service study "<cmd>"   # shell in the container
```

Environment variables: `POTATO_SECRET_KEY`, `POTATO_ADMIN_API_KEY`,
`DATABASE_URL`. Full procedure in `DEPLOY.md`.

## Storage

Annotations live in `annotation_output/<netid>/user_state.json`, one file per
annotator, rewritten atomically on every save. These files are the source of
truth; the export, the agreement report and the console all read them directly.
In the deployment they sit on a Railway volume, which is what makes them survive
a redeploy.

`mirror_to_postgres.py` copies them into Postgres every 15 minutes as a
queryable, separately backed copy. It is outside the save path, so a broken
mirror cannot affect annotation. Three tables: `annotation` (current state),
`annotation_history` (append-only, every version ever seen) and `mirror_run`
(one row per pass, so a silently dead mirror is visible).

The mirror holds annotation records, not annotator state files — the assigned
ordering and each annotator's position are not in Postgres. It recovers the
data, not a running study. Volume backups cover the rest.

The mirrored row is whatever `export_annotations.collect` returns, as `jsonb`,
so a change to the export schema changes the mirror with it. Flattening the
mechanisms was one: every row's content hash changed, so the first pass after
it rewrites every row and appends one history row each. That is the flatten
being recorded, not a fault.

## Notes

- **One replica.** Potato keeps annotator state in process memory; a second
  replica diverges and the two overwrite each other on the volume.
- **The volume must mount at `annotation_output/`.** The export, the console
  and the mirror all resolve that path.
- **No `layout:` block in `config.yaml`.** It disables `display_logic` and puts
  every conditional question permanently on screen, silently.
- **Never rename a scheme after annotation starts.** Old answers become
  invisible to the admin view, the agreement report and the export, silently.
  Flattening the mechanisms renamed seven schemes and removed two, which is why
  `migrate_flatten_mechanisms.py` exists: run it once, with the study server
  stopped, against any store that holds pre-flatten answers.
- **A rebuild does not reorder anyone already annotating.** Run
  `resync_order.py` with the server stopped. Argument numbers that moved take
  their assignments' meaning with them — `assignments.json` holds numbers, not
  ids, so re-read the Assign tab after a rebuild.
- **The NetID is the whole credential.** Treat the study URL accordingly.
- Delete the throwaway annotator that `walk_study.py` and `capture_ui.py`
  leave behind, or its answers count toward agreement.

## Documentation

| File | Contents |
|---|---|
| `DESIGN.md` | Task design and the reasoning behind each decision |
| `RUNNING.md` | Day-to-day operation, the console, accounts, data extraction |
| `DEPLOY.md` | Deployment procedure, storage decisions, pre-launch checks |
| `guidelines.md` | Annotator guidelines and mechanism definitions |
