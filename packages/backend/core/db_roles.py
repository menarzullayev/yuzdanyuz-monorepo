"""Database roles: runtime grants and the per-connection RLS bypass default (audit B15).

Two roles in production (SQL in docs/deployment/database-roles.md):

* the owner role owns every table and is used only by the migrate Job;
* the runtime role (web, channels, workers, beat) owns nothing and gets plain DML grants,
  so PostgreSQL applies row-level security to it. FORCE ROW LEVEL SECURITY (core/rls.py)
  applies it to the owner too.

RLS policies filter by `app.current_org_id`. The `rls_bypass` policy passes a statement
only when `app.rls_bypass = 'all'`, which core/db_rls.py sends (transaction-local, so it
holds behind PgBouncer) on statements issued inside unscoped_context(): Celery workers,
platform admin, cross-tenant jobs. Without it the runtime role sees its org's rows, or
none without an org, plus content shared on purpose (ops.0003). The flag is set by the
application, so it guards against application bugs (a missing tenant filter), not against
SQL injection, which could set it as it could set the org.
"""

from __future__ import annotations

from django.conf import settings
from psycopg2 import sql


def grant_runtime_privileges(cursor, role: str) -> None:
    """DML on every current and future table/sequence in public; nothing else."""
    r = sql.Identifier(role)
    for stmt in (
        'GRANT USAGE ON SCHEMA public TO {r}',
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}',
        'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {r}',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {r}',
    ):
        cursor.execute(sql.SQL(stmt).format(r=r))


def post_migrate_grants(sender, using='default', **kwargs):
    """Run by the migrate Job (owner role): keep the runtime role's grants current."""
    from django.db import connections

    role = (getattr(settings, 'DB_RUNTIME_ROLE', '') or '').strip()
    if not role:
        return
    if sender is not None and getattr(sender, 'label', None) != 'ops':
        return  # post_migrate fires once per app; once is enough
    connection = connections[using]
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cur:
        cur.execute('SELECT current_user')
        if cur.fetchone()[0] == role:
            return  # single-role setup: nothing to grant to ourselves
        grant_runtime_privileges(cur, role)
