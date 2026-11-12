"""Database roles: runtime grants and the per-connection RLS bypass default (audit B15).

Two roles in production (SQL in docs/deployment/database-roles.md):

* the owner role owns every table and is used only by the migrate Job;
* the runtime role (web, channels, workers, beat) owns nothing and gets plain DML grants,
  so PostgreSQL applies row-level security to it. FORCE ROW LEVEL SECURITY (core/rls.py)
  applies it to the owner too.

RLS policies filter by `app.current_org_id`; the `rls_bypass` policy reads the
`app.rls_bypass` flag, set on every new connection:

* 'all' (default, RLS_ENFORCE_ORG_SESSIONS=false): every session passes, as when the
  runtime role owned the tables. Enforcing org sessions hides content shared across
  organisations (public mock exams and their questions belong to the publishing org; the
  policies do not model sharing yet, measured in test_db_least_privilege), so it stays
  off until those policies exist.
* 'on' (RLS_ENFORCE_ORG_SESSIONS=true): a session with an org is filtered to it; org-less
  sessions (workers, platform admin, anonymous pages) pass.
* 'off' (also RLS_BYPASS_WITHOUT_ORG=false): org-less sessions see nothing.

What the split gives regardless of the flag: the runtime role owns nothing, cannot change
schema or policies, and cannot turn RLS off; the flag is the application's own choice.
"""

from __future__ import annotations

from django.conf import settings
from psycopg2 import sql


def bypass_default() -> str:
    """'all' until RLS_ENFORCE_ORG_SESSIONS is on; then 'on' (org-less sessions pass) or,
    with RLS_BYPASS_WITHOUT_ORG off, 'off' (fail closed)."""
    if not getattr(settings, 'RLS_ENFORCE_ORG_SESSIONS', False):
        return 'all'
    return 'on' if getattr(settings, 'RLS_BYPASS_WITHOUT_ORG', True) else 'off'


def on_connection_created(sender, connection, **kwargs):
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.rls_bypass', %s, false)", [bypass_default()])


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


def reset_org_after_request(sender, **kwargs):
    """RLSMiddleware sets app.current_org_id for the session; clear it once the response is
    finished (after a streaming body too), so code that runs later on the same persistent
    connection is not silently filtered to the last request's org."""
    from django.db import connection

    if connection.vendor != 'postgresql' or connection.connection is None:
        return  # closed (or never opened): nothing to reset, and no reconnect for it
    if connection.needs_rollback:
        return  # broken transaction: it is rolled back, and SET with it
    with connection.cursor() as cur:
        cur.execute('RESET app.current_org_id')
