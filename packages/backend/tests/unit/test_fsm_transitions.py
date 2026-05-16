"""
ISSUE-205 — State machine transitions for 7 status models.

Har model uchun:
  - Valid transition'lar `_ALLOWED_TRANSITIONS` dict'iga mos keladi
  - Invalid transition `ValueError` ko'taradi (state machine guard)
  - Terminal state'lardan boshqa state'ga o'tib bo'lmaydi

ExamAttempt allaqachon test'lar bilan qoplangan (`test_exam_tasks.py`,
`test_exam_api.py`). Bu fayl qolgan 6 ta model — MockExam, PracticeSession,
QuestionDispute, PaymentIntent, OrganizationSubscription, Notification —
uchun yagona unit test joyi.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.commerce.models import OrganizationSubscription, PaymentIntent, SubscriptionPlan
from apps.engagement.models import Notification
from apps.exams.models import MockExam, PracticeSession, QuestionDispute
from core.tenant import tenant_context

# ── MockExam ────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestMockExamFSM:
    @pytest.fixture
    def exam(self, db, org, user):
        with tenant_context(org):
            return MockExam.objects.create(
                organization=org,
                title='Test',
                duration_minutes=60,
                scheduled_at=timezone.now() + timedelta(days=1),
                closes_at=timezone.now() + timedelta(days=2),
                created_by=user,
            )

    def test_draft_publish_close_happy_path(self, exam):
        assert exam.status == MockExam.Status.DRAFT
        exam.publish()
        assert exam.status == MockExam.Status.PUBLISHED
        exam.close()
        assert exam.status == MockExam.Status.CLOSED

    def test_draft_can_cancel(self, exam):
        exam.cancel()
        assert exam.status == MockExam.Status.CANCELLED

    def test_published_can_cancel(self, exam):
        exam.publish()
        exam.cancel()
        assert exam.status == MockExam.Status.CANCELLED

    def test_closed_is_terminal(self, exam):
        exam.publish()
        exam.close()
        with pytest.raises(ValueError, match='Invalid state transition'):
            exam.cancel()

    def test_cancelled_is_terminal(self, exam):
        exam.cancel()
        with pytest.raises(ValueError):
            exam.publish()

    def test_cannot_close_from_draft(self, exam):
        with pytest.raises(ValueError):
            exam.close()


# ── PracticeSession ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPracticeSessionFSM:
    @pytest.fixture
    def session(self, db, org, user):
        with tenant_context(org):
            return PracticeSession.objects.create(
                organization=org,
                user=user,
                blueprint=[{'topic_id': 'x', 'count': 10}],
            )

    def test_in_progress_to_completed(self, session):
        session.complete()
        assert session.status == PracticeSession.Status.COMPLETED
        assert session.ended_at is not None

    def test_in_progress_to_abandoned(self, session):
        session.abandon()
        assert session.status == PracticeSession.Status.ABANDONED
        assert session.ended_at is not None

    def test_completed_is_terminal(self, session):
        session.complete()
        with pytest.raises(ValueError):
            session.abandon()

    def test_abandoned_is_terminal(self, session):
        session.abandon()
        with pytest.raises(ValueError):
            session.complete()


# ── QuestionDispute ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestQuestionDisputeFSM:
    @pytest.fixture
    def dispute(self, db, org, user, subject):
        from apps.catalog.models import Question, QuestionVersion
        from apps.exams.models import ExamAttempt, MockExam

        with tenant_context(org):
            exam = MockExam.objects.create(
                organization=org,
                title='T',
                duration_minutes=60,
                scheduled_at=timezone.now() + timedelta(days=1),
                closes_at=timezone.now() + timedelta(days=2),
                created_by=user,
            )
            attempt = ExamAttempt.objects.create(organization=org, exam=exam, user=user)
            q = Question.objects.create(
                organization=org,
                subject=subject,
                type=Question.Type.SINGLE_CHOICE,
            )
            qv = QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 'x'},
                options=[{'id': 1, 'text': 'a', 'is_correct': True}],
            )
            return QuestionDispute.objects.create(
                organization=org,
                attempt=attempt,
                question_version=qv,
                user=user,
                reason=QuestionDispute.Reason.AMBIGUOUS,
            )

    def test_open_to_quarantined(self, dispute):
        dispute.quarantine()
        assert dispute.status == QuestionDispute.Status.QUARANTINED

    def test_open_to_rejected(self, dispute):
        dispute.reject()
        assert dispute.status == QuestionDispute.Status.REJECTED

    def test_quarantined_is_terminal(self, dispute):
        dispute.quarantine()
        with pytest.raises(ValueError):
            dispute.reject()

    def test_rejected_is_terminal(self, dispute):
        dispute.reject()
        with pytest.raises(ValueError):
            dispute.quarantine()


# ── PaymentIntent ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPaymentIntentFSM:
    @pytest.fixture
    def intent(self, db, user):
        return PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.STUB,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
        )

    def test_created_start_processing(self, intent):
        intent.start_processing()
        assert intent.status == PaymentIntent.Status.PROCESSING

    def test_processing_to_succeeded(self, intent):
        intent.start_processing()
        intent.mark_succeeded(provider_tx_id='tx-1')
        assert intent.status == PaymentIntent.Status.SUCCEEDED
        assert intent.provider_tx_id == 'tx-1'

    def test_processing_to_failed_with_message(self, intent):
        intent.start_processing()
        intent.mark_failed(error_message='Insufficient funds')
        assert intent.status == PaymentIntent.Status.FAILED
        assert intent.error_message == 'Insufficient funds'

    def test_created_cancel(self, intent):
        intent.cancel()
        assert intent.status == PaymentIntent.Status.CANCELLED

    def test_succeeded_is_terminal(self, intent):
        intent.start_processing()
        intent.mark_succeeded()
        with pytest.raises(ValueError):
            intent.cancel()
        with pytest.raises(ValueError):
            intent.mark_failed()

    def test_failed_is_terminal(self, intent):
        intent.mark_failed()
        with pytest.raises(ValueError):
            intent.mark_succeeded()

    def test_created_cannot_skip_to_succeeded(self, intent):
        # Guard: webhook handler MUST start_processing() avval
        with pytest.raises(ValueError):
            intent.mark_succeeded()


# ── OrganizationSubscription ────────────────────────────────────────────────


@pytest.mark.unit
class TestOrganizationSubscriptionFSM:
    @pytest.fixture
    def plan(self, db):
        return SubscriptionPlan.objects.create(
            name='Basic',
            slug='basic',
            billing_period=SubscriptionPlan.BillingPeriod.MONTHLY,
            pricing_model=SubscriptionPlan.PricingModel.FLAT,
            price_uzs=Decimal('100000'),
        )

    @pytest.fixture
    def sub(self, db, org, plan):
        return OrganizationSubscription.objects.create(
            organization=org,
            plan=plan,
            current_period_started_at=timezone.now(),
            current_period_ends_at=timezone.now() + timedelta(days=30),
        )

    def test_trialing_to_active(self, sub):
        sub.activate()
        assert sub.status == OrganizationSubscription.Status.ACTIVE

    def test_active_to_past_due(self, sub):
        sub.activate()
        sub.mark_past_due()
        assert sub.status == OrganizationSubscription.Status.PAST_DUE

    def test_past_due_reinstate_back_to_active(self, sub):
        sub.activate()
        sub.mark_past_due()
        sub.activate()
        assert sub.status == OrganizationSubscription.Status.ACTIVE

    def test_cancel_with_reason(self, sub):
        sub.cancel(reason='User requested')
        assert sub.status == OrganizationSubscription.Status.CANCELLED
        assert sub.cancellation_reason == 'User requested'
        assert sub.cancelled_at is not None

    def test_cancelled_is_terminal(self, sub):
        sub.cancel()
        with pytest.raises(ValueError):
            sub.activate()

    def test_expired_is_terminal(self, sub):
        sub.expire()
        with pytest.raises(ValueError):
            sub.activate()

    def test_invalid_trialing_to_past_due(self, sub):
        # ISSUE-205 guard: TRIALING'dan PAST_DUE'ga to'g'ridan-to'g'ri o'tib bo'lmaydi.
        with pytest.raises(ValueError):
            sub.mark_past_due()


# ── Notification ────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestNotificationFSM:
    @pytest.fixture
    def notif(self, db, user):
        return Notification.objects.create(
            user=user,
            channel=Notification.Channel.TELEGRAM,
            title='Hi',
            body='Test message',
        )

    def test_pending_to_sent(self, notif):
        notif.mark_sent()
        assert notif.status == Notification.Status.SENT
        assert notif.sent_at is not None

    def test_sent_to_read(self, notif):
        notif.mark_sent()
        notif.mark_read()
        assert notif.status == Notification.Status.READ
        assert notif.read_at is not None

    def test_pending_to_failed_with_error(self, notif):
        notif.mark_failed(error='Telegram API down')
        assert notif.status == Notification.Status.FAILED
        assert notif.delivery_attempts == 1
        assert 'Telegram' in notif.error

    def test_failed_retry_back_to_pending(self, notif):
        notif.mark_failed()
        notif.retry()
        assert notif.status == Notification.Status.PENDING

    def test_read_is_terminal(self, notif):
        notif.mark_sent()
        notif.mark_read()
        with pytest.raises(ValueError):
            notif.retry()

    def test_pending_cannot_mark_read_directly(self, notif):
        with pytest.raises(ValueError):
            notif.mark_read()
