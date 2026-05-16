#!/usr/bin/env bash
# WAL-G environment template — source this from postgres user's .bashrc or
# load via systemd EnvironmentFile= directive in the postgres unit.
#
# Storage backends supported: S3, GCS, Azure, Yandex Object Storage,
# DigitalOcean Spaces, Cloudflare R2, local FS.
#
# Reference: https://github.com/wal-g/wal-g/blob/master/docs/PostgreSQL.md

# ── S3 / R2 / Spaces (set ALL three below — backend agnostic) ────────────────
export WALG_S3_PREFIX="s3://yuzdanyuz-backups/wal-g/$(hostname -s)"
export AWS_ACCESS_KEY_ID="<from-secret>"
export AWS_SECRET_ACCESS_KEY="<from-secret>"
export AWS_REGION="auto"                       # 'auto' for R2; 'eu-central-1' for AWS
export AWS_ENDPOINT="https://<account>.r2.cloudflarestorage.com"
export AWS_S3_FORCE_PATH_STYLE="true"          # required for R2/Minio

# ── Compression + parallelism ────────────────────────────────────────────────
export WALG_COMPRESSION_METHOD="brotli"
export WALG_DOWNLOAD_CONCURRENCY="10"
export WALG_UPLOAD_CONCURRENCY="10"
export WALG_DELTA_MAX_STEPS="6"                # incremental: full every 7 days
export WALG_DELTA_ORIGIN="LATEST"

# ── Encryption (recommended for prod) ────────────────────────────────────────
# Generate keypair: gpg --full-gen-key (RSA 4096, no expiry, passphrase-less)
# export WALG_GPG_KEY_ID="ABCDEF1234567890"

# ── PostgreSQL hooks (set in postgresql.conf) ────────────────────────────────
# archive_mode = on
# archive_timeout = 60        # force WAL switch every 60s
# archive_command = 'wal-g wal-push %p'
# restore_command = 'wal-g wal-fetch %f %p'
