"""RLS on intelligence_mistakecard: enabled, with all four org policies."""

import pytest
from django.db import connection

pytestmark = [pytest.mark.security, pytest.mark.django_db]

TABLE = 'intelligence_mistakecard'


def test_rls_enabled():
    with connection.cursor() as cursor:
        cursor.execute('SELECT rowsecurity FROM pg_tables WHERE tablename = %s', [TABLE])
        assert cursor.fetchone() == (True,)


def test_org_policies_for_every_command():
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT cmd, qual, with_check FROM pg_policies WHERE tablename = %s', [TABLE]
        )
        rows = {cmd: (qual or '') + (check or '') for cmd, qual, check in cursor.fetchall()}
    assert set(rows) == {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}
    assert all('app.current_org_id' in expr for expr in rows.values())


def test_check_catches_missing_policy():
    """Negative: the assertion above fails if a policy is dropped."""
    with connection.cursor() as cursor:
        cursor.execute('DROP POLICY "mistakecard_org_delete" ON intelligence_mistakecard')
        cursor.execute('SELECT cmd FROM pg_policies WHERE tablename = %s', [TABLE])
        assert 'DELETE' not in {r[0] for r in cursor.fetchall()}
