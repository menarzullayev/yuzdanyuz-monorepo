"""E05: the RLS org variable is transaction-scoped, so it is safe behind PgBouncer.

Old behaviour: RLSMiddleware ran a session-level `SET app.current_org_id` once per
request. Under transaction pooling the next statement may run on another server
connection (no org -> RLS error) while the SET stays behind on the first one for
whichever client gets it next (cross-tenant leak). Now every statement that runs in
a tenant context carries `set_config(..., is_local => true)` in the same round trip.
"""

import pytest
from django.db import connection, transaction
from django.test import RequestFactory

from apps.webhooks.models import WebhookEndpoint
from core.middleware.rls_middleware import RLSMiddleware
from core.tenant import clear_current_org, tenant_context

APP_ROLE = 'rls_probe_app'


def _current_org_setting(cursor):
    cursor.execute("SELECT current_setting('app.current_org_id', true)")
    return cursor.fetchone()[0]


@pytest.mark.django_db
def test_tenant_context_reaches_the_database(org):
    with tenant_context(org), connection.cursor() as cur:
        assert _current_org_setting(cur) == str(org.id)


@pytest.mark.django_db(transaction=True)
def test_org_does_not_outlive_the_statement(org):
    # autocommit, as in a request: each statement is its own transaction
    with tenant_context(org), connection.cursor() as cur:
        cur.execute('SELECT 1')
    with connection.cursor() as cur:
        assert _current_org_setting(cur) in ('', None)


@pytest.mark.django_db(transaction=True)
def test_middleware_leaves_no_session_state_on_the_connection(org):
    """A pooled server connection must not carry one request's org into the next client."""
    request = RequestFactory().get('/')
    request.org = org
    RLSMiddleware(lambda r: None).process_request(request)
    clear_current_org()  # what TenantMiddleware does when the request ends
    with connection.cursor() as cur:
        assert _current_org_setting(cur) in ('', None)


@pytest.mark.django_db(transaction=True)
def test_org_set_inside_a_transaction_stays_local(org):
    with transaction.atomic():
        with tenant_context(org), connection.cursor() as cur:
            assert _current_org_setting(cur) == str(org.id)
    with connection.cursor() as cur:
        assert _current_org_setting(cur) in ('', None)


def _ensure_app_role():
    with connection.cursor() as cur:
        cur.execute(
            f"""
            DO $$ BEGIN CREATE ROLE {APP_ROLE} NOLOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END $$;
            """
        )
        cur.execute(f'GRANT SELECT ON webhooks_webhookendpoint TO {APP_ROLE}')


@pytest.mark.django_db(transaction=True)
def test_rls_filters_rows_for_a_non_owner_role(org, org2):
    """With RLS actually enforced (non-owner role), each tenant sees only its rows."""
    for o in (org, org2):
        with tenant_context(o):
            WebhookEndpoint.objects.create(organization=o, name=o.slug, url='https://e.x/h')
    _ensure_app_role()

    for o in (org, org2, org, org2):
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {APP_ROLE}')
            with tenant_context(o):
                cur.execute('SELECT organization_id FROM webhooks_webhookendpoint')
                seen = {str(r[0]) for r in cur.fetchall()}
        assert seen == {str(o.id)}
