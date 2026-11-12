# PostgreSQL backup and restore

How the production database is backed up, and the exact steps to get it back.
The restore procedure below was rehearsed end to end on 2026-11-12 (see
[Last rehearsal](#last-rehearsal)); repeat the drill at least once a quarter and
after any change to the backup job.

## What runs

The Helm chart's `CronJob` `<release>-backup` (`charts/yuzdanyuz/templates/cronjob-backup.yaml`)
runs the image built from `docker/Dockerfile.backup` once a day (`backup.schedule`,
default 03:00 UTC). Each run (`docker/backup/backup.sh`):

1. `pg_dump --format=custom` of `externalServices.postgres.name` over the network,
   as `externalServices.postgres.user` with `DB_PASSWORD` from `yuzdanyuz-secrets`;
2. `pg_restore --list` on the dump — an unreadable dump fails the job;
3. encrypts it with GPG to the **public** key in `backup.gpgPublicKey`, then deletes
   the plain dump;
4. uploads `<db>-<UTC timestamp>.dump.gpg` and its `.sha256` with rclone to
   `backup.destination`, and compares the remote size with the local one;
5. deletes objects under the destination older than `backup.retentionDays` (30).

Any failing step fails the Job (`backoffLimit: 2`); watch for failed Jobs:
`kubectl get jobs -l app.kubernetes.io/component=backup`.

The cluster never holds the private key: whoever compromises the cluster can write
new backups but cannot read old ones.

The host-level WAL-G scripts in `scripts/backup/` (continuous archiving / PITR) are
separate and only apply when PostgreSQL runs on a host we manage.

## Setting it up (once per environment)

`values-prod.yaml` turns backups on. Until the three values below are set, `helm
template` / `helm upgrade` stops with an error naming the missing value — on
purpose, so production never runs with a backup that silently does nothing.

1. **Key pair**, on an offline workstation, never in the cluster:

   ```bash
   gpg --quick-gen-key "yuzdanyuz backups <ops contact>" rsa4096 encrypt 5y
   gpg --armor --export <fingerprint> > backup-public.asc
   gpg --armor --export-secret-keys <fingerprint> > backup-private.asc
   ```

   Store `backup-private.asc` and its passphrase in two separate offline places
   (password manager + sealed paper copy). Losing it makes every backup useless.

2. **Destination**: any rclone remote (S3 / MinIO / Backblaze B2 / SFTP / WebDAV).
   Name it `dest` and put its settings in a Secret as `RCLONE_CONFIG_DEST_*` keys.
   S3 example:

   ```bash
   kubectl -n <ns> create secret generic yuzdanyuz-backup-rclone \
     --from-literal=RCLONE_CONFIG_DEST_TYPE=s3 \
     --from-literal=RCLONE_CONFIG_DEST_PROVIDER=Other \
     --from-literal=RCLONE_CONFIG_DEST_ENDPOINT=https://<s3 endpoint> \
     --from-literal=RCLONE_CONFIG_DEST_ACCESS_KEY_ID=<key id> \
     --from-literal=RCLONE_CONFIG_DEST_SECRET_ACCESS_KEY=<secret>
   ```

   Use credentials that can write and list but, if the provider allows, not delete
   objects younger than the retention window (object lock / versioning), so a
   compromised cluster cannot wipe the backups. The destination must be outside the
   server that runs the database.

3. **Deploy** with:

   ```bash
   helm upgrade --install yuzdanyuz charts/yuzdanyuz \
     -f charts/yuzdanyuz/values.yaml -f charts/yuzdanyuz/values-prod.yaml \
     --set backup.destination=dest:<bucket>/postgres \
     --set backup.rcloneSecret=yuzdanyuz-backup-rclone \
     --set-file backup.gpgPublicKey=backup-public.asc
   ```

4. **Run one backup now** and check it landed:

   ```bash
   kubectl -n <ns> create job backup-manual-$(date +%s) --from=cronjob/<release>-backup
   kubectl -n <ns> logs -f job/backup-manual-...
   ```

### Database role

The job connects as the app's database user, which owns the tables. 17 tables use
row-level security (not `FORCE`), which the owner bypasses. If you ever point the
job at a different role, that role needs `BYPASSRLS`; otherwise `pg_dump` fails
with `query would be affected by row-level security policy for table ...` (measured
on 2026-11-12) — the job fails loudly rather than producing a partial dump.

## Restore

Run this on an operator workstation (or a throwaway pod) that has `rclone`,
`gpg` and PostgreSQL 16 client tools (`pg_restore`, `psql`, `createdb`). The
`yuzdanyuz-backup` image has all three.

1. **Pick the backup.**

   ```bash
   rclone lsl dest:<bucket>/postgres
   LATEST=$(rclone lsf --include "*.dump.gpg" dest:<bucket>/postgres | sort | tail -1)
   ```

2. **Download and verify** it was not truncated or altered:

   ```bash
   rclone copy --include "$LATEST*" dest:<bucket>/postgres .
   sha256sum -c "$LATEST.sha256"
   ```

3. **Decrypt** with the offline private key:

   ```bash
   export GNUPGHOME=$(mktemp -d)
   gpg --import backup-private.asc
   gpg --decrypt -o restore.dump "$LATEST"
   pg_restore --list restore.dump | head        # sanity check
   ```

4. **Restore into a new, empty database first** — never over the live one:

   ```bash
   export PGHOST=<target host> PGUSER=<owner role> PGPASSWORD=...
   createdb yuzdanyuz_restore
   pg_restore --exit-on-error --no-owner --role=<owner role> -j 4 \
     -d yuzdanyuz_restore restore.dump
   ```

5. **Verify** before switching anything:

   ```bash
   psql -d yuzdanyuz_restore -c "select count(*) from django_migrations;"
   psql -d yuzdanyuz_restore -c "select count(*) from accounts_customuser;"
   psql -d yuzdanyuz_restore -c "select count(*) from pg_class where relrowsecurity;"   # expect 17+
   ```

   Compare with the numbers you expect (or with the old database, if it is still
   readable).

6. **Cut over** — stop writers first so nothing is lost or split:

   ```bash
   kubectl -n <ns> scale deploy -l app.kubernetes.io/instance=<release> --replicas=0
   ```

   Then either point `externalServices.postgres.name` at `yuzdanyuz_restore` and
   `helm upgrade`, or rename the databases (`ALTER DATABASE yuzdanyuz_db RENAME TO
   yuzdanyuz_db_broken; ALTER DATABASE yuzdanyuz_restore RENAME TO yuzdanyuz_db;`)
   and scale back up with `helm upgrade` (it restores replica counts). Keep the
   broken database until the restored one has served traffic for a while.

7. **Clean up** the decrypted dump and the temporary `GNUPGHOME`; they hold the
   whole database in clear text.

Data written after the backup's timestamp is lost (RPO: up to 24 h with the daily
schedule). If that is too much, schedule more often or use WAL archiving.

## Last rehearsal

2026-11-12, on a local `kind` cluster with the chart at this commit:

- backup Job: dump 518,974 bytes → encrypted 161,569 bytes, uploaded, size checked;
- restore into an empty PostgreSQL 16 following the steps above: checksum OK,
  `pg_restore --exit-on-error` clean, a 5,000-row marker table identical (same
  row count and md5 of all rows), 124 migrations and 17 RLS tables present;
- a GPG home without the private key could not decrypt the file;
- the job refuses to start when given a private key, no destination, or an
  unreachable database.
