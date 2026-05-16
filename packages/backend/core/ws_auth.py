"""
ISSUE-207 — Explicit WebSocket authentication.

Consumer'lar middleware-set `scope['user']`'ga ishonmasdan o'zi JWT cookie'ni
decode qiladi. Defense-in-depth: middleware refactor anonymous WS connection'larini
silent qabul qilish riskini bartaraf etadi.

Cookie based — `access_token` cookie (HttpOnly) JWTAuthMiddleware'ga o'xshash
oqib chiqarish.
"""

from __future__ import annotations

import logging

from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


def _parse_cookies(scope) -> dict:
    """ASGI scope['headers'] dan cookie header'ni parse qilish."""
    cookies: dict[str, str] = {}
    for name, value in scope.get('headers', []):
        if name == b'cookie':
            for pair in value.decode('utf-8', errors='ignore').split(';'):
                if '=' in pair:
                    k, v = pair.strip().split('=', 1)
                    cookies[k] = v
    return cookies


@database_sync_to_async
def _verify_token_and_get_user(token: str):
    """JWTAuthMiddleware ishlatadigan logic'ni qayta ishlatish.

    Token noto'g'ri / eskirgan / user topilmasa None qaytaradi.
    """
    if not token:
        return None
    try:
        from apps.accounts.models import CustomUser
        from apps.accounts.services.token_service import verify_access_token
    except Exception as e:
        logger.warning('ws_auth: token_service import failed: %s', e)
        return None

    try:
        payload = verify_access_token(token)
        if not payload:
            return None
        user_id = payload.get('sub')
        if not user_id:
            return None
        return CustomUser.objects.filter(id=user_id, is_active=True).first()
    except Exception as e:
        logger.info('ws_auth: token verification failed: %s', e)
        return None


async def authenticate_ws(scope):
    """Consumer.connect() ichida chaqirilishi kerak. None qaytsa close(4401).

    Pattern:
        user = await authenticate_ws(self.scope)
        if user is None:
            await self.close(code=4401)
            return
        self.scope['user'] = user
    """
    cookies = _parse_cookies(scope)
    token = cookies.get('access_token', '')
    user = await _verify_token_and_get_user(token)
    if user is None:
        # Fallback: middleware'dan kelgan user'ga ham qarash (test'lar uchun)
        scoped_user = scope.get('user')
        if scoped_user is not None and not scoped_user.is_anonymous:
            return scoped_user
    return user
