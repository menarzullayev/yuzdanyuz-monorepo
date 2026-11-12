# Database roles, row-level security and encryption at rest

Audit items B15 (least privilege) and C18 (encryption at rest).

## Three roles

| Role | Used by | Owns tables | RLS | Secret key |
|------|---------|-------------|-----|------------|
| `yuzdanyuz_owner` | the Helm migrate Job only | yes (schema `public`) | applies (`FORCE`) | `DB_MIGRATE_PASSWORD` |
| `yuzdanyuz` | web, channels, Celery workers, beat (via PgBouncer) | no; `SELECT/INSERT/UPDATE/DELETE` only | stage 1: bypassed (`BYPASSRLS`); stage 2: applies | `DB_PASSWORD` |
| `yuzdanyuz_backup` | backup CronJob (`pg_dump`) | no; read-only | bypassed (`BYPASSRLS`) | `DB_BACKUP_PASSWORD` |

Names come from `externalServices.postgres.{migrateUser,user,backupUser}` in the chart
values; the chart refuses to render when `migrateUser` equals `user`.

The runtime role cannot `ALTER`/`DROP` a table, change or drop a policy, turn RLS off, or
create tables (tests/security/test_db_least_privilege.py runs these against real Postgres).

### Two stages for RLS on the runtime role

**Stage 1 (now): `BYPASSRLS`.** Two measured blockers keep RLS off the runtime role:

1. Shared content: the policies hide another org's public mock exam questions (below).
2. Index use: RLS quals are security barriers, so a non-leakproof predicate such as the
   question search `ILIKE` cannot use its trigram index on a table whose RLS applies to
   the role, even while the bypass policy passes every row
   (`tests/integration/test_question_search_index.py` falls back to a sequential scan when
   the suite runs as a non-superuser owner under `FORCE`).

So the runtime role gets `BYPASSRLS` for now. What stage 1 already gives: it owns nothing,
has DML only, cannot change schema or policies; the owner role is used by the migrate Job
alone; policies are `FORCE`d and never raise.

**Stage 2: enforcement.** Once the policies model shared content and the hot search paths
no longer filter RLS tables with non-leakproof operators (e.g. search through a
`SECURITY DEFINER` function or a denormalised search table):

```sql
ALTER ROLE yuzdanyuz NOBYPASSRLS;
```

and set `RLS_ENFORCE_ORG_SESSIONS=true` in the same release. `TestEnforcementBlocker`
fails when the shared-content gap is closed.

### Create them (once, as a superuser or the managed-database admin)

```sql
CREATE ROLE yuzdanyuz_owner  LOGIN PASSWORD '<owner password>';
CREATE ROLE yuzdanyuz        LOGIN PASSWORD '<runtime password>'
       NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;   -- stage 1, see above
CREATE ROLE yuzdanyuz_backup LOGIN PASSWORD '<backup password>' BYPASSRLS;
GRANT pg_read_all_data TO yuzdanyuz_backup;          -- PostgreSQL 14+
```

New database:

```sql
CREATE DATABASE yuzdanyuz_db OWNER yuzdanyuz_owner;
\c yuzdanyuz_db
ALTER SCHEMA public OWNER TO yuzdanyuz_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Existing database where the runtime role `yuzdanyuz` owns everything (the setup before
this change):

```sql
\c yuzdanyuz_db
ALTER DATABASE yuzdanyuz_db OWNER TO yuzdanyuz_owner;
ALTER SCHEMA public OWNER TO yuzdanyuz_owner;
REASSIGN OWNED BY yuzdanyuz TO yuzdanyuz_owner;     -- tables, sequences, functions
ALTER ROLE yuzdanyuz BYPASSRLS;                     -- stage 1 (as a superuser)
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Then add `DB_MIGRATE_PASSWORD` and `DB_BACKUP_PASSWORD` to the `yuzdanyuz-secrets`
Secret (docs: [secrets.md](secrets.md)) and deploy. The migrate Job runs as the owner with
`DB_RUNTIME_ROLE=yuzdanyuz`, and after every `migrate` grants the runtime role DML on
every table and sequence, plus default privileges for tables created later
(`core/db_roles.py`). The same grants by hand:

```sql
GRANT USAGE ON SCHEMA public TO yuzdanyuz;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO yuzdanyuz;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO yuzdanyuz;
ALTER DEFAULT PRIVILEGES FOR ROLE yuzdanyuz_owner IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO yuzdanyuz;
ALTER DEFAULT PRIVILEGES FOR ROLE yuzdanyuz_owner IN SCHEMA public
      GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO yuzdanyuz;
```

Check:

```sql
SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = 'yuzdanyuz';  -- 0
SELECT relname FROM pg_class WHERE relrowsecurity AND NOT relforcerowsecurity;            -- none
```

## How RLS behaves now

Migration `ops.0002_rls_force_and_bypass` (`core/rls.py`):

* every policy reads the organisation through `app_current_org_id()`, which is NULL when
  no org is set (before, most policies raised `invalid input syntax for type uuid`);
