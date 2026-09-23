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
| `agreement.py` | Agreement coefficients and confusion tables |
| `annotation_backend.py` | Admin console. `--port`, `--host` |
| `mirror_to_postgres.py` | One mirroring pass. `--verify`, `--dry-run` |
| `mirror_loop.py` | Runs the mirror every 15 minutes |
| `start.sh` | Container entry point; starts all three processes |
| `pull_annotations.sh` | Copies deployed annotations to a local working copy |
| `walk_study.py` | End-to-end test of every branch |
| `capture_ui.py` | Screenshots of each interface state |
| `verify_deploy.py` | Volume and session check against a deployment |
| `resync_order.py` | Re-aligns annotators after a corpus rebuild |

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

Agreement is on the console's Agreement tab, and in `agreement.json` from its
Download tab. Potato's `/admin/iaa` returns null for this study and is expected
to; the console computes over the overlap actually on disk.

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

## Notes

- **One replica.** Potato keeps annotator state in process memory; a second
  replica diverges and the two overwrite each other on the volume.
- **The volume must mount at `annotation_output/`.** The export, the console
  and the mirror all resolve that path.
- **No `layout:` block in `config.yaml`.** It disables `display_logic` and puts
  every conditional question permanently on screen, silently.
- **Never rename a scheme after annotation starts.** Old answers become
  invisible to the admin view, the agreement report and the export, silently.
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
