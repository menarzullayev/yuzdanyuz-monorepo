#!/usr/bin/env bash
# Point-In-Time Recovery (PITR) restore from WAL-G archive.
#
# WARNING — this is a DESTRUCTIVE operation against the target $PGDATA.
# Do NOT run on a live primary. Run on a fresh node, then promote.
#
# Usage:
#   ./restore.sh                           # restore latest base, replay all WAL
#   ./restore.sh "2026-05-16 14:30:00 UTC" # PITR to a specific timestamp

set -euo pipefail

LOG_TAG="[restore]"
PGDATA="${PGDATA:-/var/lib/postgresql/data}"
RESTORE_TO="${1:-}"

if [[ -d "$PGDATA" ]] && [[ -n "$(ls -A "$PGDATA" 2>/dev/null)" ]]; then
    echo "$LOG_TAG ERROR: $PGDATA is not empty. Refusing to overwrite." >&2
    echo "$LOG_TAG If you really mean to wipe it, do so explicitly first." >&2
    exit 1
fi

if ! command -v wal-g >/dev/null 2>&1; then
    echo "$LOG_TAG ERROR: wal-g binary not in PATH" >&2
    exit 2
fi

source "$(dirname "$0")/wal-g-config.sh"

mkdir -p "$PGDATA"
chmod 700 "$PGDATA"

echo "$LOG_TAG fetching latest base backup → $PGDATA"
wal-g backup-fetch "$PGDATA" LATEST

# recovery.signal triggers PITR; restore_command is in postgresql.conf
touch "$PGDATA/recovery.signal"

if [[ -n "$RESTORE_TO" ]]; then
    echo "$LOG_TAG PITR target: $RESTORE_TO"
    cat >> "$PGDATA/postgresql.auto.conf" <<EOF
recovery_target_time = '$RESTORE_TO'
recovery_target_action = 'pause'
EOF
fi

echo "$LOG_TAG restore staged. Start postgres now and monitor pg_log:"
echo "$LOG_TAG   pg_ctl -D $PGDATA start"
echo "$LOG_TAG When recovery completes, run:"
echo "$LOG_TAG   psql -c \"SELECT pg_promote();\""
