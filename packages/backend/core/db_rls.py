"""Transaction-local RLS org variable on every statement — safe behind PgBouncer.

RLS policies read `current_setting('app.current_org_id')`. A session-level `SET`
(what RLSMiddleware used to do) breaks under PgBouncer transaction pooling: the
next statement may run on a different server connection, and the SET stays
behind on the old one for whichever client is handed that connection next.

Instead, while a tenant context is active (`core.tenant`), every statement is sent
as `SELECT set_config('app.current_org_id', '<org>', true); <statement>` in ONE
round trip. psycopg2 sends both through the simple query protocol, Postgres runs
them in one transaction (the explicit one, or an implicit one in autocommit), and
`is_local => true` drops the value when that transaction ends — so the variable can
never outlive the statement/transaction that needed it, on any pooling mode.

Consequences:
* the variable follows `tenant_context()` everywhere: requests, Celery tasks,
  management commands, Channels consumers (contextvars cross sync_to_async);
* no tenant context -> no variable (same as the old `RESET`);
* a statement that cannot run inside a transaction block (VACUUM, CREATE INDEX
  CONCURRENTLY) must not be issued inside a tenant context;
* server-side (named) cursors are skipped; settings disable them anyway
  (DISABLE_SERVER_SIDE_CURSORS) because transaction pooling cannot keep them.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db.backends.signals import connection_created

from core.tenant import get_current_org


class RLSStatementPrefix:
    """`connection.execute_wrappers` entry that scopes each statement to the current org."""

    def __call__(self, execute, sql, params, many, context):
        org = get_current_org()
        if org is not None and getattr(settings, 'ENABLE_RLS', True):
            raw_cursor = context['cursor'].cursor
            if getattr(raw_cursor, 'name', None) is None:
                # UUID round-trip validates the value, so inlining it is injection-safe
                # and keeps the user's parameter style (%s / %(name)s) untouched.
                org_id = uuid.UUID(str(org.pk))
                sql = f"SELECT set_config('app.current_org_id', '{org_id}', true); {sql}"
        return execute(sql, params, many, context)


_PREFIX = RLSStatementPrefix()


def install(connection) -> None:
    if connection.vendor != 'postgresql':
        return
    if not any(isinstance(w, RLSStatementPrefix) for w in connection.execute_wrappers):
        connection.execute_wrappers.append(_PREFIX)


def _on_connection_created(sender, connection, **kwargs):
    install(connection)


def connect_signal() -> None:
    connection_created.connect(_on_connection_created, dispatch_uid='core.db_rls.install')
    # connections opened before app loading finished (rare) get it too
    from django.db import connections

    for conn in connections.all(initialized_only=True):
        install(conn)
