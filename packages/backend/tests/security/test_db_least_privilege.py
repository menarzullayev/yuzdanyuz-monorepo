"""B15 — the runtime database role cannot bypass row-level security.

The test connection is a superuser (which always bypasses RLS), so every check switches
to a plain role with SET LOCAL ROLE inside the test transaction (rolled back afterwards).

* every RLS table has FORCE ROW LEVEL SECURITY, so even the table owner is filtered;
* the runtime role created by core.db_roles owns nothing and only gets DML grants;
* policies never raise when no organisation is set; the `app.rls_bypass` flag (sent per
  statement by core.db_rls when RLS_ENFORCE_ORG_SESSIONS is on, unset = 'all') decides who
  passes: 'all', 'on' (org sessions filtered) or 'off'.
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
        'SELECT c.relname, c.relforcerowsecurity FROM pg_class c '
        'JOIN pg_namespace n ON n.oid = c.relnamespace '
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
        cur.execute('SET CONSTRAINTS ALL IMMEDIATE')  # ALTER TABLE refuses pending FK checks
        cur.execute(f'GRANT CREATE ON SCHEMA public TO {name}')  # a new owner needs it
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
            cur.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE')", [role, T]
            )
            assert cur.fetchone()[0] is True
            cur.execute("SELECT has_table_privilege(%s, %s, 'TRUNCATE')", [role, T])
            assert cur.fetchone()[0] is False


def _flag_seen_by_a_statement():
    """The flag as the statement itself sees it (core/db_rls.py prefixes statements)."""
    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.rls_bypass', true)")
        return cur.fetchone()[0]


@pytest.mark.django_db
class TestBypassFlag:
    def test_default_sends_no_flag(self, db):
        """Org-session enforcement is opt-in (RLS_ENFORCE_ORG_SESSIONS): see the next class."""
        assert _flag_seen_by_a_statement() in (None, '')

    @override_settings(RLS_ENFORCE_ORG_SESSIONS=True)
    def test_enforcement_mode_puts_the_flag_on_every_statement(self, db):
        assert _flag_seen_by_a_statement() == 'on'

    @override_settings(RLS_ENFORCE_ORG_SESSIONS=True, RLS_BYPASS_WITHOUT_ORG=False)
    def test_fail_closed_mode(self, db):
        assert _flag_seen_by_a_statement() == 'off'

    def test_unset_flag_counts_as_all(self, two_orgs_rows, org, org2):
        with connection.cursor() as cur:
            role = _role(cur, owner_of=T)
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                cur.execute("SELECT set_config('app.rls_bypass', '', true)")
                cur.execute("SELECT set_config('app.current_org_id', %s, true)", [str(org.id)])
                cur.execute(f'SELECT DISTINCT organization_id FROM {T}')  # noqa: S608
                assert {r[0] for r in cur.fetchall()} == {org.id, org2.id}
            finally:
                cur.execute('RESET ROLE')

    def test_all_passes_an_org_session_and_on_does_not(self, two_orgs_rows, org, org2):
        with connection.cursor() as cur:
            role = _role(cur, owner_of=T)
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                assert _orgs_seen(cur, org.id, bypass='all') == {org.id, org2.id}
                assert _orgs_seen(cur, org.id, bypass='on') == {org.id}
            finally:
                cur.execute('RESET ROLE')


@pytest.mark.django_db
class TestEnforcementBlocker:
    """Why RLS_ENFORCE_ORG_SESSIONS is off: under enforcement another org's public mock
    exam questions are invisible. When the policies learn about shared content this test
    fails; then flip the default and update docs/deployment/database-roles.md."""

    def test_public_mock_questions_of_another_org_are_hidden(self, org, org2, subject, superuser):
        from datetime import timedelta

        from django.utils import timezone

        from apps.catalog.models import Question, QuestionVersion
        from apps.exams.models import MockExam, MockExamQuestion

        now = timezone.now()
        with tenant_context(org):
            q = Question.objects.create(organization=org, subject=subject, type='SC')
            qv = QuestionVersion.objects.create(
                question=q, version_number=1, content={'text': 'q'}, options=[]
            )
            exam = MockExam.objects.create(
                organization=org,
                title='public',
                duration_minutes=60,
                is_public=True,
                scheduled_at=now - timedelta(hours=1),
                closes_at=now + timedelta(hours=1),
                status=MockExam.Status.PUBLISHED,
                created_by=superuser,
            )
            MockExamQuestion.objects.create(mock_exam=exam, question_version=qv, order=1)
        with connection.cursor() as cur:
            role = _role(cur)
            cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}')
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                cur.execute("SELECT set_config('app.rls_bypass', 'on', true)")
                cur.execute("SELECT set_config('app.current_org_id', %s, true)", [str(org2.id)])
                cur.execute('SELECT count(*) FROM exams_mockexam WHERE id = %s', [str(exam.id)])
                assert cur.fetchone()[0] == 1  # the public exam itself is visible
                cur.execute(
                    'SELECT count(*) FROM catalog_questionversion WHERE id = %s', [str(qv.id)]
                )
                assert cur.fetchone()[0] == 0  # ...its questions are not
            finally:
                cur.execute('RESET ROLE')
