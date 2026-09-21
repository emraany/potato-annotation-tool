# VLMD Subtask 1 — Cognitive Mechanism Classification

An annotation study in which trained annotators label the cognitive mechanisms
used to move from a set of premises to a claim in argument structures extracted
from internet memes. This repository holds the task configuration, the
annotation interface, the corpus build, the export and validation pipeline, the
inter-annotator agreement report, and the administrative console.

The study is built on [Potato](https://potatoannotator.com/) 2.9.3 and runs on
Python 3.9.

## The annotation task

The unit of annotation is a single **argument**: one claim together with its
immediate children. The corpus contains 1,621 such arguments drawn from 614
memes. Arguments are presented one at a time, in a fixed order that is the same
for every annotator, and each meme's arguments are ordered bottom-up so that the
supporting structure is read before the meme's root claim.

The meme image itself is not shown. Children marked as visual elements are
written descriptions of the image content, and they are the annotator's only
account of it. This is a deliberate methodological constraint; its consequences
are discussed in `DESIGN.md`.

Each argument is annotated in two stages.

**The Sensible Test.** Three required yes/no judgements determine whether the
argument moves from its children to its claim by rational comprehension or by
rhetorical manipulation:

| Check | Question |
|---|---|
| Manipulation | Is the creator using emotional hijacking, mockery, side-by-side tricks, out-of-context cropping, or coded dog-whistles to force the connection in lieu of evidence? |
| Deception | Must a reader accept an unproven assumption, a warped timeline, or a structural lie for the children to connect to the claim? |
| Rhetorical wrapper | Is the visual format used as a psychological trap to lower critical thinking, rather than to illustrate data or literal facts? |

Three answers of No mean the argument passes, and nothing further is asked of
it. A single Yes opens the second stage.

**Mechanism classification.** The annotator selects one primary mechanism from a
closed set of six, gives a written justification tied to how that mechanism
operates in the argument, and may add any number of secondary mechanisms, each
with its own justification. The six mechanisms are Affective Hijacking, Episodic
Overgeneralization, Spurious Causal Correlation, Satirical Trivialization,
Esoteric Code Activation, and Spurious Legitimacy Signaling. Definitions are in
`guidelines.md`, which is rendered above every argument in the interface and is
the single place those definitions are maintained.

There is no overall pass/fail question. The verdict is derived at export time
from the three checks, so no annotator can record it inconsistently with them.
A free-text note and a flag for discussion are available on every argument.

Justifications must be at least 15 characters. The interface refuses to submit
an argument with a required field missing or a justification below that floor,
and names the offending questions rather than failing silently.

## Annotator workflow

Annotators sign in with a NetID; there are no passwords and no registration
step. A NetID the study has not seen creates an account on first use, and the
same NetID returns the annotator to the argument they left. The study does not
partition the corpus automatically: coverage is assigned by telling each
annotator which range of argument numbers to work through, and the
administrative console reports which arguments each person has completed.

Two actions are available at the foot of every argument. **Submit** completes
the argument and enforces every requirement. **Save draft** leaves it
unfinished, retains whatever has been answered, and moves on. Drafts are not
counted as finished work and are rejected by the export under `--strict`.

## Repository layout

```
config.yaml              Potato task definition: schemes, display logic, accounts
guidelines.md            Annotator guidelines, rendered into the interface
args_0911.json           Source corpus (argument structures extracted from memes)
data/                    Built corpus: instances.json and manifest.json
static/                  Interface stylesheet and client script
scripts/                 Build, serve, export, agreement, console, deployment
annotation_output/       One directory per annotator; the annotation store
```

| Script | Purpose |
|---|---|
| `build_instances.py` | Builds `data/instances.json` and the manifest from the source corpus |
| `serve.py` | Starts the study server; applies the drafts, guidelines and sign-in patches |
| `accounts.py` | NetID validation and sign-in; closes the unused password endpoints |
| `export_annotations.py` | Writes the downstream CSV and JSONL and validates every record |
| `agreement.py` | Inter-annotator agreement coefficients and confusion tables |
| `annotation_backend.py` | Administrative console (downloads, annotations, agreement, flags) |
| `mirror_to_postgres.py` | Copies annotations into the Postgres mirror |
| `mirror_loop.py` | Runs the mirror on a fifteen-minute schedule |
| `walk_study.py` | End-to-end test of every branch of the interface |
| `capture_ui.py` | Full-page screenshots of each interface state |
| `verify_deploy.py` | Confirms the deployed volume and session survive a redeploy |
| `resync_order.py` | Re-aligns existing annotators after a corpus rebuild |
| `pull_annotations.sh` | Copies the deployed annotations to a local working copy |

## Running the study locally

```bash
source .venv/bin/activate
nohup python scripts/serve.py > server.log 2>&1 &
```

The server is ready when `http://localhost:8765` returns 200, typically after
ten to twenty seconds. Start it through `scripts/serve.py` rather than
`potato start config.yaml`: the guidelines banner, the draft mechanism and the
sign-in changes are applied in that script, and a direct Potato invocation
produces a study without them.

The administrative console is a separate process and is not started with the
server:

```bash
python scripts/annotation_backend.py      # http://127.0.0.1:8766
```

It reads `annotation_output/` directly, writes nothing, and works with the study
server stopped.

## Deployment

The study runs on Railway as a single service with a mounted volume and an
adjacent Postgres database. Two properties of the deployment are not
negotiable. The service runs **one replica**, because Potato holds each
annotator's state in the memory of a single process and a second replica would
produce divergent copies. The volume is mounted at `annotation_output/`, because
the export, the agreement report and the console all resolve that path, and
because without it a redeploy would erase the study.

Full procedure, including environment variables, backups and the verification
steps to run before annotation begins, is in `DEPLOY.md`.

## Data and storage

Annotations are stored as one JSON file per annotator under
`annotation_output/<netid>/`. Each save rewrites that file through a temporary
file, an `fsync` and an atomic rename, so an interrupted write cannot leave a
partial record. These files are the source of truth for every downstream tool.

A separate process copies the annotations into Postgres every fifteen minutes.
The mirror is derived, never authoritative, and is deliberately kept out of the
save path: if it fails, annotation continues unaffected. It maintains three
tables. `annotation` holds the current state of each annotation; the
append-only `annotation_history` retains every version the mirror has ever
observed, which makes an accidental overwrite recoverable between volume
backups; and `mirror_run` records each pass, so that a mirror which has silently
stopped can be distinguished from one with nothing to copy.

## Export and validation

```bash
python scripts/export_annotations.py --strict
```

This writes `annotations.csv`, `annotations.jsonl` and `validation_report.json`
into `annotation_output/export/`, and exits non-zero if any record violates a
study constraint. Potato's own automatic CSV and JSONL exports are retained as a
safety net but are not the deliverable: they carry one sparse column per scheme
and label, no item fields and no timestamps.

The validator checks constraints the interface cannot enforce on its own: that a
mechanism is recorded only on an argument that failed the Sensible Test and
always on one that failed it, that no mechanism appears as both primary and
secondary, that every recorded mechanism carries a substantive justification and
no justification is orphaned from its mechanism, and that no partially answered
argument is exported. Running the script with `--self-test` exercises the
validator against synthetic violations.

## Inter-annotator agreement

The console's agreement tab reports Krippendorff's alpha, Fleiss' and Cohen's
kappa, alpha-MASI for the multi-label question, and observed agreement, computed
per question and per pair of annotators, with a confusion table for the
mechanism questions. Each coefficient is reported alongside the number of
arguments it was computed on, since a coefficient without its denominator cannot
be interpreted. Coefficients that are undefined state why rather than printing
nothing.

Agreement requires that two annotators independently annotate the same
arguments. Nothing in the software creates that overlap; it is produced by
assigning overlapping ranges.

Note that Potato's built-in `/admin/iaa` endpoint returns null for this study.
It scores only items with a per-item annotator cap of two or more, and this
study sets no cap because work is distributed by argument range. The console's
report is computed over the overlap actually present on disk and is the one to
read.

## Verification

```bash
python scripts/walk_study.py --url http://localhost:8765
python scripts/capture_ui.py --url http://localhost:8765
python scripts/export_annotations.py --self-test
```

`walk_study.py` signs in as a throwaway NetID beginning `walk`, exercises every
branch of the interface including the draft and refusal paths, and verifies what
was written to disk. Delete its `annotation_output/<netid>/` directory
afterwards, or its answers will be included in the agreement report.

## Constraints on modification

Two changes break the study silently and must not be made. Adding a `layout:`
block to `config.yaml` disables `display_logic`, which places every conditional
question permanently on screen with no error. Renaming an annotation scheme
after annotation has begun makes existing answers invisible to the admin view,
the agreement report and the export, again with no error. The reasoning for both
is recorded in `DESIGN.md`.

A corpus rebuild does not reorder annotators who have already started. Potato
assigns the ordering once, when an account first reaches the annotation phase,
and stores it in that annotator's state file. `scripts/resync_order.py` re-aligns
existing annotators to a rebuilt corpus while keeping each of them on the
argument they were working on; it must be run with the server stopped.

## Documentation

- `DESIGN.md` — the design of the task and the reasoning behind each decision
- `RUNNING.md` — operating the study, the console, accounts and data extraction
- `DEPLOY.md` — deployment, storage architecture and pre-launch verification
- `guidelines.md` — the annotator-facing guidelines and mechanism definitions
