"""
Task 4 — Exam Engine Celery task tests.

CELERY_TASK_ALWAYS_EAGER=True (test settings) bilan task'lar sync ishlaydi.

Tekshiriladi:
  - publish_scheduled_mocks: DRAFT → PUBLISHED, idempotent, no-questions skip
  - quarantine_check: ratio threshold, min disputes, idempotent, signal trigger
  - finalize_attempt_score: correct_count, total_points, score formula
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import (
    ExamAttempt,
    MockExam,
    MockExamQuestion,
    QuestionDispute,
    UserAnswer,
)
from apps.exams.tasks import (
    finalize_attempt_score,
    publish_scheduled_mocks,
    quarantine_check,
)
from core.tenant import tenant_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_question_version(org, subject, *, options=None):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=options
            or [
                {'id': 1, 'text': 'A', 'is_correct': True},
                {'id': 2, 'text': 'B', 'is_correct': False},
            ],
        )


def _make_mock(org, user, *, scheduled_at=None, status=MockExam.Status.DRAFT):
    now = timezone.now()
    return MockExam.objects.create(
        organization=org,
        title='Test Mock',
        duration_minutes=60,
        scheduled_at=scheduled_at or now - timedelta(minutes=1),
        closes_at=(scheduled_at or now) + timedelta(hours=2),
        status=status,
        is_public=False,
        created_by=user,
    )


# ─── publish_scheduled_mocks ──────────────────────────────────────────────────


@pytest.mark.unit
class TestPublishScheduledMocks:
    def test_draft_with_questions_gets_published(self, db, org, user, subject):
        with tenant_context(org):
            qv = _make_question_version(org, subject)
            mock = _make_mock(org, user)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)

        result = publish_scheduled_mocks()
        assert result['published'] == 1

        mock.refresh_from_db()
        assert mock.status == MockExam.Status.PUBLISHED

    def test_draft_without_questions_skipped(self, db, org, user):
        with tenant_context(org):
            mock = _make_mock(org, user)

        result = publish_scheduled_mocks()
        assert result['published'] == 0

        mock.refresh_from_db()
        assert mock.status == MockExam.Status.DRAFT

    def test_future_scheduled_not_published(self, db, org, user, subject):
        future = timezone.now() + timedelta(hours=1)
        with tenant_context(org):
            qv = _make_question_version(org, subject)
            mock = _make_mock(org, user, scheduled_at=future)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)

        result = publish_scheduled_mocks()
        assert result['published'] == 0

    def test_idempotent_second_call_does_nothing(self, db, org, user, subject):
        with tenant_context(org):
            qv = _make_question_version(org, subject)
            mock = _make_mock(org, user)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)

        publish_scheduled_mocks()
        result_second = publish_scheduled_mocks()
        assert result_second['published'] == 0


# ─── finalize_attempt_score ───────────────────────────────────────────────────


@pytest.mark.unit
class TestFinalizeAttemptScore:
    def test_score_calculation_basic(self, db, org, user, member, subject):
        with tenant_context(org):
            qv1 = _make_question_version(org, subject)
            qv2 = _make_question_version(org, subject)

            mock = _make_mock(org, user, status=MockExam.Status.PUBLISHED)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv1, order=1, points=2)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv2, order=2, points=3)

            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=qv1,
                selected={'id': 1},
                is_correct=True,
            )
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=qv2,
                selected={'id': 2},
                is_correct=False,
            )

        result = finalize_attempt_score(str(attempt.id))

        attempt.refresh_from_db()
        assert attempt.correct_count == 1
        assert attempt.total_points == 2  # qv1=2, qv2 incorrect
        assert attempt.score == 40  # 2 / (2+3) * 100
        assert result['status'] == 'finalized'

    def test_auto_correct_counts_as_correct(self, db, org, user, member, subject):
        """auto_correct=True (quarantine bonus) ham to'g'ri javob hisoblanadi."""
        with tenant_context(org):
            qv = _make_question_version(org, subject)
            mock = _make_mock(org, user, status=MockExam.Status.PUBLISHED)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1, points=5)

            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=qv,
                selected={'id': 2},
                is_correct=False,
                auto_correct=True,  # Quarantine bonus
            )

        finalize_attempt_score(str(attempt.id))

        attempt.refresh_from_db()
        assert attempt.correct_count == 1
        assert attempt.total_points == 5
        assert attempt.score == 100

    def test_no_questions_zero_score(self, db, org, user, member):
        with tenant_context(org):
            mock = _make_mock(org, user, status=MockExam.Status.PUBLISHED)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)

        result = finalize_attempt_score(str(attempt.id))
        attempt.refresh_from_db()
        assert attempt.score == 0
        assert result['status'] == 'finalized'

    def test_missing_attempt_returns_not_found(self, db):
        import uuid

        result = finalize_attempt_score(str(uuid.uuid4()))
        assert result['status'] == 'not_found'


# ─── quarantine_check ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestQuarantineCheck:
    def _setup_attempts_with_answers(self, org, user, subject, *, n_answers, n_disputes):
        """
        n_answers ta UserAnswer va n_disputes ta QuestionDispute yaratadi
        (har biri alohida user va alohida attempt orqali — unique constraints uchun).
        Returns: question_version
        """
        from django.contrib.auth import get_user_model

        from apps.organizations.models import Membership, MembershipStatus, OrgRole

        User = get_user_model()
        with tenant_context(org):
            qv = _make_question_version(org, subject)
            mock = _make_mock(org, user, status=MockExam.Status.PUBLISHED)
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)

            role = OrgRole.objects.get(organization=org, name='student')
            for i in range(max(n_answers, n_disputes)):
                u = User.objects.create_user(
                    username=f'qcheck_user_{i}',
                    email=f'qcheck{i}@example.com',
                    password='pass',
                )
                Membership.objects.create(
                    user=u,
                    organization=org,
                    role=role,
                    status=MembershipStatus.ACTIVE,
                    is_primary=True,
                ).activate()
                attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=u)
                if i < n_answers:
                    UserAnswer.objects.create(
                        organization=org,
                        attempt=attempt,
                        question_version=qv,
                        selected={'id': 2},
                        is_correct=False,
                    )
                if i < n_disputes:
                    QuestionDispute.objects.create(
                        organization=org,
                        attempt=attempt,
                        question_version=qv,
                        user=u,
                        reason=QuestionDispute.Reason.WRONG_ANSWER,
                    )
        return qv

    def test_below_min_disputes_no_action(self, db, org, user, subject):
        # 100 ta answer, 3 ta dispute (below MIN=5)
        qv = self._setup_attempts_with_answers(org, user, subject, n_answers=100, n_disputes=3)
        # Note: signal allaqachon CELERY_TASK_ALWAYS_EAGER da chaqirilgan,
        # lekin ratio < 5% (3/100) — quarantine bo'lmagan bo'lishi kerak
        qv.refresh_from_db()
        assert qv.is_quarantined is False

    def test_above_threshold_quarantines(self, db, org, user, subject):
        # 50 answer, 10 dispute (20% ratio, ko'p MIN=5)
        qv = self._setup_attempts_with_answers(org, user, subject, n_answers=50, n_disputes=10)
        # Signal task allaqachon eager ishlagan
        qv.refresh_from_db()
        assert qv.is_quarantined is True

        # Affected UserAnswer'lar auto_correct=True
        ua_qs = UserAnswer.objects.filter(question_version=qv)
        assert all(ua.auto_correct for ua in ua_qs)

        # Dispute'lar QUARANTINED status
        d_qs = QuestionDispute.objects.filter(question_version=qv)
        assert all(d.status == QuestionDispute.Status.QUARANTINED for d in d_qs)

    def test_idempotent_second_call(self, db, org, user, subject):
        qv = self._setup_attempts_with_answers(org, user, subject, n_answers=20, n_disputes=10)
        qv.refresh_from_db()
        assert qv.is_quarantined is True

        # Yana chaqirsak — already_quarantined
        result = quarantine_check(str(qv.id))
        assert result['status'] == 'already_quarantined'

    def test_missing_question_returns_not_found(self, db):
        import uuid

        result = quarantine_check(str(uuid.uuid4()))
        assert result['status'] == 'not_found'
