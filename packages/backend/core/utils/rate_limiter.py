"""
Rate Limiter — Redis-backed sliding window with progressive penalty.

Asosiy qoidalar:
  - API endpoint: 30 req/min per user/IP
  - Static endpoint: 100 req/min per IP
  - Login endpoint: 5 req/min per IP
  - Admin/superuser: bypass

Progressive penalty (qaytariluvchi buzilishlar uchun):
  1-marta: 1 daqiqa block
  2-marta: 5 daqiqa block
  3-marta: 1 soat block
  4-marta+: 24 soat block

Key sxemasi:
  rl:count:{tier}:{ident}     — request counter (TTL = window)
  rl:violations:{ident}       — buzilishlar soni (TTL = 7 kun)
  rl:block:{ident}            — aktiv block (TTL = penalty)
"""

import logging
from dataclasses import dataclass

from django.conf import settings

log = logging.getLogger(__name__)


# ── Tier sozlamalari ──────────────────────────────────────────


@dataclass(frozen=True)
class RateTier:
    name: str
    limit: int  # requestlar soni
    window: int  # vaqt oynasi (sekund)


TIER_API = RateTier(name='api', limit=30, window=60)
TIER_STATIC = RateTier(name='static', limit=100, window=60)
TIER_LOGIN = RateTier(name='login', limit=5, window=60)


# ── Progressive penalty ladder ────────────────────────────────

PENALTY_LADDER = [
    60,  # 1-marta:  1 daqiqa
    5 * 60,  # 2-marta:  5 daqiqa
    60 * 60,  # 3-marta:  1 soat
    24 * 3600,  # 4-marta+: 24 soat
]
VIOLATION_TTL = 7 * 24 * 3600  # buzilishlar 7 kun yashaydi


# ── Exception ─────────────────────────────────────────────────


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int, tier: str):
        self.retry_after = retry_after
        self.tier = tier
        super().__init__(f'Rate limit exceeded ({tier}). Retry after {retry_after}s')


# ── Redis ─────────────────────────────────────────────────────


def _redis():
    import redis

    return redis.Redis.from_url(
        getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
        decode_responses=True,
    )


# ── Asosiy API ────────────────────────────────────────────────


def check(identifier: str, tier: RateTier) -> None:
    """
    Bitta request'ni tekshirish va counter'ni oshirish.
    Raise RateLimitExceeded — agar limit oshsa yoki block aktiv bo'lsa.
    """
    r = _redis()

    # 1) Aktiv block bormi?
    block_key = f'rl:block:{identifier}'
    block_ttl = r.ttl(block_key)
    if block_ttl and block_ttl > 0:
        raise RateLimitExceeded(retry_after=block_ttl, tier=tier.name)

    # 2) Counter'ni oshirish
    count_key = f'rl:count:{tier.name}:{identifier}'
    pipe = r.pipeline()
    pipe.incr(count_key)
    pipe.expire(count_key, tier.window)
    count, _ = pipe.execute()

    # 3) Limit oshganmi?
    if count > tier.limit:
        retry_after = _apply_penalty(r, identifier)
        log.warning(
            'Rate limit exceeded: ident=%s tier=%s count=%d/%d penalty=%ds',
            identifier,
            tier.name,
            count,
            tier.limit,
            retry_after,
        )
        raise RateLimitExceeded(retry_after=retry_after, tier=tier.name)


def _apply_penalty(r, identifier: str) -> int:
    """Progressive penalty qo'llash. Block TTL'ni qaytaradi."""
    viol_key = f'rl:violations:{identifier}'
    block_key = f'rl:block:{identifier}'

    pipe = r.pipeline()
    pipe.incr(viol_key)
    pipe.expire(viol_key, VIOLATION_TTL)
    violations, _ = pipe.execute()

    # Penalty ladder'dan tanlash (oxirgi qadam — eng yuqori)
    idx = min(int(violations) - 1, len(PENALTY_LADDER) - 1)
    penalty = PENALTY_LADDER[idx]

    r.set(block_key, '1', ex=penalty)
    return penalty


def reset(identifier: str) -> None:
    """Identifikator uchun barcha rate limit ma'lumotlarini tozalash (admin uchun)."""
    r = _redis()
    keys = list(r.scan_iter(match=f'rl:*:{identifier}'))
    keys += list(r.scan_iter(match=f'rl:*:*:{identifier}'))
    if keys:
        r.delete(*keys)


def get_status(identifier: str) -> dict:
    """Identifikator uchun joriy holat (debug/monitoring uchun)."""
    r = _redis()
    block_ttl = r.ttl(f'rl:block:{identifier}')
    violations = r.get(f'rl:violations:{identifier}') or '0'
    return {
        'identifier': identifier,
        'blocked': block_ttl is not None and block_ttl > 0,
        'block_ttl': block_ttl if block_ttl and block_ttl > 0 else 0,
        'violations': int(violations),
        'counters': {
            tier.name: int(r.get(f'rl:count:{tier.name}:{identifier}') or 0)
            for tier in (TIER_API, TIER_STATIC, TIER_LOGIN)
        },
    }
