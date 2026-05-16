"""
ISSUE-307 — Application-level caching helpers.

Django built-in cache framework (`django.core.cache`) ko'p joyda foydalanish
uchun tenant-aware decorator + invalidation helper.

Pattern:
    from core.cache import cached, invalidate_for_org

    @cached(ttl=300, key_prefix='org_settings')
    def get_effective_settings(org):
        return _expensive_aggregation(org)

    # On Organization.save() signal:
    invalidate_for_org(org, key_prefix='org_settings')
"""

from __future__ import annotations

import functools
import hashlib
import logging
from collections.abc import Callable

from django.core.cache import cache

logger = logging.getLogger(__name__)


def _make_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """Stable cache key — args/kwargs hash bilan."""
    payload = repr((args, sorted(kwargs.items()))).encode()
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f'yz:{prefix}:{digest}'


def cached(ttl: int = 300, key_prefix: str | None = None):
    """Function decorator — natija Django cache'da TTL bilan saqlanadi.

    Args:
        ttl: cache TTL sekundlarda
        key_prefix: cache key prefix (None bo'lsa function nomidan)

    Pattern:
        @cached(ttl=300, key_prefix='subject_stats')
        def get_subject_averages(org):
            return ...
    """

    def decorator(func: Callable) -> Callable:
        prefix = key_prefix or f'{func.__module__}.{func.__qualname__}'

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            key = _make_key(prefix, args, kwargs)
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            value = func(*args, **kwargs)
            cache.set(key, value, ttl)
            return value

        wrapper.cache_invalidate = lambda *a, **kw: cache.delete(_make_key(prefix, a, kw))
        wrapper.cache_clear_prefix = lambda: _clear_prefix(prefix)
        return wrapper

    return decorator


def _clear_prefix(prefix: str) -> int:
    """Prefiks bo'yicha barcha cache'ni o'chirish (Redis-only, full scan)."""
    try:
        from django_redis import get_redis_connection

        conn = get_redis_connection('default')
        pattern = f'*:yz:{prefix}:*'  # django-redis qo'shgan namespace prefix bilan
        keys = list(conn.scan_iter(match=pattern))
        if keys:
            conn.delete(*keys)
        return len(keys)
    except Exception as e:
        logger.warning('cache_clear_prefix failed for %s: %s', prefix, e)
        return 0


def invalidate_for_org(org_id, *, key_prefix: str) -> None:
    """Org-scoped cache invalidation. Signal handler'lardan chaqiriladi."""
    full_prefix = f'{key_prefix}:org:{org_id}'
    _clear_prefix(full_prefix)
