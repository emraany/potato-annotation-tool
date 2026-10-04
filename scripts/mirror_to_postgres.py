#!/usr/bin/env python3
"""
Mirror the annotations on disk into Postgres.

    python scripts/mirror_to_postgres.py                # one pass, upsert changes
    python scripts/mirror_to_postgres.py --dry-run      # say what would change
    python scripts/mirror_to_postgres.py --verify       # compare disk and Postgres

The files under annotation_output/ stay the source of truth. This is a derived,
queryable, separately-backed copy. If it breaks, annotation continues -- which
is the whole reason it runs as its own process and not inside the study server.
DEPLOY.md step 1.3 is what happens when work like this is done inside a request.

WHAT A RECORD IS is not decided here. `export_annotations.collect` is imported
and called, so the mirror, the CSV export and the agreement report cannot drift
into three different ideas of what an annotation is. Adding a field to the
export adds it to the mirror.

`annotation_history` is append-only and is the point of the exercise. Volume
backups give daily snapshots; this gives every version of every annotation the
mirror ever saw, so an overwrite or an annotator clearing their own work is
recoverable between snapshots.

THE REVIEW SITE'S DATA is mirrored the same way, from
annotation_output/review/ through scripts/review.py:

  review_decision          the current decision per argument
  review_decision_history  every version of every decision, append-only
  review_task              what each pair was given; taken back = removed_at
  gold                     one row per argument, agreed or resolved
  gold_justification       its justifications, one row per text

Gold is rebuilt whole on every pass from `review.gold`, for the same reason
records come from `collect`: what counts as gold is decided in one place. It
runs in its own savepoint, so a review file that cannot be read fails the
review half of the pass and leaves the annotations mirrored.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_annotations  # noqa: E402
import review  # noqa: E402

# One boolean column per mechanism, so counting a mechanism is a WHERE, not an
# array search: "Affective Hijacking" -> mech_affective_hijacking.
MECH_COLUMNS = {m: "mech_" + m.lower().replace(" ", "_") for m in review.MECHANISMS}


SCHEMA = """
create table if not exists annotation (
  annotator_id    text not null,
  instance_id     text not null,
  item_number     integer,
  record          jsonb not null,
  content_hash    text  not null,
  last_saved_utc  timestamptz,
  mirrored_at     timestamptz not null default now(),
  primary key (annotator_id, instance_id)
);

create table if not exists annotation_history (
  id            bigserial primary key,
  annotator_id  text not null,
  instance_id   text not null,
  record        jsonb not null,
  content_hash  text not null,
  seen_at       timestamptz not null default now()
);

create table if not exists mirror_run (
  id           bigserial primary key,
  started_at   timestamptz not null,
  finished_at  timestamptz,
  rows_seen    integer,
  rows_changed integer,
  ok           boolean,
  error        text
);

alter table mirror_run add column if not exists review_rows_changed integer;
alter table mirror_run add column if not exists gold_rows integer;

create table if not exists review_decision (
  instance_id           text primary key,
  item_number           integer,
  sensible_test         text not null check (sensible_test in ('Pass', 'Fail')),
  mechanisms            text[] not null,
  justifications        jsonb not null,          -- {mechanism: text}
  resolution_note       text,
  resolved_by           text[] not null,
  resolved_at           timestamptz,
  category              text,                    -- the colour when it was resolved
  annotations_snapshot  jsonb not null,          -- what each annotator had said then
  revision              integer not null,        -- 1 + earlier versions
  record                jsonb not null,          -- the decision as on disk, minus history
  content_hash          text not null,
  mirrored_at           timestamptz not null default now()
);

create table if not exists review_decision_history (
  id            bigserial primary key,
  instance_id   text not null,
  record        jsonb not null,
  content_hash  text not null,
  seen_at       timestamptz not null default now()
);

create table if not exists review_task (
  id           text primary key,
  pair         text[] not null,
  range_from   integer not null,
  range_to     integer not null,
  colours      text[] not null,
  label        text,
  created_at   timestamptz,
  removed_at   timestamptz,
  mirrored_at  timestamptz not null default now()
);

