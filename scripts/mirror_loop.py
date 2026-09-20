#!/usr/bin/env python3
"""
Run scripts/mirror_to_postgres.py on a timer, forever.

WHY THIS EXISTS RATHER THAN A CRON SERVICE.  DEPLOY.md step 3 asks for the
mirror to run as "a second Railway service on a cron schedule sharing the same
volume, read-only". Railway does not allow that: a volume attaches to exactly
one service ("Each service can only have a single volume",
docs.railway.com/volumes/reference), so a second service cannot see
annotation_output/ at all. The two alternatives were to have the mirror pull
annotations over the admin HTTP API -- which returns a reshaped payload, not
the on-disk user_state.json, and would therefore need a second definition of
what an annotation is, the exact thing step 3 says to avoid -- or to run it
beside the server in the same container. This is the second.

What step 1.3 actually forbids is doing this work *inside an annotator's
request*, which is what makes auto-export stall somebody's click. This is a
separate OS process on its own clock; no annotator ever waits on it.

Each pass is a subprocess, so a mirror that hangs, leaks or crashes cannot take
the study server with it -- the worst case is a skipped pass and a line in the
log. The study keeps running with no database at all, which is the property
step 3 asked for.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

INTERVAL = int(os.environ.get("MIRROR_INTERVAL_SECONDS", "900"))   # 15 minutes
TIMEOUT = int(os.environ.get("MIRROR_TIMEOUT_SECONDS", "600"))


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} [mirror] {message}", flush=True)


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        log("DATABASE_URL is not set; mirror disabled, annotation is unaffected")
        return 0

    log(f"started; every {INTERVAL}s, {TIMEOUT}s timeout per pass")
    while True:
        started = time.monotonic()
        try:
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "mirror_to_postgres.py")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=TIMEOUT,
            )
            output = (done.stdout or done.stderr or "").strip().splitlines()
            tail = output[-1] if output else "(no output)"
            if done.returncode == 0:
                log(f"ok in {time.monotonic() - started:.1f}s: {tail}")
            else:
                log(f"FAILED rc={done.returncode}: {tail}")
        except subprocess.TimeoutExpired:
            log(f"FAILED: pass exceeded {TIMEOUT}s and was killed")
        except Exception as exc:  # never let the loop die
            log(f"FAILED: {type(exc).__name__}: {exc}")

        time.sleep(max(1, INTERVAL - int(time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
