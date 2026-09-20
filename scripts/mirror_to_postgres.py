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

create index if not exists annotation_annotator_idx on annotation (annotator_id);
create index if not exists annotation_item_number_idx on annotation (item_number);
create index if not exists annotation_history_lookup_idx
  on annotation_history (annotator_id, instance_id, seen_at desc);
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
            return verify(conn, records)

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

        if args.dry_run:
            conn.rollback()
            print(f"dry run: {len(records)} row(s) on disk, {changed} would change")
            return 0

        with conn.cursor() as cur:
            cur.execute(
                "insert into mirror_run (started_at, finished_at, rows_seen,"
                " rows_changed, ok) values (%s, now(), %s, %s, true)",
                (started, len(records), changed),
            )
        conn.commit()
        print(f"mirrored {len(records)} row(s) on disk, {changed} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
