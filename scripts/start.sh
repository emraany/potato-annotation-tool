#!/bin/sh
# Railway's start command. Four processes:
#   the study server, in the foreground, as PID 1 so signals reach it;
#   the Postgres mirror, on its own 15-minute clock;
#   the backend console on :8766, reached through its own Railway domain;
#   the review site on :8767, reached through its own Railway domain.
#
# The review site runs here and not as a service of its own because a Railway
# volume attaches to one service only, and it reads and writes
# annotation_output/. It needs no key: a pair signs in with their two NetIDs.
#
# EXPOSING THE CONSOLE IS A DELIBERATE DEVIATION from DEPLOY.md, which says
# "Do not put the backend console or the admin dashboard on the public domain."
# It is exposed here because the study needs to be shown to people who are not
# at this machine. What makes it survivable is that annotation_backend.py
# refuses every request that arrives without the study's admin key once it is
# bound off loopback (its `require_key` before_request hook), and that guard
# fails closed: no key file, no answers, 503.
#
# Which is why the key file is written here. The guard reads
# admin_api_key.txt, NOT the environment, and Potato does not write that file
# when admin_api_key comes from config -- so without these three lines every
# request to the console would 503. If POTATO_ADMIN_API_KEY is unset the
# console is not started at all, rather than started unguarded.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -n "$POTATO_ADMIN_API_KEY" ]; then
  printf '%s' "$POTATO_ADMIN_API_KEY" > "$ROOT/admin_api_key.txt"
  chmod 600 "$ROOT/admin_api_key.txt"
  python scripts/annotation_backend.py --host 0.0.0.0 --port 8766 &
else
  echo "POTATO_ADMIN_API_KEY is unset; backend console NOT started" >&2
fi

python scripts/mirror_loop.py &

python scripts/review_site.py --host 0.0.0.0 --port 8767 &

exec python scripts/serve.py --port "$PORT"
