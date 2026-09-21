# Deploying the study

Written 2026-09-20, to be executed in a fresh session. Everything here was
checked against the code in `.venv/lib/python3.9/site-packages/potato`
(potato-annotation 2.9.3) and against this project's own scripts. File and line
references are given so nothing has to be re-derived.

The order matters. Steps 0–2 are what protect the data; step 3 is the database.

---

## The state today

There is **no database**. The study's storage is:

| What | Where | How |
|---|---|---|
| Annotations | `annotation_output/<username>/user_state.json` | Whole state rewritten per save — temp file, `fsync`, `os.replace` (`user_state_management.py:1554`). Atomic. |
| Accounts | `user_config.json` | PBKDF2-HMAC-SHA256, per-user 16-byte salt, constant-time compare (`authentication.py:53`). |
| Auto exports | `annotation_output/exports/{csv,jsonl}` | Every `auto_export_interval` seconds. A safety net, not the deliverable. |
| Admin key | `admin_api_key.txt` | Written at boot. Unlocks every annotation. |

`project.sqlite` is **not** the annotation store. Only `instance_fts*` (a
full-text index over the 1,621 instances) and `schema_migrations` have rows;
`codes`, `cases`, `memos`, `annotation_codes`, `review_items` are all zero —
they belong to Potato's QDA/codebook features, which this study does not use.
It is a regenerable index.

**As of writing, the study is empty**: no annotator directories exist,
`user_config.json` is 0 bytes. So there is nothing to migrate and the whole of
this document is free to execute. Once people start work, every step gets
harder. Do it now.

---

## The decision, and why

**Keep the files as the write path. Deploy to Railway with a volume. Mirror
into Postgres as the queryable, independently-backed copy.**

Do **not** switch Potato to its own database backend. The reasoning, since it
will be asked again:

Potato does ship one — `MysqlUserState` — and it is a real implementation, not
a stub: `add_label_annotation` does a proper `INSERT … ON DUPLICATE KEY UPDATE`
into a `label_annotations` table (`database/mysql_user_state.py:511`), and that
is the live path, because the browser saves through `/updateinstance` →
`routes.py:5955`/`:6250` → `add_label_annotation`. Annotations really would land
in SQL.

Three things make it the wrong trade here:

1. **It is MySQL only.** `database/connection.py` imports `mysql.connector`.
   There is no Postgres option, and the driver is not installed.
2. **It breaks on the first save.** `MysqlUserState.__init__` never calls
   `super().__init__()` and never sets `instance_id_to_label_to_value`,
   `instance_id_to_span_to_value` or `instance_id_to_behavioral_data`, and
   `UserState` defines no class-level defaults. But it inherits `save()`
   unchanged, and `save()` → `to_json()` reads all three
   (`user_state_management.py:1529`), while
   `UserStateManager.save_user_state()` calls `user_state.save(user_dir)`
   unconditionally for every backend (`:1049`). That is an `AttributeError` on
   the code path that runs every time an annotator submits. Fixable in a few
   lines — but it is a measure of how little traffic the backend has seen.
3. **It silently blanks this project's three tools.** `export_annotations.py`,
   `agreement.py` and `annotation_backend.py` all walk
   `annotation_output/*/user_state.json`. With annotations in SQL they would
   return zero rows, without erroring.

The file path is the one every Potato study exercises. In a study where the
failure mode of a storage bug is silent data loss discovered at analysis time,
boring and battle-tested beats elegant. The mirror in step 3 gives the real
database without putting it under the write path.

---

## Step 0 — the git landmine (do first, takes a minute)

`git rev-parse --show-toplevel` returns `/Users/emraan`. **The home directory is
a git repo**, with zero tracked files and no `.gitignore`. One `git add -A` from
anywhere commits `~/.ssh`, `~/.config`, `.claude.json` and every credential on
the machine.

Either give this project its own repo:

```bash
cd /Users/emraan/Desktop/potato-annotation-tool
git init
```

…or at minimum write `/Users/emraan/.gitignore`. Then, in this project:

```
# .gitignore
.venv/
annotation_output/
user_config.json
admin_api_key.txt
project.sqlite*
server.log
console.log
*.tmp
```

Never commit `user_config.json` (password hashes) or `admin_api_key.txt`.

---

## Step 1 — config changes

All in `config.yaml`. Current values are at the line numbers given.

**1. Close registration** (`:385`). Today anyone who finds the URL creates an
account and annotates into the corpus.

```yaml
user_config: {allow_all_users: false}
```

> **SUPERSEDED, 2026-09-20.** There are no passwords and no registration.
> An annotator types their NetID and is in (`require_password: false`,
> `allow_all_users: true`). The risk this step names is real and accepted, and
> is now larger: anyone with the URL who knows a NetID can annotate as that
> person. It is the trade for a small internal study with a known roster, and
> it is one line to reverse. `config.yaml` carries the reasoning and
> `RUNNING.md` the operating notes.

