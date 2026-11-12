# Database roles, row-level security and encryption at rest

Audit items B15 (least privilege) and C18 (encryption at rest).

## Roles

| Role | Used by | Owns tables | RLS | Secret key |
|------|---------|-------------|-----|------------|
| `yuzdanyuz_owner` | the Helm migrate Job only | yes (schema `public`) | `FORCE`d, passes via its role default `app.rls_bypass = 'all'` | `DB_MIGRATE_PASSWORD` |
| `yuzdanyuz` | web, channels, Celery workers, beat (via PgBouncer) | no; `SELECT/INSERT/UPDATE/DELETE` only | **enforced** (`NOBYPASSRLS`) | `DB_PASSWORD` |
| `yuzdanyuz_backup` | backup CronJob (`pg_dump`) | no; read-only | bypassed (`BYPASSRLS`) | `DB_BACKUP_PASSWORD` |
| `yuzdanyuz_definer` | nobody logs in (`NOLOGIN`) | owns three `SECURITY DEFINER` functions | bypassed (`BYPASSRLS`) | — |

Names come from `externalServices.postgres.{migrateUser,user,backupUser}` in the chart
values (the chart refuses `migrateUser == user`) and `DB_DEFINER_ROLE`.

The runtime role cannot `ALTER`/`DROP` a table, change or drop a policy, turn RLS off, or
create tables, and RLS applies to it: with no org it sees no tenant rows, with an org only
that org's rows plus content shared on purpose
(`tests/security/test_db_least_privilege.py`, `tests/security/test_rls_enforcement.py`,
against real Postgres).

### Create them (once, as a superuser or the managed-database admin)

```sql
CREATE ROLE yuzdanyuz_owner   LOGIN PASSWORD '<owner password>';
CREATE ROLE yuzdanyuz         LOGIN PASSWORD '<runtime password>'
       NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE yuzdanyuz_backup  LOGIN PASSWORD '<backup password>' BYPASSRLS;
GRANT pg_read_all_data TO yuzdanyuz_backup;              -- PostgreSQL 14+
CREATE ROLE yuzdanyuz_definer NOLOGIN BYPASSRLS;
GRANT yuzdanyuz_definer TO yuzdanyuz_owner;              -- lets migrations hand it functions
ALTER ROLE yuzdanyuz_owner SET app.rls_bypass = 'all';   -- migrations see every tenant's rows
```

`ALTER ROLE ... SET` for a custom parameter needs a superuser (measured on 16.15: the owner
gets `permission denied to set parameter "app.rls_bypass"`); without it the owner is
filtered like any role and a data migration would silently see no tenant rows.

New database:

```sql
CREATE DATABASE yuzdanyuz_db OWNER yuzdanyuz_owner;
\c yuzdanyuz_db
ALTER SCHEMA public OWNER TO yuzdanyuz_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

Existing database (runtime role owned everything before #63, or was `BYPASSRLS` after it):

```sql
\c yuzdanyuz_db
ALTER DATABASE yuzdanyuz_db OWNER TO yuzdanyuz_owner;
ALTER SCHEMA public OWNER TO yuzdanyuz_owner;
REASSIGN OWNED BY yuzdanyuz TO yuzdanyuz_owner;     -- tables, sequences, functions
ALTER ROLE yuzdanyuz NOBYPASSRLS;
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

Migrations `ops.0002_rls_force_and_bypass` and `ops.0003_rls_enforce_shared_content`
(`core/rls.py`):

* every policy reads the organisation through `app_current_org_id()`, which is NULL when
  no org is set (never `invalid input syntax for type uuid`);
* every RLS table has `FORCE ROW LEVEL SECURITY`;
* commands that had no policy got the table's own org filter;
* a permissive `rls_bypass` policy passes a statement only when `app.rls_bypass = 'all'`;
  unset means no bypass;
* shared content is readable across organisations (SELECT only): a question is shared when
  it is in a public question bank or one of its versions is in a public mock exam
  (`app_question_shared`, `app_question_visible`). Writes stay with the owning org.

What the application sends (`core/db_rls.py`, transaction-local on each statement, so it
holds behind PgBouncer transaction pooling):

| Code runs in | `app.current_org_id` | `app.rls_bypass` | the runtime role sees |
|---|---|---|---|
| a tenant request / `tenant_context(org)` | org | — | that org's rows + shared content |
| `unscoped_context()` (workers, platform admin, cross-tenant jobs) | any | `all` | everything |
| neither (anonymous page, stray code) | — | — | no tenant rows; shared content only |

Inside a transaction, once a statement carried a value, every later statement sends its
own, so nothing outlives its `tenant_context()` / `unscoped_context()` block (savepoint
statements are sent unchanged).

Application rules that keep this working:

* `global_objects` querysets evaluate inside `unscoped_context()` (they are the declared
  cross-tenant manager); `objects.for_org(org)` evaluates under that org;
* `save()` / `delete()` / `refresh_from_db()` from code without a tenant context run under
  the row's own organisation (`core.mixins.RLSRowMixin`; rows without an `organization`
  column use their parent's). A tenant request keeps its own org, so writing another
  org's row is still refused by the database;
* Celery tasks that receive a row id load it inside `unscoped_context()` and work under the
  row's org (`generate_export`, `evaluate_openended_submission`, `deliver_webhook`);
  management commands that touch every tenant (`rotate_field_encryption`) run unscoped.

Question search: RLS quals are security barriers, so a `LIKE` on an RLS table cannot use
its trigram index (E01). `app_search_question_versions` (owned by `yuzdanyuz_definer`)
runs the match with the index and applies the same visibility rule itself; the matched
rows are then loaded normally, under RLS.

Measured (2026-11-12): the full backend suite run as a non-superuser table owner, so RLS
applies to it like to the runtime role: 2529 passed; the 2 left are a probe role created
earlier by a superuser in the same cluster and are environment-only. Run as the CI
superuser: 2531 passed.

Limits: the org and the bypass flag are set by the application, so RLS here catches
application bugs (a missing tenant filter), not SQL injection, which could set them.

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
