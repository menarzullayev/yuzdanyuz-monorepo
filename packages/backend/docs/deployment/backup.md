# Backup — WAL-G + PITR

PostgreSQL Point-In-Time Recovery via [WAL-G](https://github.com/wal-g/wal-g).

| Tool      | Schedule        | Retention            | Storage           |
|-----------|-----------------|----------------------|-------------------|
| Full base | Daily 03:00 UTC | 7 fulls              | S3 / R2 / Spaces  |
| WAL stream| Continuous      | Implicit (full anchor)| Same bucket      |
| PITR window| —              | ~7 days              | RPO ~1 min        |

Files:
- [`scripts/backup/wal-g-config.sh`](../../../../scripts/backup/wal-g-config.sh) — env config
- [`scripts/backup/backup_full.sh`](../../../../scripts/backup/backup_full.sh) — daily full backup
- [`scripts/backup/restore.sh`](../../../../scripts/backup/restore.sh) — PITR restore
- [`charts/yuzdanyuz/templates/cronjob-backup.yaml`](../../../../charts/yuzdanyuz/templates/cronjob-backup.yaml) — K8s CronJob

## Setup (one-time, on PostgreSQL primary)

### 1. Install wal-g

```bash
WG_VERSION=v3.0.7
curl -sSL "https://github.com/wal-g/wal-g/releases/download/${WG_VERSION}/wal-g-pg-ubuntu-22.04-amd64.tar.gz" \
    | tar -xz -C /usr/local/bin
mv /usr/local/bin/wal-g-pg-ubuntu-22.04-amd64 /usr/local/bin/wal-g
chmod +x /usr/local/bin/wal-g
```

### 2. Configure storage

`scripts/backup/wal-g-config.sh`'ni copy qiling va `<from-secret>`'larni to'ldiring.

S3 (AWS):
```bash
WALG_S3_PREFIX="s3://yuzdanyuz-backups/wal-g/db-primary"
AWS_REGION="eu-central-1"
```

Cloudflare R2:
```bash
WALG_S3_PREFIX="s3://yuzdanyuz-backups/wal-g/db-primary"
AWS_REGION="auto"
AWS_ENDPOINT="https://<account>.r2.cloudflarestorage.com"
AWS_S3_FORCE_PATH_STYLE="true"
```

### 3. PostgreSQL config

`postgresql.conf`:
```conf
wal_level = replica
archive_mode = on
archive_timeout = 60                # force WAL switch every 60s — tighter RPO
archive_command = 'wal-g wal-push %p'
restore_command = 'wal-g wal-fetch %f %p'   # only used during recovery
max_wal_senders = 5
wal_keep_size = 1024MB
```

Restart PostgreSQL → first base backup:
```bash
sudo -u postgres /opt/yuzdanyuz/scripts/backup/backup_full.sh
```

### 4. Schedule daily backup (cron — bare-metal)

```bash
# /etc/cron.d/wal-g-backup
0 3 * * * postgres /opt/yuzdanyuz/scripts/backup/backup_full.sh >> /var/log/wal-g.log 2>&1
```

### 5. Or schedule via K8s CronJob

```bash
helm upgrade yuzdanyuz charts/yuzdanyuz \
  --set backup.enabled=true \
  --set backup.schedule="0 3 * * *"
```

Pre-req: `walg-credentials` Secret + PV mount with PGDATA access.

## Restore — PITR

```bash
# 1. Stop the broken primary
sudo systemctl stop postgresql

# 2. Empty the data dir (or create a new node)
mv /var/lib/postgresql/data /var/lib/postgresql/data.broken

# 3. Restore latest base + WAL
PGDATA=/var/lib/postgresql/data \
  /opt/yuzdanyuz/scripts/backup/restore.sh

# OR with PITR target:
PGDATA=/var/lib/postgresql/data \
  /opt/yuzdanyuz/scripts/backup/restore.sh "2026-05-16 14:30:00 UTC"

# 4. Start postgres — replay completes; if PITR was set, it pauses at target
sudo systemctl start postgresql

# 5. Promote (exit recovery)
sudo -u postgres psql -c "SELECT pg_promote();"
```

## Verification — restore drill

Quarterly run a full restore drill on a staging server:

```bash
# 1. Take a fresh full backup
./scripts/backup/backup_full.sh

# 2. Note current wal lsn + timestamp
psql -c "SELECT pg_current_wal_lsn(), now();"
# → 0/8000180 | 2026-05-16 14:30:00+00

# 3. Make a marker change
psql -c "CREATE TABLE backup_drill_marker(id int);"
psql -c "INSERT INTO backup_drill_marker VALUES (42);"

# 4. Restore on a fresh node, PITR to BEFORE the marker
PGDATA=/tmp/pg-drill ./scripts/backup/restore.sh "2026-05-16 14:30:00 UTC"

# 5. Verify marker is absent
psql -c "SELECT * FROM backup_drill_marker;"   # → ERROR: relation does not exist
```

## Monitoring

`BackupTooOld` alert in [`monitoring/prometheus/alerts.yml`](../../../../monitoring/prometheus/alerts.yml)
fires if `walg_last_backup_timestamp` is >25h old.

To expose this metric, run `wal-g-prometheus-exporter` as a sidecar
(future PR — not in this Task 10 scope).

## Encryption (recommended for prod)

Generate GPG keypair (passphrase-less, RSA 4096):
```bash
gpg --full-gen-key
gpg --export-secret-keys --armor <KEY_ID> > walg.key
```

Set in `wal-g-config.sh`:
```bash
export WALG_GPG_KEY_ID="ABCDEF1234567890"
```

Mount the private key in the backup pod via Secret.
