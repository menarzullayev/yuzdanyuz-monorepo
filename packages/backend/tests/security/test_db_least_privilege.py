"""B15 — the runtime database role cannot bypass row-level security.

The test connection is a superuser (which always bypasses RLS), so every check switches
to a plain role with SET LOCAL ROLE inside the test transaction (rolled back afterwards).

* every RLS table has FORCE ROW LEVEL SECURITY, so even the table owner is filtered;
* the runtime role created by core.db_roles owns nothing and only gets DML grants;
* policies never raise when no organisation is set. A session with an org is always
  filtered; an org-less session (workers, platform admin, anonymous) sees everything only
  while the explicit `app.rls_bypass` flag is on (set per connection, see core.db_roles).
"""

import uuid

import pytest
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.test import override_settings

from apps.webhooks.models import WebhookEndpoint
from core.tenant import tenant_context

T = 'webhooks_webhookendpoint'


def _rls_tables(cur):
    cur.execute(
        "SELECT c.relname, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity"
    )
    return dict(cur.fetchall())


@pytest.fixture
def two_orgs_rows(org, org2):
    rows = {}
    for o in (org, org2):
        with tenant_context(o):
            rows[o.id] = WebhookEndpoint.objects.create(
                organization=o, name=f'h-{o.slug}', url='https://e.com/'
            )
    return rows


def _role(cur, *, owner_of=None):
    name = f'p1_probe_{uuid.uuid4().hex[:8]}'
    cur.execute(f'CREATE ROLE {name} NOLOGIN')
    if owner_of:
        cur.execute(f'ALTER TABLE {owner_of} OWNER TO {name}')
    return name


def _orgs_seen(cur, org_id=None, bypass='off'):
    cur.execute("SELECT set_config('app.rls_bypass', %s, true)", [bypass])
    if org_id is None:
        cur.execute('RESET app.current_org_id')
    else:
        cur.execute("SELECT set_config('app.current_org_id', %s, true)", [str(org_id)])
    cur.execute(f'SELECT DISTINCT organization_id FROM {T}')  # noqa: S608
    return {r[0] for r in cur.fetchall()}


