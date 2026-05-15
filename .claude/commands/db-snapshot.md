---
name: db-snapshot
description: Backup PostgreSQL database to local file. Use before risky migrations, before dropping tables, or as periodic dev backup.
---

Take a snapshot of the local PostgreSQL database.

## Workflow

1. **Verify PostgreSQL is running**:
   ```bash
   /home/hsm/.local/pgsql/bin/pg_ctl -D /home/hsm/.local/pgsql/data status
   ```
   - If not running: start it via `/home/hsm/scripts/start-postgres.sh`

2. **Create backup directory**:
   ```bash
   mkdir -p /home/hsm/backups/postgres
   ```

3. **Generate filename** with timestamp:
   ```bash
   STAMP=$(date +%Y%m%d-%H%M%S)
   FILE=/home/hsm/backups/postgres/yuzdanyuz_db_${STAMP}.sql.gz
   ```

4. **Dump** (custom format, compressed):
   ```bash
   /home/hsm/.local/pgsql/bin/pg_dump \
     -h 127.0.0.1 -p 5992 -U hsm -d yuzdanyuz_db \
     | gzip -9 > "$FILE"
   ```

5. **Verify**:
   ```bash
   ls -lh "$FILE"
   gunzip -t "$FILE"   # integrity check
   ```

6. **Cleanup old**: keep last 10 snapshots, delete older
   ```bash
   ls -t /home/hsm/backups/postgres/yuzdanyuz_db_*.sql.gz | tail -n +11 | xargs -r rm
   ```

7. **Output**:
   ```
   ✅ Snapshot: /home/hsm/backups/postgres/yuzdanyuz_db_20260516-001523.sql.gz
      Size: 4.3 MB
      Integrity: OK

   To restore:
     gunzip -c <file> | psql -U hsm -d yuzdanyuz_db
   ```

## Restore (when needed)

```bash
# Create fresh DB
/home/hsm/.local/pgsql/bin/createdb -h 127.0.0.1 -p 5992 -U hsm yuzdanyuz_db_restore

# Restore from snapshot
gunzip -c /home/hsm/backups/postgres/yuzdanyuz_db_20260516-001523.sql.gz \
  | /home/hsm/.local/pgsql/bin/psql -h 127.0.0.1 -p 5992 -U hsm -d yuzdanyuz_db_restore
```

## Constraints

- Local DB only (don't connect to prod from here)
- Backup goes outside repo (never commit DB dumps)
- Auto-rotate to keep last 10 snapshots
- Use SQL dump (text+gzip) not custom format — works across PostgreSQL versions
