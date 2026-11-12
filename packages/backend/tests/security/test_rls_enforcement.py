"""B15 stage 2 — RLS is enforced for the runtime role, against real Postgres.

The test connection is a superuser (always bypasses RLS), so each check switches to a
plain role created like the production runtime role (core.db_roles.grant_runtime_privileges:
no ownership, DML only, NOBYPASSRLS) with SET LOCAL ROLE inside the test transaction.

* no org and no explicit bypass: another org's rows are invisible (fail closed);
* another org's context: only that org's rows;
* content shared on purpose (public mock exams, public question banks) stays readable,
  private questions of another org do not;
* the explicit bypass is what the application sends inside unscoped_context();
* question search still finds visible questions through the trigram index.
"""

import uuid
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.catalog.models import Question, QuestionBank, QuestionVersion
from apps.exams.models import MockExam, MockExamQuestion
from apps.webhooks.models import WebhookEndpoint
from core.tenant import tenant_context, unscoped_context


@pytest.fixture
def runtime_role(db):
    from core import db_roles

    name = f'rt_probe_{uuid.uuid4().hex[:8]}'
    with connection.cursor() as cur:
        cur.execute(f'CREATE ROLE {name} NOLOGIN NOBYPASSRLS')
        db_roles.grant_runtime_privileges(cur, name)
    return name


class as_role:
    """Run the block's statements as `role` (Django cursor, so core.db_rls still applies)."""

    def __init__(self, role):
        self.role = role

    def __enter__(self):
        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        with connection.cursor() as cur:
            cur.execute(f'SET LOCAL ROLE {self.role}')
        return self

    def __exit__(self, *exc):
        with connection.cursor() as cur:
            cur.execute('RESET ROLE')
        return self.atomic.__exit__(*exc)


def _scalar(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _orgs_in(table):
    with connection.cursor() as cur:
        cur.execute(f'SELECT DISTINCT organization_id FROM {table}')  # noqa: S608
        return {r[0] for r in cur.fetchall()}


@pytest.fixture
def endpoints(org, org2):
    for o in (org, org2):
        with tenant_context(o):
            WebhookEndpoint.objects.create(organization=o, name=o.slug, url='https://e.x/h')


def _question(org, subject, text, *, public_bank=False):
    with tenant_context(org):
        q = Question.objects.create(organization=org, subject=subject, type='SC')
        qv = QuestionVersion.objects.create(
            question=q, version_number=1, content={'text': text}, options=[]
        )
        if public_bank:
            bank = QuestionBank.objects.create(
                organization=org, name='pub', slug=f'pub-{uuid.uuid4().hex[:8]}', is_public=True
            )
            bank.questions.add(q)
    return q, qv


def _public_mock(org, qv, creator):
    now = timezone.now()
    with tenant_context(org):
        exam = MockExam.objects.create(
            organization=org,
            title='public',
            duration_minutes=60,
            is_public=True,
            scheduled_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=1),
            status=MockExam.Status.PUBLISHED,
            created_by=creator,
        )
        MockExamQuestion.objects.create(mock_exam=exam, question_version=qv, order=1)
    return exam


@pytest.mark.django_db
class TestTenantRows:
    def test_no_org_context_sees_no_tenant_rows(self, runtime_role, endpoints):
        with as_role(runtime_role):
            assert _orgs_in('webhooks_webhookendpoint') == set()

    def test_other_org_context_sees_only_that_org(self, runtime_role, endpoints, org, org2):
        for o in (org, org2):
            with as_role(runtime_role), tenant_context(o):
                assert _orgs_in('webhooks_webhookendpoint') == {o.id}

    def test_unscoped_context_is_the_explicit_bypass(self, runtime_role, endpoints, org, org2):
        with as_role(runtime_role), unscoped_context():
            assert _orgs_in('webhooks_webhookendpoint') == {org.id, org2.id}

    def test_raw_flag_cannot_be_a_leftover_default(self, runtime_role, endpoints):
        """An unset or unknown flag value never opens the table."""
        with as_role(runtime_role):
            for value in ('', 'yes', 'ALL', 'on '):
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('app.rls_bypass', %s, true)", [value])
                assert _orgs_in('webhooks_webhookendpoint') == set(), value


