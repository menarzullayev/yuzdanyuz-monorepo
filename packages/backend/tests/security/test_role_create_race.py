"""Concurrent CREATE ROLE of the same cluster-wide role must not fail migrations.

pytest-xdist gives every worker its own test database, but roles are cluster-wide, so
ops/0003 runs ``CREATE ROLE yuzdanyuz_definer`` in several databases at once. The loser
of that race waits on pg_authid's unique index and then fails with unique_violation,
not duplicate_object (seen in CI: "duplicate key value violates unique constraint
pg_authid_rolname_index"). The guard has to catch both.
"""

import threading
import uuid

import psycopg2
import pytest
from django.db import connection
from psycopg2 import sql

GUARDED = (
    'DO $$ BEGIN CREATE ROLE {} NOLOGIN; '
    'EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$'
)
OLD = 'DO $$ BEGIN CREATE ROLE {} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$'


def _connect():
    params = connection.get_connection_params()
    conn = psycopg2.connect(**params)
    conn.autocommit = False
    return conn


def _race(statement: str) -> BaseException | None:
    """A creates the role and holds the transaction open; B runs ``statement`` meanwhile."""
    role = f'race_probe_{uuid.uuid4().hex[:8]}'
    a, b = _connect(), _connect()
    error: list[BaseException] = []
    try:
        a.cursor().execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))

        def run_b():
            try:
                with b.cursor() as cur:
                    cur.execute(sql.SQL(statement).format(sql.Identifier(role)))
                b.commit()
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                error.append(exc)
                b.rollback()

        t = threading.Thread(target=run_b)
        t.start()
        t.join(timeout=1)
        assert t.is_alive(), 'B should be blocked on the uncommitted role'
        a.commit()
        t.join(timeout=10)
        assert not t.is_alive()
        return error[0] if error else None
    finally:
        with a.cursor() as cur:
            cur.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))
        a.commit()
        a.close()
        b.close()


@pytest.mark.django_db(transaction=True)
class TestCreateRoleRace:
    def test_old_guard_fails_with_unique_violation(self):
        """Proves the race is real: catching only duplicate_object is not enough."""
        err = _race(OLD)
        assert isinstance(err, psycopg2.errors.UniqueViolation)

    def test_guard_survives_the_race(self):
        assert _race(GUARDED) is None

    def test_migration_and_test_helpers_use_the_full_guard(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        files = [
            root / 'apps/ops/migrations/0003_rls_enforce_shared_content.py',
            root / 'tests/pooling/test_rls_pgbouncer.py',
            root / 'tests/security/test_rls_transaction_scope.py',
        ]
        for f in files:
            text = f.read_text(encoding='utf-8')
            assert 'EXCEPTION WHEN duplicate_object THEN NULL' not in text, f
            assert 'duplicate_object OR unique_violation' in text, f
