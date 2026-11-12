"""Database roles: runtime grants and the per-connection RLS bypass default (audit B15).

Two roles in production (SQL in docs/deployment/database-roles.md):

* the owner role owns every table and is used only by the migrate Job;
* the runtime role (web, channels, workers, beat) owns nothing and gets plain DML grants,
  so PostgreSQL applies row-level security to it. FORCE ROW LEVEL SECURITY (core/rls.py)
  applies it to the owner too.

RLS policies filter by `app.current_org_id`; the `rls_bypass` policy reads the
`app.rls_bypass` flag. Both ride transaction-local on each statement (core/db_rls.py), so
they hold behind PgBouncer transaction pooling; an unset flag counts as 'all':

* 'all' (default, RLS_ENFORCE_ORG_SESSIONS=false, flag not sent): every session passes, as
  when the runtime role owned the tables. Enforcing org sessions hides content shared across
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
    """Flag value for this process: 'all' until RLS_ENFORCE_ORG_SESSIONS is on; then 'on'
    (org-less sessions pass) or, with RLS_BYPASS_WITHOUT_ORG off, 'off' (fail closed)."""
    if not getattr(settings, 'RLS_ENFORCE_ORG_SESSIONS', False):
        return 'all'
    return 'on' if getattr(settings, 'RLS_BYPASS_WITHOUT_ORG', True) else 'off'


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
