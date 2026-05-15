"""
Task 5 — Leaderboard WebSocket consumer tests.

InMemoryChannelLayer + WebsocketCommunicator (Redis kerakmas).

Tekshiriladi:
  - Anonymous user → 4401
  - Subscribe global / mock / tenant scope (auth check)
  - Tenant scope: faqat o'z org/region uchun (4403 wrong tenant)
  - record_attempt → subscribed client `leaderboard.updated` event oladi
"""

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from apps.engagement import leaderboard
from core.asgi import application

# ─── Connection guards ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestLeaderboardWSConnection:
    async def test_anonymous_rejected(self, db):
        communicator = WebsocketCommunicator(application, '/ws/leaderboard/global/')
        communicator.scope['user'] = AnonymousUser()
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4401

    async def test_authenticated_user_can_subscribe_global(self, db, user, member):
        communicator = WebsocketCommunicator(application, '/ws/leaderboard/global/')
        communicator.scope['user'] = user
        connected, _ = await communicator.connect()
        assert connected is True
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['type'] == 'subscribed'
        assert msg['scope'] == 'lb_global'
        await communicator.disconnect()

    async def test_tenant_scope_wrong_org_rejected(self, db, user, org2, member):
        # User org1 member, lekin org2 scope'iga subscribe urinmoqda → 4403
        communicator = WebsocketCommunicator(application, f'/ws/leaderboard/tenant/{org2.id}/')
        communicator.scope['user'] = user
        # request.org middleware'siz scope.org = None → fail unless user is staff
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4403


# ─── Broadcast on record_attempt ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestLeaderboardBroadcast:
    async def test_record_attempt_pushes_to_global_subscribers(self, db, user, member, org):
        from channels.db import database_sync_to_async

        # Connect global subscriber
        communicator = WebsocketCommunicator(application, '/ws/leaderboard/global/')
        communicator.scope['user'] = user
        await communicator.connect()
        await communicator.receive_json_from(timeout=2)  # 'subscribed'

        # Trigger record_attempt (sync sync_to_async wrapper)
        await database_sync_to_async(leaderboard.record_attempt)(
            user_id=user.id, score=80, mock_id='mock-uuid-test', org_id=org.id
        )

        # Subscribed client `leaderboard.updated` event oladi
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['type'] == 'leaderboard.updated'
        assert msg['scope'] == 'global'
        assert msg['reason'] == 'new_score'

        await communicator.disconnect()

    async def test_mock_scope_subscriber_receives_specific_mock(self, db, user, member, org):
        from channels.db import database_sync_to_async

        mock_id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        communicator = WebsocketCommunicator(application, f'/ws/leaderboard/mock/{mock_id}/')
        communicator.scope['user'] = user
        await communicator.connect()
        await communicator.receive_json_from(timeout=2)  # 'subscribed'

        # record_attempt → broadcast to lb_mock_<id> group
        await database_sync_to_async(leaderboard.record_attempt)(
            user_id=user.id, score=70, mock_id=mock_id, org_id=org.id
        )

        # Birinchi keladigan event global yoki mock — ikkalasini ham eshitish mumkin.
        # Mock subscriber faqat mock event olishi kerak, lekin kelish tartibi
        # platform-specific. Eng aniq tekshirish: kamida bir mock event keladi.
        msg = await communicator.receive_json_from(timeout=2)
        assert msg['type'] == 'leaderboard.updated'
        # Bu konsumer faqat mock_id group'iga subscribed, shuning uchun mock event
        assert msg['scope'] == f'mock:{mock_id}'

        await communicator.disconnect()
