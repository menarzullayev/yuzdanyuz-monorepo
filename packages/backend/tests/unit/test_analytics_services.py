"""
Unit tests for `apps.analytics.services` — aggregation + event recording.

Strategy: directly seed `ExamEvent` rows (bypass the ExamAttempt signal flow,
which is covered in `tests/integration/test_analytics.py`). This lets us
exercise aggregation logic, bucket boundaries, day-window cutoffs and
multi-tenant filtering with minimal setup.

`record_exam_event` itself is tested via a lightweight stub attempt object —
no actual `ExamAttempt`/`MockExamQuestion` machinery required.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.analytics import services
from apps.analytics.models import ExamEvent

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_event(
    org,
    user,
    *,
    score,
    when=None,
    subject_id=None,
    subject_name='Math',
    correct=5,
    total=10,
):
    """Lightweight ExamEvent factory — by-passes signal flow."""
    return ExamEvent.objects.create(
        organization=org,
        user=user,
        exam_attempt_id=__import__('uuid').uuid4(),
        mock_exam_id=__import__('uuid').uuid4(),
        subject_id=subject_id,
        subject_name=subject_name,
        score=Decimal(str(score)),
        correct_count=correct,
        total_count=total,
        duration_seconds=300,
        completed_at=when or timezone.now(),
    )


# ─── get_subject_averages ────────────────────────────────────────────────────


@pytest.mark.unit
class TestSubjectAverages:
    def test_empty_org_returns_empty(self, db, org):
        assert services.get_subject_averages(org) == []

    def test_averages_grouped_by_subject(self, db, org, user, subject):
        _make_event(org, user, score=60, subject_id=subject.id, subject_name='Math')
        _make_event(org, user, score=90, subject_id=subject.id, subject_name='Math')
        _make_event(org, user, score=40, subject_id=None, subject_name='')

        result = services.get_subject_averages(org, days=30)
        # Ikkita subject bucket: Math + Aniqlanmagan (NULL subject)
        by_name = {r['subject_name']: r for r in result}
        assert by_name['Math']['avg_score'] == 75.0
        assert by_name['Math']['event_count'] == 2
        # NULL subject 'Aniqlanmagan' fallback bilan ko'rinadi
        assert by_name['Aniqlanmagan']['avg_score'] == 40.0

    def test_excludes_events_outside_window(self, db, org, user, subject):
        old = timezone.now() - timedelta(days=60)
        _make_event(org, user, score=50, when=old, subject_id=subject.id)
        result = services.get_subject_averages(org, days=30)
        assert result == []

    def test_tenant_isolation(self, db, org, org2, user, subject):
        _make_event(org, user, score=80, subject_id=subject.id)
        _make_event(org2, user, score=10, subject_id=subject.id)
        # org so'rovi org2 event'larini ko'rmasligi kerak
        result = services.get_subject_averages(org)
        assert all(r['avg_score'] == 80.0 for r in result)


# ─── get_weekly_growth ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestWeeklyGrowth:
    def test_empty_returns_empty(self, db, org):
        assert services.get_weekly_growth(org) == []

    def test_groups_by_week(self, db, org, user):
        now = timezone.now()
        _make_event(org, user, score=70, when=now)
        _make_event(org, user, score=90, when=now)
        # 2 hafta oldin (boshqa hafta bucket)
        _make_event(org, user, score=30, when=now - timedelta(days=14))

        result = services.get_weekly_growth(org, weeks=8)
        # Kamida 2 ta haftalik bucket bo'lishi kerak
        assert len(result) >= 2
        # ISO format string week
        for row in result:
            assert isinstance(row['week'], str)
            assert len(row['week']) == 10  # YYYY-MM-DD

    def test_window_cutoff(self, db, org, user):
        old = timezone.now() - timedelta(weeks=20)
        _make_event(org, user, score=50, when=old)
        # weeks=4 oxirida cutoff
        result = services.get_weekly_growth(org, weeks=4)
        assert result == []


# ─── get_score_distribution ──────────────────────────────────────────────────


@pytest.mark.unit
class TestScoreDistribution:
    def test_empty_returns_all_buckets_zero(self, db, org):
        result = services.get_score_distribution(org, days=30)
        buckets = {b['bucket']: b['count'] for b in result}
        assert buckets == {'0-25': 0, '26-50': 0, '51-75': 0, '76-100': 0}

    def test_bucket_boundaries(self, db, org, user):
        # Boundary score'lar: 25 → 0-25, 26 → 26-50, 50 → 26-50, 75 → 51-75, 76 → 76-100
        _make_event(org, user, score=25)
        _make_event(org, user, score=26)
        _make_event(org, user, score=50)
        _make_event(org, user, score=75)
        _make_event(org, user, score=76)
        _make_event(org, user, score=100)

        result = services.get_score_distribution(org, days=30)
        buckets = {b['bucket']: b['count'] for b in result}
        assert buckets['0-25'] == 1  # 25
        assert buckets['26-50'] == 2  # 26, 50
        assert buckets['51-75'] == 1  # 75
        assert buckets['76-100'] == 2  # 76, 100

    def test_bucket_keys_in_order(self, db, org):
        result = services.get_score_distribution(org)
        assert [b['bucket'] for b in result] == ['0-25', '26-50', '51-75', '76-100']

    def test_tenant_isolation(self, db, org, org2, user):
        _make_event(org, user, score=10)
        _make_event(org2, user, score=90)
        buckets = {b['bucket']: b['count'] for b in services.get_score_distribution(org)}
        assert buckets['0-25'] == 1
        assert buckets['76-100'] == 0


# ─── get_weak_students ───────────────────────────────────────────────────────


@pytest.mark.unit
class TestWeakStudents:
    def test_requires_min_two_events(self, db, org, user, user2):
        # user: 1 ta past score (filter chetlatadi)
        _make_event(org, user, score=20)
        # user2: 2 ta past score (kiradi)
        _make_event(org, user2, score=30)
        _make_event(org, user2, score=40)

        result = services.get_weak_students(org, limit=10)
        ids = {r['user_id'] for r in result}
        assert str(user2.id) in ids
        assert str(user.id) not in ids

    def test_sorted_ascending_by_avg(self, db, org, user, user2):
        # user: avg 35
        _make_event(org, user, score=30)
        _make_event(org, user, score=40)
        # user2: avg 25 (zaifroq → birinchi keladi)
        _make_event(org, user2, score=20)
        _make_event(org, user2, score=30)

        result = services.get_weak_students(org, limit=10)
        avgs = [r['avg_score'] for r in result]
        assert avgs == sorted(avgs)

    def test_returns_username_and_email(self, db, org, user):
        _make_event(org, user, score=20)
        _make_event(org, user, score=30)
        result = services.get_weak_students(org)
        assert len(result) == 1
        assert result[0]['username'] == user.username
        assert result[0]['email'] == user.email


# ─── get_dashboard_summary ───────────────────────────────────────────────────


@pytest.mark.unit
class TestDashboardSummary:
    def test_structure_contains_all_widgets(self, db, org, user):
        _make_event(org, user, score=50)
        summary = services.get_dashboard_summary(org, days=30)
        assert summary['period_days'] == 30
        assert summary['total_events'] == 1
        for key in (
            'subject_averages',
            'weekly_growth',
            'score_distribution',
            'weak_students',
        ):
            assert key in summary

    def test_total_events_respects_window(self, db, org, user):
        _make_event(org, user, score=50)
        _make_event(org, user, score=60, when=timezone.now() - timedelta(days=90))
        # days=30 → eski event hisobga olinmaydi
        summary = services.get_dashboard_summary(org, days=30)
        assert summary['total_events'] == 1


# ─── record_exam_event ───────────────────────────────────────────────────────


def _stub_attempt(org, user, *, score, subject=None, with_links=True):
    """
    Light-weight attempt-shaped object. `record_exam_event` faqat bir nechta
    attribute o'qiydi (score, id, organization, user, exam.question_links, ...).
    Bizga butun ExamAttempt+MockExam zanjiri kerak emas.
    """
    import uuid as _uuid

    now = timezone.now()
    first_link = None
    question_links = MagicMock()
    if with_links and subject is not None:
        first_link = SimpleNamespace(
            question_version=SimpleNamespace(question=SimpleNamespace(subject=subject))
        )
    question_links.select_related.return_value.first.return_value = first_link
    question_links.count.return_value = 10

    exam = SimpleNamespace(question_links=question_links)
    return SimpleNamespace(
        id=_uuid.uuid4(),
        exam=exam,
        exam_id=_uuid.uuid4(),
        organization=org,
        organization_id=org.id,
        user=user,
        user_id=user.id,
        score=Decimal(str(score)) if score is not None else None,
        correct_count=int(score / 10) if score is not None else 0,
        started_at=now - timedelta(minutes=20),
        submitted_at=now,
    )


@pytest.mark.unit
class TestRecordExamEvent:
    def test_returns_none_when_score_missing(self, db, org, user):
        attempt = _stub_attempt(org, user, score=None)
        assert services.record_exam_event(attempt) is None
        assert ExamEvent.global_objects.count() == 0

    def test_creates_event_with_subject_meta(self, db, org, user, subject):
        attempt = _stub_attempt(org, user, score=85, subject=subject)
        event = services.record_exam_event(attempt)
        assert event is not None
        assert event.subject_id == subject.id
        assert event.subject_name == subject.name
        assert float(event.score) == 85.0
        assert event.total_count == 10  # question_links.count() stub
        assert event.duration_seconds == 20 * 60  # submitted - started

    def test_idempotent_for_same_attempt(self, db, org, user, subject):
        attempt = _stub_attempt(org, user, score=70, subject=subject)
        first = services.record_exam_event(attempt)
        second = services.record_exam_event(attempt)
        assert first is not None
        assert second is None  # duplicate skip
        assert ExamEvent.global_objects.filter(exam_attempt_id=attempt.id).count() == 1

    def test_handles_attempt_without_subject(self, db, org, user):
        attempt = _stub_attempt(org, user, score=55, subject=None, with_links=False)
        event = services.record_exam_event(attempt)
        assert event is not None
        assert event.subject_id is None
        assert event.subject_name == ''
