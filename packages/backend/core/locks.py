"""
Distributed lock primitives — Redis SET NX EX (Lua-siz, fakeredis-compat).

ISSUE-102: Celery beat single-replica + failover sababli scheduled task'lar
qayta ishga tushishi mumkin (notification duplikat, SMS bill spike). Lock
har scheduled task uchun "only one runner at a time" garantiyasini beradi.

Usage:
    from core.locks import single_runner_lock

    @shared_task
    def check_broken_streaks_task():
        with single_runner_lock('streak_check_broken', expire=600) as acquired:
            if not acquired:
                return {'status': 'skipped_lock_busy'}
            ... critical section ...

Mechanism:
    SET lock:<name> <random_token> NX EX <expire>  → acquire
    Delete only if our token still there (atomic check-and-delete)

Idle workers wait yo'q — agar lock band bo'lsa, task darhol qaytadi.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _release_if_owner(client: redis.Redis, key: str, token: str) -> bool:
    """Atomic check-and-delete: faqat o'z token'imizni o'chirish (Lua-siz, pipeline orqali).

    Race scenariy: TTL expired → boshqa caller lock oldi → biz uni o'chirib qo'ymaymiz.
    Lua eval mavjud bo'lsa (real Redis), eval ishlatish 1 RTT'ga tushadi; bu yerda
    fakeredis compat uchun WATCH+MULTI+EXEC ishlatamiz (3 RTT, lekin to'g'ri).
    """
    try:
        with client.pipeline() as pipe:
            pipe.watch(key)
            current = pipe.get(key)
            if current != token:
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(key)
            pipe.execute()
            return True
    except redis.WatchError:
        # Boshqa client shu vaqt ichida o'zgartirdi — biz tegmaymiz
        return False


def _redis() -> redis.Redis:
    """Cached Redis client. Test'da conftest.py mock_redis patch qiladi."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


@contextmanager
def single_runner_lock(name: str, expire: int = 300) -> Iterator[bool]:
    """
    Context manager: lock olinsa True, band bo'lsa False qaytaradi.

    Args:
        name: lock identifier (mas. 'streak_check_broken')
        expire: TTL sekundlarda. Worker crash bo'lsa, TTL'dan keyin avtomat
                ozod qilinadi. Vazifa o'rtacha vaqtidan 5-10x katta tanlang.

    Pattern:
        with single_runner_lock('my_task', expire=600) as acquired:
            if not acquired:
                return
            # ... critical section ...

    Race-safety: SET NX EX atomic. Release Lua script bilan
    (faqat o'z token'imizni o'chiramiz — TTL expired bo'lib boshqa caller
    lock olib bo'lgan stsenariydan himoya).
    """
    key = f'lock:{name}'
    token = secrets.token_urlsafe(16)
    client = _redis()

    acquired = bool(client.set(key, token, nx=True, ex=expire))
    if acquired:
        logger.debug('Lock acquired: %s (token=%s, expire=%ds)', key, token[:6], expire)
    else:
        logger.info('Lock busy, skipping: %s', key)

    try:
        yield acquired
    finally:
        if acquired:
            if _release_if_owner(client, key, token):
                logger.debug('Lock released: %s', key)
            else:
                logger.warning('Lock %s not released — token mismatch (TTL expired?)', key)
