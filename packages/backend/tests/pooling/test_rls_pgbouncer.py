"""E05: RLS stays correct through PgBouncer in transaction pooling mode.

Runs only inside the compose stack `docker/pgbouncer/rls-test.compose.yml` (see
docs there): Postgres + PgBouncer with ONE server connection, so every client is
multiplexed onto the same backend — the worst case for leaked session state.
The default Django connection goes straight to Postgres (pytest-django creates
the test database there); the connections under test go through PgBouncer.
"""

import os

import psycopg2
import pytest
from django.db import connection
from django.db.backends.postgresql.base import DatabaseWrapper
from django.test import RequestFactory

from apps.webhooks.models import WebhookEndpoint
from core.middleware import rls_middleware
from core.middleware.rls_middleware import RLSMiddleware
from core.tenant import clear_current_org, tenant_context

PGBOUNCER_HOST = os.getenv('PGBOUNCER_RLS_TEST_HOST')
pytestmark = pytest.mark.skipif(
    not PGBOUNCER_HOST, reason='needs the PgBouncer compose stack (PGBOUNCER_RLS_TEST_HOST)'
)
APP_ROLE = 'rls_pool_app'


def _pooled(alias, user='postgres'):
    settings = dict(connection.settings_dict)
    settings.update(HOST=PGBOUNCER_HOST, PORT='6432', USER=user, PASSWORD='', CONN_MAX_AGE=0)
    settings['OPTIONS'] = {'connect_timeout': 5}
    return DatabaseWrapper(settings, alias=alias)


def _org_setting(wrapper):
    with wrapper.cursor() as cur:
        cur.execute("SELECT current_setting('app.current_org_id', true)")
        return cur.fetchone()[0]


@pytest.fixture
def release_pool():
    yield
    # let pytest-django drop the test database: PgBouncer still holds a server connection
    admin = psycopg2.connect(host=PGBOUNCER_HOST, port=6432, user='postgres', dbname='pgbouncer')
    admin.autocommit = True
    with admin.cursor() as cur:
        db = connection.settings_dict['NAME']
        cur.execute(f'KILL {db}')  # drops its server connections ...
        cur.execute(f'RESUME {db}')  # ... and lets new clients in again
    admin.close()


@pytest.mark.django_db(transaction=True)
def test_one_request_org_never_reaches_another_client(org, release_pool, monkeypatch):
    a, b = _pooled('pool_a'), _pooled('pool_b')
    try:
        request = RequestFactory().get('/')
        request.org = org
        # what RLSMiddleware does for request A, on A's pooled connection
        monkeypatch.setattr(rls_middleware, 'connection', a, raising=False)
        RLSMiddleware(lambda r: None).process_request(request)
        clear_current_org()  # request A is over
        with tenant_context(org):
            assert _org_setting(a) == str(org.id)
        # B shares the single server connection and runs without any tenant
        assert _org_setting(b) in ('', None)
    finally:
        a.close()
        b.close()


@pytest.mark.django_db(transaction=True)
def test_interleaved_tenants_see_only_their_rows(org, org2, release_pool):
    for o in (org, org2):
        with tenant_context(o):
            WebhookEndpoint.objects.create(organization=o, name=o.slug, url='https://e.x/h')
    with connection.cursor() as cur:
        cur.execute(
            f"""
            DO $$ BEGIN CREATE ROLE {APP_ROLE} LOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
            """
        )
        cur.execute(f'GRANT SELECT ON webhooks_webhookendpoint TO {APP_ROLE}')

    c, d = _pooled('pool_c', APP_ROLE), _pooled('pool_d', APP_ROLE)
    try:
        for _ in range(10):
            for wrapper, o in ((c, org), (d, org2)):
                with tenant_context(o), wrapper.cursor() as cur:
                    cur.execute('SELECT organization_id FROM webhooks_webhookendpoint')
                    assert {str(r[0]) for r in cur.fetchall()} == {str(o.id)}
            # several statements in one transaction keep the org for the whole transaction
            with _atomic(c):
                with tenant_context(org), c.cursor() as cur:
                    cur.execute('SELECT count(*) FROM webhooks_webhookendpoint')
                    assert cur.fetchone()[0] == 1
                    cur.execute('SELECT organization_id FROM webhooks_webhookendpoint')
                    assert str(cur.fetchone()[0]) == str(org.id)
    finally:
        c.close()
        d.close()


class _atomic:
    """transaction.atomic for a DatabaseWrapper that is not in settings.DATABASES."""

    def __init__(self, wrapper):
        self.wrapper = wrapper

    def __enter__(self):
        self.wrapper.ensure_connection()
        self.wrapper.set_autocommit(False)

    def __exit__(self, exc_type, *exc):
        if exc_type:
            self.wrapper.rollback()
        else:
            self.wrapper.commit()
        self.wrapper.set_autocommit(True)


def _orgs_seen(wrapper):
    with wrapper.cursor() as cur:
        cur.execute('SELECT DISTINCT organization_id FROM webhooks_webhookendpoint')
        return {str(r[0]) for r in cur.fetchall()}


@pytest.mark.django_db(transaction=True)
def test_unscoped_bypass_never_reaches_another_client(org, org2, release_pool):
    """B15: the explicit bypass rides on the statement like the org does, so a pooled
    client never inherits it from another client (or from an earlier statement)."""
    from core.tenant import unscoped_context

    for o in (org, org2):
        with tenant_context(o):
            WebhookEndpoint.objects.create(organization=o, name=o.slug, url='https://e.x/h')
    with connection.cursor() as cur:
        cur.execute(
            f"""
            DO $$ BEGIN CREATE ROLE {APP_ROLE} LOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
            """
        )
        cur.execute(f'GRANT SELECT ON webhooks_webhookendpoint TO {APP_ROLE}')

    both = {str(org.id), str(org2.id)}
    e, f = _pooled('pool_e', APP_ROLE), _pooled('pool_f', APP_ROLE)
    try:
        for _ in range(5):
            with unscoped_context():
                assert _orgs_seen(e) == both
            assert _orgs_seen(f) == set()  # same server connection, no bypass
            assert _orgs_seen(e) == set()  # nor for e once its block is over
    finally:
        e.close()
        f.close()

