"""
Task 4 — Exam Engine model unit tests.

Tekshiriladi:
  - PublicOrTenantManager: tenant filter + is_public + fail-closed
  - MockExam create + question pinning via through table
  - ExamAttempt unique_together (exam, user)
  - PracticeSession blueprint create
  - UserAnswer XOR constraint (DB-level CHECK)
  - AntiCheatEvent strike log
  - QuestionDispute unique (attempt, question_version, user)
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion, Subject
from apps.exams.models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    MockExamQuestion,
    PracticeSession,
    QuestionDispute,
    UserAnswer,
)
from core.tenant import clear_current_org, tenant_context, unscoped_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def question_version(db, org, subject):
    """Bitta QuestionVersion — exam fixturelar uchun."""
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': '2 + 2 = ?'},
            options=[
                {'id': 1, 'text': '4', 'is_correct': True},
                {'id': 2, 'text': '5', 'is_correct': False},
            ],
        )


@pytest.fixture
def question_version_org2(db, org2):
    """QuestionVersion org2 uchun — cross-tenant test'larda."""
    subject2 = Subject.objects.create(name='Other Sub', slug='other-sub')
    with tenant_context(org2):
        q = Question.objects.create(
            organization=org2, subject=subject2, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q, version_number=1, content={'text': 'x'}, options=[]
        )


def _make_mock(org, user, *, is_public=False, status=MockExam.Status.PUBLISHED):
    now = timezone.now()
    return MockExam.objects.create(
        organization=org,
        title='Test Mock',
        duration_minutes=60,
        scheduled_at=now,
        closes_at=now + timedelta(hours=2),
        status=status,
        is_public=is_public,
        created_by=user,
    )


# ─── PublicOrTenantManager ────────────────────────────────────────────────────


@pytest.mark.unit
class TestPublicOrTenantManager:
    def test_org_context_sees_own_private_mock(self, db, org, user):
        with tenant_context(org):
            _make_mock(org, user, is_public=False)
            assert MockExam.objects.count() == 1

    def test_org_context_sees_other_org_public_mock(self, db, org, org2, user, user2):
        with tenant_context(org2):
            _make_mock(org2, user2, is_public=True)

        with tenant_context(org):
            assert MockExam.objects.count() == 1
            assert MockExam.objects.first().is_public is True

    def test_org_context_does_not_see_other_org_private_mock(self, db, org, org2, user, user2):
        with tenant_context(org2):
            _make_mock(org2, user2, is_public=False)
        with tenant_context(org):
            assert MockExam.objects.count() == 0

    def test_no_context_fail_closed(self, db, org, user):
        with tenant_context(org):
            _make_mock(org, user, is_public=True)
        clear_current_org()
        assert MockExam.objects.count() == 0

    def test_unscoped_context_sees_all(self, db, org, org2, user, user2):
        with tenant_context(org):
            _make_mock(org, user, is_public=False)
        with tenant_context(org2):
            _make_mock(org2, user2, is_public=True)
        clear_current_org()
        with unscoped_context():
            assert MockExam.objects.count() == 2

    def test_for_org_helper_ignores_context(self, db, org, org2, user, user2):
        with tenant_context(org2):
            _make_mock(org2, user2, is_public=False)
        assert MockExam.objects.for_org(org2).count() == 1

    def test_public_only_helper(self, db, org, org2, user, user2):
        with tenant_context(org):
            _make_mock(org, user, is_public=False)
        with tenant_context(org2):
            _make_mock(org2, user2, is_public=True)
        assert MockExam.objects.public_only().count() == 1


# ─── MockExam + MockExamQuestion (snapshot through table) ─────────────────────


