"""
Membership existence cache.

TenantMiddleware har request'da `user.memberships.filter(org, status='active').exists()`
chaqirardi — bu DB hit. Redis cache (60s TTL) bilan bu N+1 yumshatiladi.

Invalidation: `apps/organizations/signals.py` Membership post_save/post_delete'da
`invalidate(user_id, org_id)` chaqiradi. 60s TTL — agar signal o'tkazib yuborilsa
ham eskirgan ma'lumot uzoq tursa olmaydi.

Redis errors yumshoq handle qilinadi: cache miss → DB fallback. Hech qachon
xavfsizlik qaroriga ta'sir qilmaydi (False positive bo'lmasligi muhim — chunki
cache ishlamasa, DB query orqali tekshiriladi).
"""

import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

TTL_SECONDS = 60
_redis_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
            decode_responses=True,
        )
    return _redis_client


def _key(user_pk, org_pk) -> str:
    return f'tenant:membership:{user_pk}:{org_pk}'


def is_active_member(user, org) -> bool:
    """
    Cache-first check. Returns True iff user has an active membership in org.

    Cache miss yoki Redis error → DB query fallback (xavfsiz default).
    """
    key = _key(user.pk, org.pk)

    try:
        cached = _redis().get(key)
        if cached is not None:
            return cached == '1'
    except redis.RedisError as e:
        logger.warning('membership cache read failed: %s', e)
        # Fall through to DB

    is_member = user.memberships.filter(organization=org, status='active').exists()

    try:
        _redis().setex(key, TTL_SECONDS, '1' if is_member else '0')
    except redis.RedisError as e:
        logger.warning('membership cache write failed: %s', e)

    return is_member


def invalidate(user_pk, org_pk) -> None:
    """post_save/post_delete signal'lar tomonidan chaqiriladi."""
    try:
        _redis().delete(_key(user_pk, org_pk))
    except redis.RedisError as e:
        logger.warning('membership cache invalidate failed: %s', e)