@pytest.mark.django_db
class TestSharedContent:
    def test_public_mock_exam_questions_are_readable_by_another_org(
        self, runtime_role, org, org2, subject, superuser
    ):
        q, qv = _question(org, subject, 'shared mock question')
        exam = _public_mock(org, qv, superuser)
        with as_role(runtime_role), tenant_context(org2):
            assert _scalar('SELECT count(*) FROM exams_mockexam WHERE id = %s', [exam.id]) == 1
            assert (
                _scalar(
                    'SELECT count(*) FROM exams_mockexamquestion WHERE mock_exam_id = %s', [exam.id]
                )
                == 1
            )
            assert (
                _scalar('SELECT count(*) FROM catalog_questionversion WHERE id = %s', [qv.id]) == 1
            )
            assert _scalar('SELECT count(*) FROM catalog_question WHERE id = %s', [q.id]) == 1
            links = list(
                MockExamQuestion.objects.filter(mock_exam_id=exam.id).select_related(
                    'question_version__question'
                )
            )
            assert [lk.question_version.question_id for lk in links] == [q.id]

    def test_public_bank_questions_are_readable_by_another_org(
        self, runtime_role, org, org2, subject
    ):
        q, qv = _question(org, subject, 'shared bank question', public_bank=True)
        with as_role(runtime_role), tenant_context(org2):
            assert _scalar('SELECT count(*) FROM catalog_question WHERE id = %s', [q.id]) == 1
            assert (
                _scalar('SELECT count(*) FROM catalog_questionversion WHERE id = %s', [qv.id]) == 1
            )

    def test_private_questions_stay_private(self, runtime_role, org, org2, subject):
        q, qv = _question(org, subject, 'private question')
        for ctx in (tenant_context(org2), _no_org()):
            with as_role(runtime_role), ctx:
                assert _scalar('SELECT count(*) FROM catalog_question WHERE id = %s', [q.id]) == 0
                assert (
                    _scalar('SELECT count(*) FROM catalog_questionversion WHERE id = %s', [qv.id])
                    == 0
                )

    def test_shared_rows_are_read_only_for_other_orgs(self, runtime_role, org, org2, subject):
        q, qv = _question(org, subject, 'shared bank question', public_bank=True)
        with as_role(runtime_role), tenant_context(org2):
            with connection.cursor() as cur:
                cur.execute('UPDATE catalog_question SET is_active = false WHERE id = %s', [q.id])
                assert cur.rowcount == 0
                cur.execute('DELETE FROM catalog_questionversion WHERE id = %s', [qv.id])
                assert cur.rowcount == 0


class _no_org:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.django_db
class TestSearch:
    def test_search_finds_own_and_shared_but_not_private_of_others(
        self, runtime_role, org, org2, subject, superuser
    ):
        from apps.engagement.search_service import search_questions

        _, mine = _question(org2, subject, 'Parabola vertex (mine)')
        _, shared = _question(org, subject, 'Parabola focus (public bank)', public_bank=True)
        _, private = _question(org, subject, 'Parabola directrix (private)')
        with as_role(runtime_role), tenant_context(org2):
            found = {r['version_id'] for r in search_questions('parabola')}
        assert found == {str(mine.id), str(shared.id)}

    def test_search_without_org_finds_only_shared(self, runtime_role, org, subject):
        from apps.engagement.search_service import search_questions

        _, shared = _question(org, subject, 'Hyperbola (public bank)', public_bank=True)
        _question(org, subject, 'Hyperbola (private)')
        with as_role(runtime_role):
            found = {r['version_id'] for r in search_questions('hyperbola')}
        assert found == {str(shared.id)}

    def test_search_function_is_security_definer_owned_by_a_bypass_role(self, db):
        row = _owner_of('app_search_question_versions')
        assert row is not None, 'search function missing'
        prosecdef, rolbypassrls, rolcanlogin = row
        assert prosecdef and rolbypassrls and not rolcanlogin

    def test_search_function_query_uses_the_trigram_index(self, db):
        """The function's owner bypasses RLS, so the planner may use the index (E01)."""
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                'SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname = '
                "'app_search_question_versions'"
            )
            owner = cur.fetchone()[0]
            cur.execute(f'SET LOCAL ROLE {owner}')
            cur.execute('SET LOCAL enable_seqscan = off')
            cur.execute(
                'EXPLAIN SELECT qv.id FROM catalog_questionversion qv '
                "WHERE upper(qv.content::text) LIKE upper('%%parabola%%')"
            )
            plan = '\n'.join(r[0] for r in cur.fetchall())
            cur.execute('RESET ROLE')
        assert 'catalog_qv_content_trgm' in plan, plan


