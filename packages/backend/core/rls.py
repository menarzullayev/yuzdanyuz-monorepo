"""Row-level security helpers for migrations (audit B15).

Every RLS policy compares against `app_current_org_id()`, which is NULL (never an error)
when no org is set, and every RLS table carries:

* one `<table>_org_<cmd>` policy per command, filtered by the org;
* a permissive `rls_bypass` policy, true only while `app.rls_bypass = 'all'`, which
  core/db_rls.py sends on statements issued inside unscoped_context() (workers, platform
  admin); unset means no bypass;
* FORCE ROW LEVEL SECURITY, so the table owner is filtered too.

New tenant tables: call `enable(cursor, table, clause)` from a RunPython migration, where
`clause` uses app_current_org_id(), e.g. "organization_id = app_current_org_id()".
tests/security/test_db_least_privilege.py fails for any policy that still reads
current_setting('app.current_org_id') directly or any RLS table without the bypass policy.
"""

from __future__ import annotations

import re

from psycopg2 import sql

COMMANDS = ('SELECT', 'INSERT', 'UPDATE', 'DELETE')

FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION app_current_org_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT NULLIF(current_setting('app.current_org_id', true), '')::uuid $$;

CREATE OR REPLACE FUNCTION app_rls_bypass() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'all' $$;
"""

# pg_policies deparses the original expressions into these forms
_RAW_ORG = [
    re.compile(
        r"\(NULLIF\(current_setting\('app\.current_org_id'::text, true\), ''::text\)\)::uuid"
    ),
    re.compile(r"\(current_setting\('app\.current_org_id'::text, true\)\)::uuid"),
    re.compile(r"\(current_setting\('app\.current_org_id'::text\)\)::uuid"),
]


def rewrite_expression(expr: str | None) -> str | None:
    if expr is None:
        return None
    for pattern in _RAW_ORG:
        expr = pattern.sub('app_current_org_id()', expr)
    if 'app.current_org_id' in expr:
        raise ValueError(f'unrecognised app.current_org_id expression in RLS policy: {expr}')
    return expr


def create_functions(cursor) -> None:
    cursor.execute(FUNCTIONS_SQL)


def _ident(name: str) -> sql.Identifier:
    return sql.Identifier(name)


def ensure_bypass_and_force(cursor, table: str) -> None:
    t = _ident(table)
    cursor.execute(sql.SQL('DROP POLICY IF EXISTS rls_bypass ON {t}').format(t=t))
    cursor.execute(
        sql.SQL(
            'CREATE POLICY rls_bypass ON {t} FOR ALL USING (app_rls_bypass()) '
            'WITH CHECK (app_rls_bypass())'
        ).format(t=t)
    )
    cursor.execute(sql.SQL('ALTER TABLE {t} ENABLE ROW LEVEL SECURITY').format(t=t))
    cursor.execute(sql.SQL('ALTER TABLE {t} FORCE ROW LEVEL SECURITY').format(t=t))


def enable(cursor, table: str, clause: str) -> None:
    """RLS for a new tenant table: one org policy per command + bypass + FORCE."""
    create_functions(cursor)
    t = _ident(table)
    for cmd in COMMANDS:
        name = _ident(f'{table}_org_{cmd.lower()}')
        cursor.execute(sql.SQL('DROP POLICY IF EXISTS {n} ON {t}').format(n=name, t=t))
        if cmd == 'INSERT':
            stmt = 'CREATE POLICY {n} ON {t} FOR INSERT WITH CHECK ({c})'
        elif cmd == 'UPDATE':
            stmt = 'CREATE POLICY {n} ON {t} FOR UPDATE USING ({c}) WITH CHECK ({c})'
        else:
            stmt = 'CREATE POLICY {n} ON {t} FOR ' + cmd + ' USING ({c})'
        cursor.execute(sql.SQL(stmt).format(n=name, t=t, c=sql.SQL(clause)))
    ensure_bypass_and_force(cursor, table)


def rls_tables(cursor) -> list[str]:
    cursor.execute(
        'SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace '
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity ORDER BY 1"
    )
    return [r[0] for r in cursor.fetchall()]


def harden_existing(cursor) -> list[str]:
    """Rewrite every existing policy to app_current_org_id(), fill in missing commands,
    add the bypass policy and FORCE RLS. Idempotent. Returns the tables touched."""
    create_functions(cursor)
    cursor.execute(
        'SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies '
        "WHERE schemaname = 'public' AND policyname <> 'rls_bypass'"
    )
    by_table: dict[str, dict[str, tuple[str | None, str | None]]] = {}
    for table, name, cmd, qual, check in cursor.fetchall():
        new_qual, new_check = rewrite_expression(qual), rewrite_expression(check)
        parts = []
        if new_qual is not None:
            parts.append(sql.SQL('USING ({})').format(sql.SQL(new_qual)))
        if new_check is not None:
            parts.append(sql.SQL('WITH CHECK ({})').format(sql.SQL(new_check)))
        if parts and (new_qual != qual or new_check != check):
            cursor.execute(
                sql.SQL('ALTER POLICY {n} ON {t} ').format(n=_ident(name), t=_ident(table))
                + sql.SQL(' ').join(parts)
            )
        by_table.setdefault(table, {})[cmd] = (new_qual, new_check)

    tables = rls_tables(cursor)
    for table in tables:
        cmds = by_table.get(table, {})
        if 'ALL' not in cmds:
            # Commands without a policy are denied outright once RLS applies to the runtime
            # role; give them the table's own org filter (INSERT check first: it never
            # includes the "OR is_public" read widening).
            base = (cmds.get('INSERT') or (None, None))[1] or (cmds.get('SELECT') or (None, None))[
                0
            ]
            for cmd in COMMANDS:
                if cmd in cmds or base is None:
                    continue
                name = _ident(f'{table}_org_{cmd.lower()}')
                t = _ident(table)
                c = sql.SQL(base)
                if cmd == 'INSERT':
                    stmt = sql.SQL('CREATE POLICY {n} ON {t} FOR INSERT WITH CHECK ({c})')
                elif cmd == 'UPDATE':
                    stmt = sql.SQL(
                        'CREATE POLICY {n} ON {t} FOR UPDATE USING ({c}) WITH CHECK ({c})'
                    )
                else:
                    stmt = sql.SQL('CREATE POLICY {n} ON {t} FOR ' + cmd + ' USING ({c})')
                cursor.execute(stmt.format(n=name, t=t, c=c))
        ensure_bypass_and_force(cursor, table)
    return tables