create table if not exists gold (
  instance_id             text primary key,
  item_number             integer,
  source_index            integer,
  meme_id                 text,
  image_file              text,
  relation                text,
  claim_id                integer,
  sensible_test           text not null check (sensible_test in ('Pass', 'Fail')),
  mechanisms              text[] not null,
  mech_affective_hijacking boolean not null,
  mech_episodic_overgeneralization boolean not null,
  mech_spurious_causal_correlation boolean not null,
  mech_satirical_trivialization boolean not null,
  mech_esoteric_code_activation boolean not null,
  mech_spurious_legitimacy_signaling boolean not null,
  source                  text not null check (source in ('agreed', 'resolved')),
  annotators              text[] not null,
  n_annotators            integer not null,
  category                text,                  -- the colour before resolution
  verdict_split           boolean not null,
  resolved_by             text[],
  resolved_at             timestamptz,
  resolution_note         text,
  revision                integer,
  changed_since_resolved  text[] not null default '{}',
  built_at                timestamptz not null default now()
);

create table if not exists gold_justification (
  id           bigserial primary key,
  instance_id  text not null references gold (instance_id) on delete cascade,
  mechanism    text not null,
  annotator_id text,                             -- null: the resolved text
  text         text
);

create index if not exists annotation_annotator_idx on annotation (annotator_id);
create index if not exists annotation_item_number_idx on annotation (item_number);
create index if not exists annotation_history_lookup_idx
  on annotation_history (annotator_id, instance_id, seen_at desc);
create index if not exists review_decision_history_lookup_idx
  on review_decision_history (instance_id, seen_at desc);
create index if not exists gold_justification_instance_idx
  on gold_justification (instance_id);
