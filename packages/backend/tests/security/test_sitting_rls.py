"""RLS is enabled and policies exist on every sitting table (migration 0009)."""

import pytest
from django.db import connection

TABLES = [
    'exams_gradescale',
    'exams_examsitting',
    'exams_sittingitem',
    'exams_sittingresult',
    'exams_certificate',
]
ALL_CMDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}


def _rls_state(table):
    with connection.cursor() as cur:
        cur.execute('SELECT rowsecurity FROM pg_tables WHERE tablename = %s', [table])
        row = cur.fetchone()
        cur.execute(
            "SELECT cmd FROM pg_policies WHERE tablename = %s AND policyname <> 'rls_bypass'",
            [table],
        )
        return (row[0] if row else None), {r[0] for r in cur.fetchall()}


@pytest.mark.security
@pytest.mark.parametrize('table', TABLES)
def test_rls_enabled_with_all_four_policies(db, table):
    assert _rls_state(table) == (True, ALL_CMDS)


@pytest.mark.security
def test_check_catches_table_without_rls(db):
    """Negative: a table that never got the migration must not satisfy the same check."""
    with connection.cursor() as cur:
        cur.execute('CREATE TEMP TABLE sitting_rls_probe (id int)')
    # temp tables live in pg_temp, not 'public' pg_tables rows we assert on -> no state found
    assert _rls_state('sitting_rls_probe') != (True, ALL_CMDS)
