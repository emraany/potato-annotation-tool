#!/bin/sh
# Copy every annotator's work off the Railway volume into ./annotation_output,
# so the backend console and the export scripts can be run locally.
#
#     sh scripts/pull_annotations.sh
#     python scripts/annotation_backend.py        # http://127.0.0.1:8766
#
# The console is deliberately not deployed: it hands out every annotation in
# the study and binds loopback only (DEPLOY.md, "Do not expose the backend
# console"). This is the "run it locally against a downloaded copy" half of
# that instruction.
#
# Read-only against the volume. Nothing here writes to or deletes from Railway.
# user_config.json is skipped on purpose -- it holds password hashes and no
# tool on this side needs it.
set -e

VOLUME="${VOLUME:-study-volume}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/annotation_output"

mkdir -p "$OUT"

echo "listing $VOLUME ..."
DIRS=$(railway volume files --volume "$VOLUME" list / --json 2>/dev/null \
  | sed -n '/{/,$p' \
  | python3 -c '
import json, sys
skip = {"exports", "lost+found", "pocket"}
for f in json.load(sys.stdin)["files"]:
    if f["type"] == "directory" and f["name"] not in skip:
        print(f["name"])
')

if [ -z "$DIRS" ]; then
  echo "no annotator directories on the volume yet"
  exit 0
fi

echo "$DIRS" | while IFS= read -r dir; do
  [ -z "$dir" ] && continue
  echo "  pulling $dir"
  rm -rf "$OUT/$dir"
  railway volume files --volume "$VOLUME" download "/$dir" "$OUT/$dir" --json >/dev/null 2>&1 \
    || echo "    WARNING could not pull $dir"
done

echo
echo "into $OUT:"
ls -1 "$OUT" | sed 's/^/  /'
echo
echo "now:  python scripts/annotation_backend.py     # http://127.0.0.1:8766"