create index if not exists gold_item_number_idx on gold (item_number);
"""


def content_hash(record: dict) -> str:
    """sha256 over canonical JSON.

    sort_keys and a fixed separator so a dict that merely reordered is not a
    change -- the hash is what decides whether a row is written at all, and a
    mirror that rewrites every row on every pass would fill the history table
    with duplicates and make it useless for exactly the recovery it exists for.
    """
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_disk() -> list[dict]:
    """Every annotation on disk, as the export defines it.

    An empty study is not an error here. `collect` exits when nobody has
    annotated yet, which is right for a human running an export and wrong for a
    job on a 15-minute timer -- the first scheduled run would page somebody
    about a study that is merely new.
    """
    instances = ROOT / "data" / "instances.json"
    items = {i["id"]: i for i in json.loads(instances.read_text())}

    if not (ROOT / "annotation_output").exists():
        return []
    try:
        records, _problems = export_annotations.collect(ROOT, items)
    except SystemExit:
        return []
    return records


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def mirror(conn, records: list[dict], dry_run: bool) -> int:
    changed = 0
    with conn.cursor() as cur:
        cur.execute("select annotator_id, instance_id, content_hash from annotation")
        known = {(a, i): h for a, i, h in cur.fetchall()}

        for record in records:
            key = (record["annotator_id"], record["instance_id"])
            digest = content_hash(record)
            if known.get(key) == digest:
                continue
            changed += 1
            if dry_run:
                continue

            payload = json.dumps(record, default=str)
            cur.execute(
                """
                insert into annotation (annotator_id, instance_id, item_number,
                                        record, content_hash, last_saved_utc,
                                        mirrored_at)
                values (%s, %s, %s, %s::jsonb, %s, %s, now())
                on conflict (annotator_id, instance_id) do update set
                  item_number    = excluded.item_number,
                  record         = excluded.record,
                  content_hash   = excluded.content_hash,
                  last_saved_utc = excluded.last_saved_utc,
                  mirrored_at    = now()
                """,
                (key[0], key[1], record.get("item_number"), payload, digest,
                 parse_ts(record.get("last_saved_utc"))),
            )
            # Append-only. Never updated, never deleted.
            cur.execute(
                """
                insert into annotation_history (annotator_id, instance_id,
                                                record, content_hash)
                values (%s, %s, %s::jsonb, %s)
                """,
                (key[0], key[1], payload, digest),
            )
    return changed


def read_review():
    """Decisions, tasks and gold, as the review site and the console see them."""
    items = review.load_items()
    try:
        records = review.load_records(items)
    except SystemExit:      # nobody has annotated yet; see read_disk
        records = []
    decisions = review.load_decisions()
    return decisions, review.load_tasks(), review.gold(items, records, decisions)


def decision_hash(decision: dict) -> str:
    return content_hash({k: v for k, v in decision.items() if k != "history"})


def mirror_review(conn, dry_run: bool) -> tuple[int, int]:
    """Decisions and tasks upserted by hash; gold rebuilt whole.

    Returns (decision and task rows changed, gold rows)."""
    decisions, tasks, gold = read_review()
    changed = 0
    with conn.cursor() as cur:
        cur.execute("select instance_id, content_hash from review_decision")
        known = dict(cur.fetchall())
        for instance_id, d in decisions.items():
            digest = decision_hash(d)
            if known.get(instance_id) == digest:
                continue
            changed += 1
            if dry_run:
                continue
            current = {k: v for k, v in d.items() if k != "history"}
            payload = json.dumps(current, default=str)
            cur.execute(
                """
                insert into review_decision (instance_id, item_number, sensible_test,
                  mechanisms, justifications, resolution_note, resolved_by, resolved_at,
                  category, annotations_snapshot, revision, record, content_hash, mirrored_at)
                values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, now())
                on conflict (instance_id) do update set
                  item_number = excluded.item_number, sensible_test = excluded.sensible_test,
                  mechanisms = excluded.mechanisms, justifications = excluded.justifications,
                  resolution_note = excluded.resolution_note, resolved_by = excluded.resolved_by,
                  resolved_at = excluded.resolved_at, category = excluded.category,
                  annotations_snapshot = excluded.annotations_snapshot,
                  revision = excluded.revision, record = excluded.record,
                  content_hash = excluded.content_hash, mirrored_at = now()
                """,
                (instance_id, d.get("item_number"), d["sensible_test"], d["mechanisms"],
                 json.dumps(d.get("justifications") or {}), d.get("note") or None,
                 d.get("resolved_by") or [], parse_ts(d.get("resolved_at")), d.get("category"),
                 json.dumps(d.get("annotations") or {}), len(d.get("history") or []) + 1,
                 payload, digest),
            )
            # Append-only. Never updated, never deleted.
            cur.execute(
                "insert into review_decision_history (instance_id, record, content_hash)"
                " values (%s, %s::jsonb, %s)", (instance_id, payload, digest))

        # A decision only leaves the file by hand. Its versions stay in history.
        gone = sorted(set(known) - set(decisions))
        changed += len(gone)
        if gone and not dry_run:
            cur.execute("delete from review_decision where instance_id = any(%s)", (gone,))

        cur.execute("select id, pair, range_from, range_to, colours, label, removed_at"
                    " from review_task")
        known_tasks = {r[0]: r[1:] for r in cur.fetchall()}
        for t in tasks:
            row = (t["pair"], t["from"], t["to"], t["colours"], t.get("label") or "", None)
            if known_tasks.get(t["id"]) == row:
                continue
            changed += 1
            if not dry_run:
                cur.execute(
                    """
                    insert into review_task (id, pair, range_from, range_to, colours, label,
                                             created_at, removed_at, mirrored_at)
                    values (%s, %s, %s, %s, %s, %s, %s, null, now())
                    on conflict (id) do update set
                      pair = excluded.pair, range_from = excluded.range_from,
                      range_to = excluded.range_to, colours = excluded.colours,
                      label = excluded.label, removed_at = null, mirrored_at = now()
                    """,
                    (t["id"], *row[:5], parse_ts(t.get("created"))))
        taken_back = [i for i, r in known_tasks.items()
                      if r[-1] is None and i not in {t["id"] for t in tasks}]
        changed += len(taken_back)
        if taken_back and not dry_run:
            cur.execute("update review_task set removed_at = now(), mirrored_at = now()"
                        " where id = any(%s)", (taken_back,))

        if not dry_run:
            cur.execute("delete from gold")   # cascades to gold_justification
            columns = ["instance_id", "item_number", "source_index", "meme_id", "image_file",
                       "relation", "claim_id", "sensible_test", "mechanisms",
                       *MECH_COLUMNS.values(), "source", "annotators", "n_annotators",
                       "category", "verdict_split", "resolved_by", "resolved_at",
                       "resolution_note", "revision", "changed_since_resolved"]
            cur.executemany(
                f"insert into gold ({', '.join(columns)})"
                f" values ({', '.join(['%s'] * len(columns))})",
                [(r["instance_id"], r["item_number"], r["source_index"], r["meme_id"],
                  r["image_file"], r["relation"], r["claim_id"], r["sensible_test"],
                  r["mechanisms"], *[m in r["mechanisms"] for m in MECH_COLUMNS],
                  r["source"], r["annotators"], len(r["annotators"]), r["category"],
                  r["verdict_split"], r["resolved_by"], parse_ts(r["resolved_at"]),
                  r["resolution_note"], r["revision"], r["changed_since_resolved"])
                 for r in gold])
            cur.executemany(
                "insert into gold_justification (instance_id, mechanism, annotator_id, text)"
                " values (%s, %s, %s, %s)",
                [(r["instance_id"], j["mechanism"], j["annotator"], j["text"])
                 for r in gold for j in r["justifications"]])
    return changed, len(gold)


def verify_review(conn) -> bool:
    """Decisions and gold, disk against Postgres. True when they match."""
    decisions, _, gold = read_review()
    with conn.cursor() as cur:
        cur.execute("select instance_id, content_hash from review_decision")
        db = dict(cur.fetchall())
        cur.execute("select instance_id from gold")
        db_gold = {r[0] for r in cur.fetchall()}
    disk = {i: decision_hash(d) for i, d in decisions.items()}
    drift = sorted(i for i in set(disk) | set(db) if disk.get(i) != db.get(i))
    disk_gold = {r["instance_id"] for r in gold}
    print(f"review: disk {len(disk)} decision(s), postgres {len(db)}; "
          f"gold disk {len(disk_gold)} row(s), postgres {len(db_gold)}")
    if drift:
        print(f"  {len(drift)} decision(s) differ: {', '.join(drift[:20])}")
    if disk_gold != db_gold:
        print(f"  gold differs: {len(disk_gold - db_gold)} missing, "
              f"{len(db_gold - disk_gold)} extra (gold is rebuilt each pass)")
    return not drift and disk_gold == db_gold


def verify(conn, records: list[dict]) -> int:
    """Compare disk against Postgres. Non-zero exit on any drift."""
    disk = {(r["annotator_id"], r["instance_id"]): content_hash(r) for r in records}
    with conn.cursor() as cur:
        cur.execute("select annotator_id, instance_id, content_hash from annotation")
        db = {(a, i): h for a, i, h in cur.fetchall()}

    missing = sorted(set(disk) - set(db))
    extra = sorted(set(db) - set(disk))
    stale = sorted(k for k in set(disk) & set(db) if disk[k] != db[k])

    print(f"disk {len(disk)} row(s), postgres {len(db)} row(s)")
    for label, keys in (("missing from postgres", missing),
                        ("in postgres but not on disk", extra),
                        ("hash differs", stale)):
        if keys:
            print(f"  {len(keys)} {label}:")
            for annotator, instance in keys[:20]:
                print(f"    {annotator}  {instance}")
            if len(keys) > 20:
                print(f"    ... and {len(keys) - 20} more")

    if missing or extra or stale:
        return 1
    print("in sync")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="one mirroring pass (the default)")
    mode.add_argument("--verify", action="store_true",
                      help="compare disk and Postgres; exit non-zero on drift")
    mode.add_argument("--dry-run", action="store_true",
                      help="count what would change, write nothing")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()

    if not args.database_url:
        raise SystemExit("set DATABASE_URL (or pass --database-url)")

    import psycopg

    records = read_disk()

    with psycopg.connect(args.database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()

        if args.verify:
            annotations = verify(conn, records)
            return 1 if not verify_review(conn) else annotations

        started = datetime.now(timezone.utc)
        try:
            changed = mirror(conn, records, args.dry_run)
        except Exception as exc:
            conn.rollback()
            # A run that failed has to leave a trace, or a mirror that has been
            # dead for a week looks exactly like a mirror with nothing to do.
            with conn.cursor() as cur:
                cur.execute(
                    "insert into mirror_run (started_at, finished_at, rows_seen,"
                    " rows_changed, ok, error) values (%s, now(), %s, 0, false, %s)",
                    (started, len(records), f"{type(exc).__name__}: {exc}"),
                )
            conn.commit()
            raise

        # Its own savepoint: a review file that cannot be read costs the
        # review half of the pass, never the annotations.
        review_changed, gold_rows, review_error = 0, None, None
        try:
            with conn.transaction():
                review_changed, gold_rows = mirror_review(conn, args.dry_run)
        except Exception as exc:
            review_error = f"review: {type(exc).__name__}: {exc}"

        if args.dry_run:
            conn.rollback()
            print(f"dry run: {len(records)} row(s) on disk, {changed} would change; "
                  + (review_error or f"review {review_changed} would change"))
            return 1 if review_error else 0

        with conn.cursor() as cur:
            cur.execute(
                "insert into mirror_run (started_at, finished_at, rows_seen,"
                " rows_changed, ok, error, review_rows_changed, gold_rows)"
                " values (%s, now(), %s, %s, %s, %s, %s, %s)",
                (started, len(records), changed, review_error is None, review_error,
                 review_changed, gold_rows),
            )
        conn.commit()
        print(f"mirrored {len(records)} row(s) on disk, {changed} changed; "
              + (review_error or f"review {review_changed} changed, gold {gold_rows} row(s)"))
    return 1 if review_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
