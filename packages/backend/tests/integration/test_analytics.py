"""
Task 8 — B2B Analytics integration tests.

Tekshiriladi:
  - record_exam_event: ExamAttempt SUBMITTED → ExamEvent yoziladi
  - Idempotent: ikki marta chaqirish dublikat yaratmaydi
  - Aggregation services: subject avg, weekly growth, distribution, weak students
  - REST API: dashboard, charts, weak students
  - Permission: faqat org admin/teacher dashboard ko'ra oladi
  - Reports: CSV/Excel export Celery task (eager)
"""

import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics import services
from apps.analytics.models import ExamEvent, ReportExport
from apps.analytics.tasks import generate_export
from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import ExamAttempt, MockExam, MockExamQuestion
from core.tenant import tenant_context, unscoped_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_qv(org, subject):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[{'id': 1, 'text': 'A', 'is_correct': True}],
        )


def _submit_attempt(org, user, score, subject, *, when=None):
    """Helper: SUBMITTED attempt yaratish + score → signal triggers ExamEvent."""
    when = when or timezone.now()
    qv = _make_qv(org, subject)
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title='M',
            duration_minutes=60,
            scheduled_at=when - timedelta(minutes=5),
            closes_at=when + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=False,
            created_by=user,
        )
        MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)
        attempt = ExamAttempt.objects.create(
            organization=org,
            exam=mock,
            user=user,
            status=ExamAttempt.Status.SUBMITTED,
            score=Decimal(str(score)),
            correct_count=int(score / 10),
            submitted_at=when,
        )
    return attempt


@pytest.fixture
def admin_member(db, user, org):
    """User'ni org'ga 'admin' role bilan member qilish (dashboard ko'ra oladi)."""
    from apps.organizations.models import Membership, MembershipStatus, OrgRole

    role = OrgRole.objects.get(organization=org, name='owner')
    m = Membership.objects.create(
        user=user,
        organization=org,
        role=role,
        status=MembershipStatus.ACTIVE,
        is_primary=True,
    )
    m.activate()
    return m


# ─── Event recording (signal flow) ───────────────────────────────────────────


@pytest.mark.integration
class TestEventRecording:
    def test_submitted_attempt_creates_event(self, db, org, user, member, subject):
        attempt = _submit_attempt(org, user, 75.0, subject)
        with unscoped_context():
            events = ExamEvent.objects.filter(exam_attempt_id=attempt.id)
            assert events.count() == 1
            ev = events.first()
            assert float(ev.score) == 75.0
            assert ev.subject_name == subject.name

    def test_record_event_idempotent(self, db, org, user, member, subject):
        attempt = _submit_attempt(org, user, 80, subject)
        # Manually call again
        services.record_exam_event(attempt)
        with unscoped_context():
            assert ExamEvent.objects.filter(exam_attempt_id=attempt.id).count() == 1

    def test_event_skipped_when_score_none(self, db, org, user, member, subject):
        # Manually create attempt with score=None — signal skips
        with tenant_context(org):
            qv = _make_qv(org, subject)
            mock = MockExam.objects.create(
                organization=org,
                title='M',
                duration_minutes=60,
                scheduled_at=timezone.now() - timedelta(minutes=5),
                closes_at=timezone.now() + timedelta(hours=2),
                status=MockExam.Status.PUBLISHED,
                created_by=user,
            )
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)
            ExamAttempt.objects.create(
                organization=org,
                exam=mock,
                user=user,
                status=ExamAttempt.Status.IN_PROGRESS,
                score=None,
            )
        with unscoped_context():
            assert ExamEvent.objects.count() == 0


# ─── Aggregation services ────────────────────────────────────────────────────


