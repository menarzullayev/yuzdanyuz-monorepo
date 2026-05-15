"""
Task 4 — Exam WebSocket consumer tests.

`channels.testing.WebsocketCommunicator` orqali async tests.
InMemoryChannelLayer (test settings'da) bilan Redis kerakmas.

Tekshiriladi:
  - Anonim user → 4401 close
  - Boshqa user'ning attempt'i → 4404
  - Submitted attempt'ga ulanish → 4409
  - Heartbeat → ack + seconds_left
  - Anti-cheat strike (3 ta) → cancelled + close
  - Timer request → seconds_left
"""

from datetime import timedelta

import pytest
from channels.testing import WebsocketCommunicator
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    MockExamQuestion,
)
from core.asgi import application
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


def _make_attempt(org, user, subject, *, status=ExamAttempt.Status.IN_PROGRESS):
    now = timezone.now()
    with tenant_context(org):
        qv = _make_qv(org, subject)
        mock = MockExam.objects.create(
            organization=org,
            title='WS Mock',
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=False,
            created_by=user,
        )
        MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)
        attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user, status=status)
    return attempt


async def _make_communicator(attempt_id, user=None):
    """WebsocketCommunicator with optional user in scope."""
    path = f'/ws/exams/attempt/{attempt_id}/'
    communicator = WebsocketCommunicator(application, path)
    if user is not None:
        communicator.scope['user'] = user
    return communicator


# ─── Connection guards ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestConnectionGuards:
    async def test_anonymous_rejected_4401(self, db, org, user, member, subject):
        from django.contrib.auth.models import AnonymousUser

        attempt = await _make_attempt_async(org, user, subject)
        communicator = await _make_communicator(attempt.id, user=AnonymousUser())
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4401

    async def test_other_user_rejected_4404(self, db, org, user, user2, member, subject):
        attempt = await _make_attempt_async(org, user, subject)
        communicator = await _make_communicator(attempt.id, user=user2)
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4404

    async def test_submitted_attempt_rejected_4409(self, db, org, user, member, subject):
        attempt = await _make_attempt_async(org, user, subject, status=ExamAttempt.Status.SUBMITTED)
        communicator = await _make_communicator(attempt.id, user=user)
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4409


# ─── Heartbeat / timer ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestHeartbeatTimer:
    async def test_initial_timer_event(self, db, org, user, member, subject):
        attempt = await _make_attempt_async(org, user, subject)
        communicator = await _make_communicator(attempt.id, user=user)
        connected, _ = await communicator.connect()
        assert connected
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['type'] == 'timer'
        assert msg['seconds_left'] > 0
        await communicator.disconnect()

    async def test_heartbeat_returns_ack(self, db, org, user, member, subject):
        attempt = await _make_attempt_async(org, user, subject)
        communicator = await _make_communicator(attempt.id, user=user)
        await communicator.connect()
        await communicator.receive_json_from(timeout=2)  # initial timer

        await communicator.send_json_to({'type': 'heartbeat'})
        ack = await communicator.receive_json_from(timeout=2)
        assert ack['type'] == 'heartbeat_ack'
        assert ack['seconds_left'] > 0
        await communicator.disconnect()


# ─── Anti-cheat strikes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestAntiCheatStrikes:
    async def test_three_strikes_cancels(self, db, org, user, member, subject):
        attempt = await _make_attempt_async(org, user, subject)
        communicator = await _make_communicator(attempt.id, user=user)
        await communicator.connect()
        await communicator.receive_json_from(timeout=2)  # initial timer

        # 1-strike
        await communicator.send_json_to({'type': 'anticheat', 'event': 'tab_switch'})
        msg = await communicator.receive_json_from(timeout=2)
        assert msg == {'type': 'strike', 'count': 1, 'max': 3}

        # 2-strike
        await communicator.send_json_to({'type': 'anticheat', 'event': 'window_blur'})
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['count'] == 2

        # 3-strike → cancelled + close
        await communicator.send_json_to({'type': 'anticheat', 'event': 'fullscreen_exit'})
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['count'] == 3
        cancel_msg = await communicator.receive_json_from(timeout=2)
        assert cancel_msg['type'] == 'cancelled'

        # DB tekshirish
        await _verify_attempt_cancelled(attempt.id)


# ─── DB sync helpers (async test'lar uchun) ──────────────────────────────────


async def _make_attempt_async(org, user, subject, *, status=ExamAttempt.Status.IN_PROGRESS):
    from channels.db import database_sync_to_async

    return await database_sync_to_async(_make_attempt)(org, user, subject, status=status)


async def _verify_attempt_cancelled(attempt_id):
    from channels.db import database_sync_to_async

    @database_sync_to_async
    def _check():
        with unscoped_context():
            a = ExamAttempt.objects.get(pk=attempt_id)
            assert a.status == ExamAttempt.Status.CANCELLED
            assert a.strikes == 3
            assert AntiCheatEvent.objects.filter(attempt=a).count() == 3

    await _check()