**2. Move the accounts file onto the volume** (`:387`). It must survive
redeploys, and the volume is mounted at `annotation_output/` (step 2).

```yaml
authentication:
  user_config_path: annotation_output/user_config.json
```

**3. Stop the five-minute stall** (`:396`). `auto_export_interval: 300` means
that every 300 seconds, whichever annotator saves next pays — synchronously,
inside their own request — for a full re-read of *every* annotator's state file
plus a CSV and JSONL rewrite (`user_state_management.py:1049` calls
`_maybe_auto_export`; the work is at `:1075`). It grows with the study. One
person's click randomly takes seconds.

```yaml
auto_export_interval: 3600
```

`scripts/export_annotations.py` is the real deliverable and runs on demand, so
this costs nothing.

**4. Add session and key settings** (new keys, anywhere top-level):

```yaml
secret_key: ${POTATO_SECRET_KEY}
admin_api_key: ${POTATO_ADMIN_API_KEY}
session_lifetime_days: 14
```

Both keys support `${VAR}` substitution (`config_module.py:6409`). Without
`secret_key`, `session_config.py:50` generates a random one per process and
**every annotator is logged out on every redeploy**. The default session
lifetime is 2 days (`session_config.py:18`).

Leave everything else alone. In particular the two warnings in `RUNNING.md`
still stand: do not add a `layout:` block, do not rename a scheme.

---

## Step 2 — Railway

The app binds `0.0.0.0` by default (`flask_server.py:6344`) and takes the port
from `--port`, so it is container-ready as-is.

**Service**

- Start command: `python scripts/serve.py --port $PORT`
- **Replicas: 1. Never more.** Potato holds each annotator's state in memory in
  one process (`app.run(..., threaded=True)`, `flask_server.py:6398`). Two
  replicas means two divergent copies of every annotator's state and last-write-
  wins on the file. This is a hard ceiling, not a tuning knob.
- Region: nearest to most annotators. One region only, so a distant annotator
  pays ~250ms per save. Fine, but pick deliberately.

**Volume — this is the step that prevents total data loss**

Mount it at `annotation_output/` inside the app directory (e.g.
`/app/annotation_output`). Mounting it *there* rather than at `/data` is
deliberate: `export_annotations.py:175` and `annotation_backend.py:68` both
hardcode `<root>/annotation_output`, so this keeps all three existing tools
working with no code change.

Without a volume, a redeploy wipes the study. This is the single largest risk
and it looks fine right up until it doesn't.

**Backups**

Enable **daily** volume backups in the service's Backups tab. Railway keeps
daily for 6 days, weekly for 27, monthly for 89; multiple schedules can run at
once. Two caveats worth knowing: wiping a volume deletes its backups, and a
backup can only be restored into the same project and environment. So keep a
weekly copy off Railway too — `annotation_output.zip` from the console's
Download tab, or the Postgres mirror from step 3.

**Environment variables**

```
POTATO_SECRET_KEY      = <openssl rand -hex 32>
POTATO_ADMIN_API_KEY   = <openssl rand -hex 32>
DATABASE_URL           = <from the Railway Postgres service, step 3>
```

No mail server: the study is passwordless, so there is no reset to send.

**TLS** is Railway's, automatically, on the generated domain. Do not serve this
over plain HTTP — annotators post passwords.

**Do not expose the backend console.** `scripts/annotation_backend.py` hands out
every annotation in the study. It binds loopback by default; keep it that way
and reach it over an SSH tunnel or run it locally against a downloaded copy.

---

## Step 3 — the Postgres mirror

A Railway Postgres service, plus `scripts/mirror_to_postgres.py`. The files stay
the source of truth; the mirror is a derived, queryable, separately-backed copy.
If it breaks, annotation continues.

**Why it is worth building rather than just relying on volume backups:** the
`annotation_history` table below is append-only. Volume backups give you daily
snapshots; the history table gives you *every version of every annotation the
mirror ever saw*. A corrupted state file, an accidental overwrite, or an
annotator clearing their own work is recoverable from it.

**Schema**

```sql
create table annotation (
  annotator_id    text not null,
  instance_id     text not null,
  item_number     integer,
  record          jsonb not null,
  content_hash    text  not null,
  last_saved_utc  timestamptz,
  mirrored_at     timestamptz not null default now(),
  primary key (annotator_id, instance_id)
);

create table annotation_history (      -- append-only; never updated or deleted
  id            bigserial primary key,
  annotator_id  text not null,
  instance_id   text not null,
  record        jsonb not null,
  content_hash  text not null,
  seen_at       timestamptz not null default now()
);

create table mirror_run (
  id           bigserial primary key,
  started_at   timestamptz not null,
  finished_at  timestamptz,
  rows_seen    integer,
  rows_changed integer,
  ok           boolean,
  error        text
);

create index on annotation (annotator_id);
create index on annotation (item_number);
create index on annotation_history (annotator_id, instance_id, seen_at desc);
```