@pytest.mark.integration
class TestAggregations:
    def test_subject_averages(self, db, org, user, member, subject):
        _submit_attempt(org, user, 60, subject)
        _submit_attempt(org, user, 90, subject)
        result = services.get_subject_averages(org, days=30)
        assert len(result) >= 1
        sub_data = next((r for r in result if r['subject_name'] == subject.name), None)
        assert sub_data is not None
        assert sub_data['avg_score'] == 75.0
        assert sub_data['event_count'] == 2

    def test_weekly_growth(self, db, org, user, member, subject):
        _submit_attempt(org, user, 70, subject)
        result = services.get_weekly_growth(org, weeks=4)
        assert len(result) >= 1
        assert result[-1]['avg_score'] == 70.0

    def test_score_distribution(self, db, org, user, user2, member, subject):
        from apps.organizations.models import Membership, MembershipStatus, OrgRole

        # user2'ni ham member qilish
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user2,
            organization=org,
            role=role,
            status=MembershipStatus.ACTIVE,
            is_primary=True,
        )
        m.activate()

        _submit_attempt(org, user, 20, subject)  # 0-25
        _submit_attempt(org, user2, 80, subject)  # 76-100
        result = services.get_score_distribution(org, days=30)
        buckets = {b['bucket']: b['count'] for b in result}
        assert buckets['0-25'] == 1
        assert buckets['76-100'] == 1
        assert buckets['26-50'] == 0

    def test_weak_students_filters_min_attempts(self, db, org, user, user2, member, subject):
        from apps.organizations.models import Membership, MembershipStatus, OrgRole

        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user2,
            organization=org,
            role=role,
            status=MembershipStatus.ACTIVE,
            is_primary=True,
        )
        m.activate()

        # User: 1 ta past score (min_attempts=2 dan past) — list'ga kirmaydi
        _submit_attempt(org, user, 30, subject)
        # User2: 2 ta attempt — kiradi
        _submit_attempt(org, user2, 30, subject)
        _submit_attempt(org, user2, 40, subject)

        result = services.get_weak_students(org, limit=10)
        user_ids = {r['user_id'] for r in result}
        assert str(user2.id) in user_ids
        assert str(user.id) not in user_ids  # min_attempts=2 filter

    def test_dashboard_summary_structure(self, db, org, user, member, subject):
        _submit_attempt(org, user, 60, subject)
        result = services.get_dashboard_summary(org, days=30)
        assert 'subject_averages' in result
        assert 'weekly_growth' in result
        assert 'score_distribution' in result
        assert 'weak_students' in result
        assert result['total_events'] >= 1


# ─── REST API ────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestDashboardAPI:
    def test_dashboard_endpoint(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 75, subject)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:dashboard'))
        assert resp.status_code == 200
        data = resp.json()
        assert data['total_events'] >= 1
        assert 'subject_averages' in data

    def test_subjects_endpoint(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 80, subject)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:subjects'))
        assert resp.status_code == 200
        assert 'subjects' in resp.json()

    def test_weekly_endpoint(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 80, subject)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:weekly'), {'weeks': 4})
        assert resp.status_code == 200

    def test_distribution_endpoint(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 80, subject)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:distribution'))
        assert resp.status_code == 200

    def test_weak_students_endpoint(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 30, subject)
        _submit_attempt(org, user, 40, subject)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:weak-students'))
        assert resp.status_code == 200

    def test_student_role_forbidden(self, db, user, member, org):
        # `member` fixture default 'student' role beradi
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:dashboard'))
        assert resp.status_code == 403

    def test_invalid_days_param_400(self, db, user, admin_member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('analytics:dashboard'), {'days': 999})
        assert resp.status_code == 400


# ─── Reports export ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestReportsExport:
    def test_csv_export_via_celery(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 75, subject)

        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('analytics:export-create'),
            data={'report_type': 'students_full', 'fmt': 'csv'},
            content_type='application/json',
        )
        assert resp.status_code == 202
        body = resp.json()
        # Celery eager → already ready
        assert body['status'] == 'ready'
        assert body['file_url'] is not None

        # Read the file content (csv format)
        with unscoped_context():
            report = ReportExport.objects.get(pk=body['id'])
        with report.file.open('rb') as fh:
            content = fh.read().decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert rows[0][0] == 'user_email'  # header
        assert len(rows) >= 2  # header + 1 data row

    def test_excel_export(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 80, subject)
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('analytics:export-create'),
            data={'report_type': 'students_full', 'fmt': 'excel'},
            content_type='application/json',
        )
        assert resp.status_code == 202
        assert resp.json()['status'] == 'ready'

    def test_export_detail(self, db, org, user, admin_member, subject):
        _submit_attempt(org, user, 80, subject)
        client = Client()
        client.force_login(user)
        create_resp = client.post(
            reverse('analytics:export-create'),
            data={'report_type': 'students_full', 'fmt': 'csv'},
            content_type='application/json',
        )
        rid = create_resp.json()['id']
        resp = client.get(reverse('analytics:export-detail', args=[rid]))
        assert resp.status_code == 200
        assert resp.json()['status'] == 'ready'

    def test_export_invalid_fmt_400(self, db, user, admin_member, org):
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('analytics:export-create'),
            data={'fmt': 'pdf'},
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_generate_export_handles_missing_id(self, db):
        import uuid

        result = generate_export(str(uuid.uuid4()))
        assert result['status'] == 'not_found'