* every RLS table has `FORCE ROW LEVEL SECURITY`, so the owner is filtered too;
* commands that had no policy (UPDATE on `catalog_importbatch`, `catalog_questionbank`,
  `catalog_questiondraft`, `exams_mockexamquestion`; INSERT/UPDATE on
  `catalog_questionversion`) got the table's own org filter — without it the runtime
  role could not run them at all;
* a permissive `rls_bypass` policy reads the session flag `app.rls_bypass`.

The flag is set on every new connection (`core/db_roles.py`):

| `RLS_ENFORCE_ORG_SESSIONS` | `RLS_BYPASS_WITHOUT_ORG` | flag | effect |
|---|---|---|---|
| `false` (default) | — | not sent (= `all`) | every session passes, as when the runtime role owned the tables |
| `true` | `true` | `on` | a session with an org (`RLSMiddleware`) is filtered to it; org-less sessions (workers, platform admin, anonymous pages) pass |
| `true` | `false` | `off` | org-less sessions see nothing (fail closed) |

**Why enforcement is off by default (measured 2026-11-12).** The full backend suite run
as a non-superuser table owner with the flag at `on`: 2353 passed, 10 failed. Eight were
test artefacts of the session-level org variable (gone since the variable became
transaction-local, #62).
The other two show a real gap: the policies do not model content shared across
organisations. A public mock exam is visible to another org, but its questions
(`catalog_question`, `catalog_questionversion` belong to the publishing org) are not, and
question-quality stats for shared questions are lost. Turning enforcement on today would
break B2B students taking platform mock exams.
`tests/security/test_db_least_privilege.py::TestEnforcementBlocker` pins this; when the
policies allow shared content it fails, and the default can flip.

What holds regardless of the flag: the runtime role owns nothing, cannot alter or drop
tables, change or drop policies, disable or un-force RLS, or create tables; `SET
row_security = off` makes its filtered queries fail instead of returning everything.

Both variables ride transaction-local on each statement (`core/db_rls.py`, audit E05), so
they survive PgBouncer transaction pooling. The bypass flag is only sent with enforcement
on; a statement without it (psql, the default mode) counts as `all`.

Limits: the flag is set by the application, so it protects against application bugs (a missing
tenant filter in an org request), not against SQL injection, which could set it.
`RLS_BYPASS_WITHOUT_ORG=false` (with enforcement on) makes org-less sessions see nothing;
use it only once every worker task sets an org.

New tenant tables: enable RLS with `core.rls.enable(cursor, table, clause)` in a
`RunPython` migration. The guard test fails for a policy that reads
`current_setting('app.current_org_id')` directly or a table without the bypass policy.

### PgBouncer

PgBouncer connects as the runtime role (`DB_PASSWORD`); the migrate Job and the backup
CronJob connect to Postgres directly with their own roles.

### Backup and restore

`pg_dump` turns row security off and fails on any table whose RLS would filter it, so the
backup CronJob connects as `yuzdanyuz_backup` (`BYPASSRLS`). Restores are unaffected:
`pg_restore` loads data before it re-creates policies and `FORCE`
([backup-restore runbook](../../../../docs/operations/backup-restore.md)).

## Encryption at rest

**Field level (application).** Secrets stored in the database — `AIProviderConfig.api_key`,
`WebhookEndpoint.secret`, the bot token and webhook secret in `ops.BotSettings` — are
Fernet tokens (`enc1:` prefix, `core/crypto.py`, `core/fields.py`). The admin never
renders them; `AIProviderConfig` shows the last four characters.

* `FIELD_ENCRYPTION_KEYS` — comma-separated Fernet keys; the first encrypts, all decrypt.
  Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
  Without it a key derived from `SECRET_KEY` is used (`check --deploy` warns,
  `core.W010`), and rotating `SECRET_KEY` would make the secrets unreadable.
* Moving an existing deployment from the derived key to an explicit one: set
  `FIELD_ENCRYPTION_KEYS=<new key>` (the derived key stays readable as a fallback), then run
  `python manage.py rotate_field_encryption`.
* Rotation: `FIELD_ENCRYPTION_KEYS=<new>,<old>` on every pod, run
  `python manage.py rotate_field_encryption` once, then `FIELD_ENCRYPTION_KEYS=<new>`.
  The command never prints a value and writes nothing if any row cannot be decrypted.
* `accounts.TOTPDevice.secret_enc` uses its own key (`TOTP_ENCRYPTION_KEY`,
  `apps/accounts/services/two_factor.py`) and is not covered by the command.

**Storage level (operator).** Postgres is an external host; nothing in this repository can
enforce its disk encryption, so it is a deployment requirement:

* the data directory, WAL and temporary files live on an encrypted volume (cloud disk
  encryption, LUKS/dm-crypt, or the managed service's encryption at rest), with keys held
  outside the database host;
* every backup copy is encrypted before it leaves the host — the backup CronJob
  encrypts with GPG to an offline public key and fails without one; WAL-G archives, if
  enabled, need `WALG_GPG_KEY_ID` / `WALG_LIBSODIUM_KEY` ([backup.md](backup.md));
* connections use TLS (`sslmode=require` or stricter) so data is not exposed in transit;
* Redis holds sessions and Celery payloads: same disk-encryption requirement for its
  persistence files, or run it without persistence.