@pytest.mark.django_db
class TestForceRls:
    def test_every_rls_table_forces_rls_on_its_owner(self):
        with connection.cursor() as cur:
            tables = _rls_tables(cur)
        assert tables, 'no RLS tables found'
        assert [t for t, forced in tables.items() if not forced] == []

    def test_table_owner_is_filtered_too(self, two_orgs_rows, org):
        with connection.cursor() as cur:
            role = _role(cur, owner_of=T)
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                assert _orgs_seen(cur, org.id) == {org.id}
            finally:
                cur.execute('RESET ROLE')

    def test_every_policy_uses_the_safe_helpers(self):
        """New RLS migrations must use core.rls (guard against raw current_setting::uuid)."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tablename, policyname, coalesce(qual, '') || ' ' || "
                "coalesce(with_check, '') FROM pg_policies WHERE schemaname = 'public'"
            )
            rows = cur.fetchall()
        raw = [(t, p) for t, p, expr in rows if "current_setting('app.current_org_id'" in expr]
        assert raw == []
        tables = {t for t, _, _ in rows}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT tablename FROM pg_policies WHERE schemaname = 'public' "
                "AND policyname = 'rls_bypass'"
            )
            with_bypass = {r[0] for r in cur.fetchall()}
        assert tables - with_bypass == set()


@pytest.mark.django_db
class TestRuntimeRole:
    @pytest.fixture
    def runtime(self, db):
        from core import db_roles

        with connection.cursor() as cur:
            role = _role(cur)
            db_roles.grant_runtime_privileges(cur, role)
        return role

    def test_runtime_role_owns_nothing_and_cannot_bypass(self, runtime):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=%s",
                [runtime],
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                'SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles '
                'WHERE rolname = %s',
                [runtime],
            )
            assert cur.fetchone() == (False, False, False, False)

    def test_runtime_role_sees_only_its_org(self, runtime, two_orgs_rows, org, org2):
        with connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {runtime}')
            try:
                assert _orgs_seen(cur, org.id) == {org.id}
                assert _orgs_seen(cur, org2.id) == {org2.id}
                # no org and no bypass: fail closed, and no error from ''::uuid
                assert _orgs_seen(cur, None) == set()
                # explicit bypass (workers, admin, anonymous): everything, as before
                assert _orgs_seen(cur, None, bypass='on') == {org.id, org2.id}
            finally:
                cur.execute('RESET ROLE')

    def test_runtime_role_cannot_write_into_another_org(self, runtime, two_orgs_rows, org, org2):
        with connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {runtime}')
            try:
                _orgs_seen(cur, org.id)
                cur.execute(f'UPDATE {T} SET name = %s', ['x'])  # noqa: S608
                assert cur.rowcount == 1
                with pytest.raises(DatabaseError), transaction.atomic():
                    cur.execute(
                        f'INSERT INTO {T} (id, organization_id, name, url, secret, events, '  # noqa: S608
                        'is_active, created_at, updated_at) '
                        "VALUES (%s, %s, 'evil', 'https://e/', 'x', '[]', true, now(), now())",
                        [str(uuid.uuid4()), str(org2.id)],
                    )
            finally:
                cur.execute('RESET ROLE')

    @pytest.mark.parametrize(
        'stmt',
        [
            f'ALTER TABLE {T} DISABLE ROW LEVEL SECURITY',
            f'ALTER TABLE {T} NO FORCE ROW LEVEL SECURITY',
            f'DROP POLICY rls_bypass ON {T}',
            'CREATE TABLE p1_probe_tbl (id int)',
        ],
    )
    def test_runtime_role_cannot_change_schema_or_policies(self, runtime, stmt):
        with connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {runtime}')
            try:
                with pytest.raises(DatabaseError), transaction.atomic():
                    cur.execute(stmt)
            finally:
                cur.execute('RESET ROLE')

    def test_row_security_off_is_refused(self, runtime, two_orgs_rows, org):
        with connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {runtime}')
            try:
                cur.execute("SELECT set_config('app.current_org_id', %s, true)", [str(org.id)])
                cur.execute("SELECT set_config('app.rls_bypass', 'off', true)")
                cur.execute('SET LOCAL row_security = off')
                with pytest.raises(DatabaseError), transaction.atomic():
                    cur.execute(f'SELECT count(*) FROM {T}')  # noqa: S608
            finally:
                cur.execute('RESET ROLE')

    def test_post_migrate_grants_runtime_role_from_settings(self, db):
        from core import db_roles

        with connection.cursor() as cur:
            role = _role(cur)
        with override_settings(DB_RUNTIME_ROLE=role):
            db_roles.post_migrate_grants(sender=None, using='default')
        with connection.cursor() as cur:
            cur.execute("SELECT has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE')", [role, T])
            assert cur.fetchone()[0] is True
            cur.execute("SELECT has_table_privilege(%s, %s, 'TRUNCATE')", [role, T])
            assert cur.fetchone()[0] is False


@pytest.mark.django_db
class TestBypassFlag:
    def test_new_connections_start_with_bypass_on(self, db):
        from core.db_roles import on_connection_created

        on_connection_created(sender=None, connection=connection)
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.rls_bypass', true)")
            assert cur.fetchone()[0] == 'on'

    def test_bypass_never_applies_once_an_org_is_set(self, two_orgs_rows, org):
        """RLSMiddleware sets the org for tenant requests; the flag cannot widen that."""
        with connection.cursor() as cur:
            role = _role(cur, owner_of=T)
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                assert _orgs_seen(cur, org.id, bypass='on') == {org.id}
            finally:
                cur.execute('RESET ROLE')

    @override_settings(RLS_BYPASS_WITHOUT_ORG=False)
    def test_fail_closed_mode_turns_the_default_off(self, db):
        from core.db_roles import on_connection_created

        on_connection_created(sender=None, connection=connection)
        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.rls_bypass', true)")
            assert cur.fetchone()[0] == 'off'
