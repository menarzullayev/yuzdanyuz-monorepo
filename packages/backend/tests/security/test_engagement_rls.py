"""RLS is enabled with all four policies on the engagement tenant tables (migration 0005)."""

import pytest
from django.db import connection

TABLES = [
    'engagement_quiz',
    'engagement_quizquestion',
    'engagement_quizplay',
    'engagement_quizplayanswer',
    'engagement_orgratingprofile',
]
ALL_CMDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}


def _rls_state(table):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT rowsecurity FROM pg_tables WHERE tablename = %s AND schemaname = 'public'",
            [table],
        )
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
@pytest.mark.parametrize('table', TABLES)
def test_policies_filter_on_organization(db, table):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT qual FROM pg_policies WHERE tablename = %s AND qual IS NOT NULL'
            " AND policyname <> 'rls_bypass'",
            [table],
        )
        quals = [r[0] for r in cur.fetchall()]
    assert quals and all('app_current_org_id()' in q for q in quals)


@pytest.mark.security
def test_typing_tables_are_deliberately_not_tenant_scoped(db):
    """Negative: the check must not claim RLS for the platform-wide typing tables."""
    assert _rls_state('engagement_typingrun') != (True, ALL_CMDS)
    assert _rls_state('engagement_typingtext') != (True, ALL_CMDS)