@pytest.mark.unit
class TestMockExamSnapshot:
    def test_pin_questions_via_through(self, db, org, user, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            MockExamQuestion.objects.create(
                mock_exam=mock, question_version=question_version, order=1, points=2
            )
            assert mock.questions.count() == 1
            assert mock.question_links.first().points == 2

    def test_unique_order_per_mock(self, db, org, user, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            MockExamQuestion.objects.create(
                mock_exam=mock, question_version=question_version, order=1
            )
            q2 = Question.objects.create(
                organization=org,
                subject=question_version.question.subject,
                type=Question.Type.SINGLE_CHOICE,
            )
            qv2 = QuestionVersion.objects.create(
                question=q2, version_number=1, content={'text': 'x'}, options=[]
            )
            with pytest.raises(IntegrityError), transaction.atomic():
                MockExamQuestion.objects.create(mock_exam=mock, question_version=qv2, order=1)

    def test_unique_question_per_mock(self, db, org, user, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            MockExamQuestion.objects.create(
                mock_exam=mock, question_version=question_version, order=1
            )
            with pytest.raises(IntegrityError), transaction.atomic():
                MockExamQuestion.objects.create(
                    mock_exam=mock, question_version=question_version, order=2
                )


# ─── ExamAttempt ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestExamAttempt:
    def test_create_attempt(self, db, org, user, member):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            assert attempt.status == ExamAttempt.Status.IN_PROGRESS
            assert attempt.strikes == 0

    def test_one_attempt_per_user_per_exam(self, db, org, user, member):
        with tenant_context(org):
            mock = _make_mock(org, user)
            ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            with pytest.raises(IntegrityError), transaction.atomic():
                ExamAttempt.objects.create(organization=org, exam=mock, user=user)


# ─── PracticeSession ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPracticeSession:
    def test_create_session_with_blueprint(self, db, org, user, member):
        with tenant_context(org):
            session = PracticeSession.objects.create(
                organization=org,
                user=user,
                blueprint=[{'topic_id': 'abc', 'count': 10, 'difficulty': 'hard'}],
            )
            assert session.status == PracticeSession.Status.IN_PROGRESS
            assert session.blueprint[0]['count'] == 10


# ─── UserAnswer XOR constraint ────────────────────────────────────────────────


@pytest.mark.unit
class TestUserAnswerXOR:
    def test_attempt_only_ok(self, db, org, user, member, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            ua = UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=question_version,
                selected={'option_id': 1},
                is_correct=True,
            )
            assert ua.attempt == attempt
            assert ua.session is None

    def test_session_only_ok(self, db, org, user, member, question_version):
        with tenant_context(org):
            session = PracticeSession.objects.create(organization=org, user=user, blueprint=[])
            ua = UserAnswer.objects.create(
                organization=org,
                session=session,
                question_version=question_version,
                selected={'option_id': 1},
            )
            assert ua.session == session
            assert ua.attempt is None

    def test_both_null_rejected(self, db, org, user, member, question_version):
        with tenant_context(org), pytest.raises(IntegrityError), transaction.atomic():
            UserAnswer.objects.create(
                organization=org,
                attempt=None,
                session=None,
                question_version=question_version,
                selected={},
            )

    def test_both_set_rejected(self, db, org, user, member, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            session = PracticeSession.objects.create(organization=org, user=user, blueprint=[])
            with pytest.raises(IntegrityError), transaction.atomic():
                UserAnswer.objects.create(
                    organization=org,
                    attempt=attempt,
                    session=session,
                    question_version=question_version,
                    selected={},
                )


# ─── AntiCheatEvent ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestAntiCheatEvent:
    def test_log_strike(self, db, org, user, member):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            event = AntiCheatEvent.objects.create(
                organization=org,
                attempt=attempt,
                event_type=AntiCheatEvent.EventType.TAB_SWITCH,
                metadata={'screen_w': 1920},
            )
            assert event.event_type == 'tab_switch'
            assert attempt.anti_cheat_events.count() == 1


# ─── QuestionDispute ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestQuestionDispute:
    def test_file_dispute(self, db, org, user, member, question_version):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            dispute = QuestionDispute.objects.create(
                organization=org,
                attempt=attempt,
                question_version=question_version,
                user=user,
                reason=QuestionDispute.Reason.WRONG_ANSWER,
                note="Variant 3 ham to'g'ri",
            )
            assert dispute.status == QuestionDispute.Status.OPEN

    def test_one_dispute_per_user_per_question_per_attempt(
        self, db, org, user, member, question_version
    ):
        with tenant_context(org):
            mock = _make_mock(org, user)
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            QuestionDispute.objects.create(
                organization=org,
                attempt=attempt,
                question_version=question_version,
                user=user,
                reason=QuestionDispute.Reason.WRONG_ANSWER,
            )
            with pytest.raises(IntegrityError), transaction.atomic():
                QuestionDispute.objects.create(
                    organization=org,
                    attempt=attempt,
                    question_version=question_version,
                    user=user,
                    reason=QuestionDispute.Reason.AMBIGUOUS,
                )
