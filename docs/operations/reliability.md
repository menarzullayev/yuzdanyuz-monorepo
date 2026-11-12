# Reliability settings (P1 audit, 2026-11-12)

What the backend does about connection limits, hung dependencies, lost tasks and
stale assets, and what an operator has to set when deploying. Recovery procedures
are in `packages/backend/docs/DR_RUNBOOK.md`.

## Postgres: PgBouncer and RLS (E05)

- App pods (web, channels, Celery) connect to `<release>-pgbouncer:6432`, transaction
  pooling (`charts/yuzdanyuz/templates/pgbouncer.yaml`). Two pools on one database:
  `<db>` (web/channels, `statement_timeout` 25 s) and `<db>_worker` (Celery workers set
  `DB_NAME=<db>_worker`, `statement_timeout` 300 s = task hard limit).
- Server connections: `replicas × (maxDbConnections + workerMaxDbConnections)` =
  2 × (30 + 10) = 80 by default. The chart refuses values above
  `externalServices.postgres.maxConnections − pgbouncer.reservedConnections` (100 − 15).
- The migrate Job and the backup CronJob connect to Postgres directly.
- Django: `DISABLE_SERVER_SIDE_CURSORS = True` (named cursors cannot survive transaction
  pooling); `DB_POOLER=pgbouncer` drops the `options` startup parameter, which PgBouncer
  rejects.
- **RLS under pooling.** The org variable used by the policies is no longer a
  session-level `SET`. `core/db_rls.py` sends
  `SELECT set_config('app.current_org_id', '<org>', true); <statement>` as one round trip
  for every statement issued inside `tenant_context()`. `is_local => true` ends with the
  transaction, so the value can never reach another client sharing the server
  connection. Proven by `tests/pooling/test_rls_pgbouncer.py` against PgBouncer with a
  single server connection:

  ```bash
  docker compose -f docker/pgbouncer/rls-test.compose.yml -p rls-test run --rm tests
  docker compose -f docker/pgbouncer/rls-test.compose.yml -p rls-test down -v
  ```

  Inside an explicit transaction the value stays until commit: leaving
  `tenant_context()` mid-transaction keeps the previous org for the rest of that
  transaction (more restrictive, never another tenant). Statements that cannot run in a
  transaction block (`VACUUM`, `CREATE INDEX CONCURRENTLY`) must not run inside a tenant
  context.

## Timeouts (F06)

| Hop | Setting | Default |
|---|---|---|
| Postgres connect | `DB_CONNECT_TIMEOUT` | 5 s |
| Postgres statement | `DB_STATEMENT_TIMEOUT_MS` (direct) / PgBouncer `connect_query` (pooled) | 30 s / 25 s web, 300 s workers |
| PgBouncer wait for a server connection | `query_wait_timeout` | 15 s |
| Idle-in-transaction | PgBouncer `idle_transaction_timeout` | 60 s |
| Redis (request path, cache) | `REDIS_SOCKET_CONNECT_TIMEOUT`, `REDIS_SOCKET_TIMEOUT` | 2 s, 2 s |
| Celery broker | `broker_transport_options` socket timeouts, `broker_connection_timeout` | 5 s |
| `.delay()` in a request | `task_publish_retry_policy` | ≤ 3 retries, ~1 s total |

Every Redis client goes through `core.infra.redis_from_url`.

## TLS in transit (C19)

- Postgres: `DB_SSLMODE` (`disable … verify-full`, validated at startup) and
  `DB_SSLROOTCERT` (`system` = OS trust store). Chart: `externalServices.postgres.sslmode`
  (`require` in `values-prod.yaml`) — applied by PgBouncer to the server side and by the
  migrate/backup pods directly. App → PgBouncer stays inside the namespace
  (NetworkPolicy).
- Redis / Celery / Channels: use `rediss://` URLs and TLS is on, with certificate
  verification (`REDIS_SSL_CERT_REQS=required` default, `REDIS_SSL_CA_CERTS` for a
  private CA).

## Celery delivery (F10, F11)

- `task_acks_late`, `task_reject_on_worker_lost`, `worker_prefetch_multiplier = 1`: a
  worker killed mid-task (rolling deploy, OOM) puts the task back. **Every task must be
  idempotent** — a re-run must not double money, rewards or messages. Money-moving work
  is guarded by row locks / unique constraints (wallet, payment intents), periodic jobs
  by single-run locks; `exams.finalize_attempt_score` recomputes from stored answers and
  retries on DB errors.
- `broker_transport_options.visibility_timeout` (3600 s) must stay above the longest
  task time limit, or Redis redelivers a task that is still running.
- Every periodic task is in `core/celery.py` `beat_schedule`; `DatabaseScheduler` copies
  it into django-celery-beat on start. Each scheduled task holds a Redis single-run lock
  (`core.locks.single_run` / `single_runner_lock`), so two beats never double a run.
  `tests/integration/test_beat_schedule.py` fails when a periodic task is not scheduled
  or not locked.

## Shared state (F13)

- Django cache: Redis (`REDIS_CACHE_URL`, default DB 3 of `REDIS_URL`), shared by all
  replicas.
- Celery workers mount the media PVC at `/app/media` (report exports are written there
  and served by web/nginx).

## Static files (F05)

- `CompressedManifestStaticFilesStorage`: collected names carry a content hash; the
  image runs `collectstatic` at build time.
- nginx sends `immutable` (1 year) only for hashed names, 5 min otherwise. In Helm, an
  initContainer (`copy-static`) copies the image's static files into nginx.

## SSE on ASGI (F17)

`GET /api/(v1/)exams/attempts/<id>/events/` is an async view; nginx routes it to the
Channels/Daphne service with buffering off. If it ever reaches gunicorn, it answers one
`timer` event and closes (the browser reconnects after 3 s) instead of holding a
thread.

## Deploy-time checklist

1. Postgres `max_connections` ≥ the chart budget (default 100); adjust
   `externalServices.postgres.maxConnections` to the real value.
2. `values-prod`: `externalServices.postgres.sslmode: require` (or `verify-full` +
   `sslrootcert`). Confirm the server accepts TLS before the release.
3. Redis with TLS: switch `REDIS_URL`, `CELERY_BROKER_URL`, `CHANNELS_REDIS_URL` secrets
   to `rediss://` (optional, when the provider supports it).
4. The first release runs migration `catalog 0012` (`pg_trgm` extension + two indexes,
   built `CONCURRENTLY`); the DB role must be allowed to `CREATE EXTENSION pg_trgm`
   (trusted extension: database owner is enough).
5. After the release: `kubectl get pods -l app.kubernetes.io/component=pgbouncer`,
   `kubectl logs deploy/<release>-celery-beat | grep -c "Scheduler: Sending"` grows,
   `curl -sI https://<host>/static/admin/css/base.css` shows `max-age=300`, a hashed
   name shows `immutable`.