def _owner_of(func):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT p.prosecdef, r.rolbypassrls, r.rolcanlogin FROM pg_proc p '
            'JOIN pg_roles r ON r.oid = p.proowner WHERE p.proname = %s',
            [func],
        )
        return cur.fetchone()


@pytest.mark.django_db
class TestStatementPrefix:
    def test_failed_savepoint_inside_a_tenant_context_rolls_back_cleanly(self, org):
        """ROLLBACK TO SAVEPOINT must not carry the RLS prefix: the transaction is aborted
        at that point and the prefix would fail it, breaking the outer transaction too."""
        from django.db.utils import DatabaseError

        with tenant_context(org):
            with pytest.raises(DatabaseError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute('SELECT 1 / 0')
            assert _scalar('SELECT 1') == 1

    def test_unscoped_bypass_does_not_outlive_its_block(self, runtime_role, endpoints, org):
        with as_role(runtime_role):
            with unscoped_context():
                assert len(_orgs_in('webhooks_webhookendpoint')) == 2
            assert _orgs_in('webhooks_webhookendpoint') == set()
            with tenant_context(org):
                assert _orgs_in('webhooks_webhookendpoint') == {org.id}
            assert _orgs_in('webhooks_webhookendpoint') == set()


@pytest.mark.django_db
class TestWorkersUnderRls:
    """Code without a request context must still reach the rows it was handed."""

    def test_generate_export_task_finds_and_writes_its_report(
        self, runtime_role, org, user, tmp_path, settings
    ):
        from apps.analytics.models import ReportExport
        from apps.analytics.tasks import generate_export

        settings.MEDIA_ROOT = tmp_path
        with tenant_context(org):
            report = ReportExport.objects.create(
                organization=org, user=user, report_type='students_full'
            )
        with as_role(runtime_role):
            result = generate_export(str(report.pk))
        assert result['status'] == 'ready', result

    def test_rotate_command_reaches_every_tenant(self, runtime_role, org, org2, settings):
        from cryptography.fernet import Fernet
        from django.core.management import call_command

        from core import crypto

        old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        settings.FIELD_ENCRYPTION_KEYS = [old]
        for o in (org, org2):
            with tenant_context(o):
                WebhookEndpoint.objects.create(organization=o, name=o.slug, url='https://e.x/h')
        settings.FIELD_ENCRYPTION_KEYS = [new, old]
        with as_role(runtime_role):
            call_command('rotate_field_encryption')
        settings.FIELD_ENCRYPTION_KEYS = [new]
        with unscoped_context(), connection.cursor() as cur:
            cur.execute('SELECT secret FROM webhooks_webhookendpoint')
            secrets = [r[0] for r in cur.fetchall()]
        assert len(secrets) == 2
        for value in secrets:
            crypto.decrypt(value)  # raises if still under the retired key

    def test_for_org_is_an_explicit_scope(self, runtime_role, endpoints, org, org2):
        with as_role(runtime_role), tenant_context(org):
            assert WebhookEndpoint.objects.for_org(org2).count() == 1
            assert WebhookEndpoint.objects.count() == 1  # still org only
