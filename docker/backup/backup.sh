#!/bin/sh
# Encrypted logical backup of the yuzdanyuz database.
#
# Required env:
#   PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD   database connection
#   BACKUP_DESTINATION                           rclone path, e.g. dest:yuzdanyuz-backups/postgres
#   BACKUP_GPG_PUBLIC_KEY_FILE                   armored public key of the backup recipient
#   RCLONE_CONFIG_<REMOTE>_*                     rclone remote settings (from a Secret)
# Optional env:
#   BACKUP_RETENTION_DAYS (default 30)           older objects under BACKUP_DESTINATION are deleted
#   BACKUP_WORK_DIR (default /work)
#
# The plain dump never leaves the pod's emptyDir and is removed after encryption.
set -eu

log() { echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

: "${PGHOST:?PGHOST is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGUSER:?PGUSER is required}"
: "${PGPASSWORD:?PGPASSWORD is required}"
: "${BACKUP_DESTINATION:?BACKUP_DESTINATION is required (rclone remote:path)}"
: "${BACKUP_GPG_PUBLIC_KEY_FILE:?BACKUP_GPG_PUBLIC_KEY_FILE is required}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
WORK="${BACKUP_WORK_DIR:-/work}"

[ -s "$BACKUP_GPG_PUBLIC_KEY_FILE" ] || fail "GPG public key file is empty: $BACKUP_GPG_PUBLIC_KEY_FILE"

# The remote is configured only through RCLONE_CONFIG_* env; an empty config file
# keeps rclone from warning about a missing one on every call.
export RCLONE_CONFIG="${RCLONE_CONFIG:-$WORK/rclone.conf}"
: > "$RCLONE_CONFIG"

export GNUPGHOME="$WORK/.gnupg"
mkdir -p "$GNUPGHOME"
chmod 700 "$GNUPGHOME"
gpg --batch --quiet --import "$BACKUP_GPG_PUBLIC_KEY_FILE"
RECIPIENT="$(gpg --batch --with-colons --list-keys | awk -F: '/^fpr:/ {print $10; exit}')"
[ -n "$RECIPIENT" ] || fail "no usable key in $BACKUP_GPG_PUBLIC_KEY_FILE"
if gpg --batch --with-colons --list-secret-keys 2>/dev/null | grep -q '^sec'; then
  fail "the key file contains a PRIVATE key; mount only the public key"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="${PGDATABASE}-${STAMP}.dump"
DUMP="$WORK/$NAME"

log "pg_dump ${PGDATABASE}@${PGHOST}:${PGPORT:-5432} -> $NAME"
pg_dump --format=custom --compress=6 --file="$DUMP"
# A dump pg_restore cannot list is not a backup.
pg_restore --list "$DUMP" > /dev/null || fail "pg_restore cannot read the dump"
log "dump ok: $(wc -c < "$DUMP") bytes"

gpg --batch --yes --trust-model always --recipient "$RECIPIENT" \
    --output "$DUMP.gpg" --encrypt "$DUMP"
rm -f "$DUMP"
( cd "$WORK" && sha256sum "$NAME.gpg" > "$NAME.gpg.sha256" )
log "encrypted for key $RECIPIENT"

DEST="${BACKUP_DESTINATION%/}"
rclone copyto --error-on-no-transfer "$DUMP.gpg" "$DEST/$NAME.gpg"
rclone copyto "$WORK/$NAME.gpg.sha256" "$DEST/$NAME.gpg.sha256"
# Size check against the remote copy: catches a truncated upload.
LOCAL_SIZE="$(wc -c < "$DUMP.gpg" | tr -d ' ')"
REMOTE_SIZE="$(rclone size --json "$DEST/$NAME.gpg" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')"
[ "$LOCAL_SIZE" = "$REMOTE_SIZE" ] || fail "uploaded size $REMOTE_SIZE != local $LOCAL_SIZE"
log "uploaded $DEST/$NAME.gpg ($LOCAL_SIZE bytes)"

if [ "$RETENTION_DAYS" -gt 0 ]; then
  rclone delete --min-age "${RETENTION_DAYS}d" --include "${PGDATABASE}-*.dump.gpg*" "$DEST"
  log "retention: removed objects older than ${RETENTION_DAYS}d"
fi
rm -f "$DUMP.gpg" "$WORK/$NAME.gpg.sha256"
log "done"
