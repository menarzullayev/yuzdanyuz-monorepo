"""
Task 5 — Leaderboard WebSocket consumer.

URL: ws/leaderboard/<scope>/
  - ws/leaderboard/global/
  - ws/leaderboard/region/<region_id>/
  - ws/leaderboard/tenant/<org_id>/
  - ws/leaderboard/mock/<mock_id>/

Client subscribes to a scope group; har leaderboard.record_attempt'da
mos group'ga push event yuboriladi. Frontend event qabul qiladi va
REST endpoint'dan yangi leaderboard'ni qayta yuklab oladi (poll-on-push).

Bu pattern oddiy va xavfsiz: real-time payload kichik (faqat invalidation
hint), ranking ma'lumotini frontend cache'lash mumkin.

Server → client message:
  {type: 'leaderboard.updated', scope: <str>, reason: 'new_score'}
"""

import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger(__name__)


def group_name_for(scope_kind: str, scope_id: str = '') -> str:
    """
    Channel layer group names: alphanumeric + underscore + hyphen + period only.
    UUID hyphens OK; ':' not allowed → underscore separator.
    """
    if scope_id:
        return f'lb_{scope_kind}_{scope_id}'
    return f'lb_{scope_kind}'


class LeaderboardConsumer(AsyncJsonWebsocketConsumer):
    """
    Single-scope subscriber. URL kwargs scope'ni belgilaydi.

    Auth: user authenticated bo'lishi kerak. Tenant/region scope uchun
    qo'shimcha tekshiruv (faqat o'z org/region'ini subscribe qilishi mumkin).
    """

    async def connect(self):
        # ISSUE-207: explicit token validation
        from core.ws_auth import authenticate_ws

        user = await authenticate_ws(self.scope)
        if user is None:
            await self.close(code=4401)
            return
        self.scope['user'] = user

        kwargs = self.scope['url_route']['kwargs']
        if 'region_id' in kwargs:
            self.group_name = group_name_for('region', kwargs['region_id'])
            # Region scope: user shu region'da bo'lishi kerak
            if str(getattr(user, 'region_id', '')) != kwargs['region_id']:
                await self.close(code=4403)
                return
        elif 'org_id' in kwargs:
            self.group_name = group_name_for('tenant', kwargs['org_id'])
            # Tenant scope: user shu org member bo'lishi (yoki staff)
            user_org = self.scope.get('org')  # set by middleware
            if user_org is None or str(user_org.id) != kwargs['org_id']:
                if not (user.is_superuser or user.is_staff):
                    await self.close(code=4403)
                    return
        elif 'mock_id' in kwargs:
            self.group_name = group_name_for('mock', kwargs['mock_id'])
        else:
            self.group_name = group_name_for('global')

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({'type': 'subscribed', 'scope': self.group_name})

    async def disconnect(self, code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # ── Group message handler ─────────────────────────────────────────────────
    # `lb.update` → method `lb_update` (channels convention: dot → underscore)

    async def lb_update(self, event):
        """Server-side push: leaderboard yangilangan, frontend qayta fetch qilsin."""
        await self.send_json(
            {
                'type': 'leaderboard.updated',
                'scope': event.get('scope'),
                'reason': event.get('reason', 'new_score'),
            }
        )
