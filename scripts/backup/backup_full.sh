#!/usr/bin/env bash
# Full base backup — push to remote object storage via WAL-G.
# Run as the `postgres` OS user with PGDATA pointing at the live cluster.
#
# Recommended schedule: daily at 03:00 (low traffic window).
# Retention: keep last 7 fulls; older delta archives expire automatically.

set -euo pipefail

LOG_TAG="[backup-full]"
PGDATA="${PGDATA:-/var/lib/postgresql/data}"

if [[ ! -d "$PGDATA" ]]; then
    echo "$LOG_TAG ERROR: PGDATA=$PGDATA not found" >&2
    exit 1
fi

if ! command -v wal-g >/dev/null 2>&1; then
    echo "$LOG_TAG ERROR: wal-g binary not in PATH" >&2
    exit 2
fi

source "$(dirname "$0")/wal-g-config.sh"

echo "$LOG_TAG starting at $(date -u --iso-8601=seconds)"
START_EPOCH=$(date +%s)

wal-g backup-push "$PGDATA"

ELAPSED=$(( $(date +%s) - START_EPOCH ))
echo "$LOG_TAG full backup pushed in ${ELAPSED}s"

echo "$LOG_TAG retention: keep last 7 full backups"
wal-g delete retain FULL 7 --confirm

echo "$LOG_TAG done at $(date -u --iso-8601=seconds)"
