#!/bin/sh
# Railway's start command. Two processes:
#   the study server, in the foreground, as PID 1 so signals reach it;
#   the Postgres mirror, beside it, on its own 15-minute clock.
# The mirror is started first and detached. If DATABASE_URL is unset it logs
# that and exits, and the study runs exactly as it would with no database.
set -e

python scripts/mirror_loop.py &

exec python scripts/serve.py --port "$PORT"