**The job — `scripts/mirror_to_postgres.py`**

- Reuse `export_annotations.collect(ROOT, items)` (`export_annotations.py:173`)
  so there is exactly one definition of what an annotation record is. It already
  returns one dict per `(annotator_id, instance_id)` with the derived
  `sensible_test`, mechanisms and justifications.
- `content_hash` = sha256 of the canonical JSON of `record`. Skip rows whose
  hash is unchanged; that makes the job idempotent and cheap to run often.
- On a changed or new row: upsert into `annotation`, **and** insert into
  `annotation_history`. Never update or delete history.
- Wrap each run in a `mirror_run` row so a silently failing mirror is visible.
- Flags: `--once` (default), `--verify` (compare row counts and hashes between
  disk and Postgres, exit non-zero on drift), `--dry-run`.

**Running it**

A second Railway service on a cron schedule (every 15 minutes) sharing the same
volume, read-only. Do not run it inside the study process — that is what makes
the auto-export hiccup in step 1.4, and repeating the mistake is not an
improvement.

**Then, optionally**, point `agreement.py` and the console at Postgres instead
of the directory walk. Not required — they work unchanged, because the files
are still there. Do it only if querying gets painful.

---

## Step 4 — accounts

> **As built (2026-09-20), there is nothing to do here.** There are no
> accounts to create: an annotator types their NetID and is on argument 1 (see
> the note on step 1.1). No roster, no passwords, no redeploy to add somebody.
> What you send out is the URL and where to start. Removing somebody is one
> deletion now -- their `annotation_output/<netid>/` directory. The paragraph
> below applies only if passwords are put back.

With `allow_all_users: false`, create each annotator yourself. They still
register from the landing page, but only names you have permitted go through.

`RUNNING.md` has the removal procedure and it still applies: deleting an account
means deleting its entry from `user_config.json` **and** its
`annotation_output/<user>/` directory. One without the other leaves either
orphaned annotations in the export or a login with no data.

`user_config.json` is now on the volume, so it is inside the daily backup. Good
— losing it means no annotator can log back in, and their work orphans.

---

## Step 5 — verify before anyone starts

1. Deploy, then **redeploy immediately** and confirm an annotation made before
   the redeploy is still there. This is the volume test, and it is the one that
   matters most.
2. Restart the service and confirm you are *not* logged out. That is
   `POTATO_SECRET_KEY` working.
3. Register a throwaway annotator, annotate three arguments, run
   `python scripts/export_annotations.py` and confirm the rows appear. Then run
   the mirror and confirm the same three rows are in Postgres.
4. Delete the throwaway from both places (step 4).
5. Confirm the admin console and `:8766` backend are **not** reachable from the
   public domain.
6. Take one manual volume backup and restore it into a scratch environment, so
   the restore path has been walked once before you need it.

`python scripts/walk_study.py --url <deployed url>` exercises every branch
end-to-end and registers its own throwaway annotator — delete it afterwards.

---

## Operating notes for remote annotators

This works for people in different places, and the client is built for it:
failed saves keep the work on screen and retry on the next edit, with a
`sendBeacon` flush on page close, and a dead session interrupts loudly rather
than silently discarding (`static/annotation.js:1767`, and the comment at
`:1783` explaining why).

Tell annotators two things:

- **One tab, one device at a time.** The same person open twice shares one
  in-memory state on the server, and the last write wins.
- **If a save warning appears, do not close the tab.** The work is still on
  screen and the next edit resends it.

And know these yourself:

- Sessions last `session_lifetime_days` (14 after step 1.4).
- The URL should go to named annotators only. This is Werkzeug's development
  server, single process — right for ~10 known people, not built to sit on the
  open internet.
- Coverage is still settled by telling people where to start ("you take 1 to
  500"). Nothing enforces it; the console's **Who is where** table is how you
  see it. See `QUESTIONS.md` question 4.

---

## What not to do

- Do not run more than one replica.
- Do not deploy without a volume mounted at `annotation_output/`.
- Do not switch Potato to its MySQL backend (see *The decision*).
- Do not put the backend console or the admin dashboard on the public domain.
- Do not commit `user_config.json` or `admin_api_key.txt`.
- Do not add a `layout:` block to `config.yaml`, and do not rename a scheme
  after annotation starts. Both are in `RUNNING.md`; both fail silently.

---

## Order of work

1. Step 0 — `.gitignore` (minutes)
2. Step 1 — the four config changes (minutes)
3. Step 2 — Railway service, volume, backups, env vars
4. Step 5 items 1 and 2 — the redeploy and restart tests
5. Step 3 — Postgres and the mirror
6. Step 4, then the rest of step 5

Steps 1–2 are what protect the data. Step 3 is the database. Do not let step 3
delay steps 1–2.

`DESIGN.md` has the study's reasoning, `RUNNING.md` how to operate it,
`QUESTIONS.md` what is still open.
