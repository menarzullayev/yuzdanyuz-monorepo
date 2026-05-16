"""
Rate Limiter — Redis-backed sliding window with progressive penalty.

═══════════════════════════════════════════════════════════════════════
TIER STRATEGY (2026-05-16 redesign — NAT-aware)
═══════════════════════════════════════════════════════════════════════

Login (anti brute-force):
  - TIER_LOGIN: 5 req/min — login, OTP, password reset

Browse (read-heavy, NAT-friendly):
  - TIER_BROWSE: 120 req/min — API GET, list endpoints
    (single office NAT with ~50 users browsing comfortably fits)

Mutation (write — less frequent):
  - TIER_API: 60 req/min — POST/PUT/DELETE on API, admin pages

Anti-cheat (anti-spam):
  - TIER_ANTICHEAT: 30 req/min — /anticheat/ endpoint (real anti-cheat
    fires ~1-5x/exam, 30/min lets legit traffic through)

Static (rarely hits Django — Apache direct serve):
  - TIER_STATIC: 200 req/min — fallback for /static/, /media/

═══════════════════════════════════════════════════════════════════════
IDENTIFIER STRATEGY
═══════════════════════════════════════════════════════════════════════

Authenticated user → f'user:{user.pk}'
  (NAT immune — har user alohida bucket)

Anonymous → f'ip:{client_ip}'
  (shared NAT risk, lekin TIER_BROWSE 120/min generous enough)

Org-level aggregate (B2B fairness — optional, NOT enforced in v1 redesign):
  Authenticated requests can ALSO check `org:{org_id}` bucket via
  check_with_org_aggregate(). Not enabled by default — adds Redis
  round-trip. Enable per-deployment as needed.

═══════════════════════════════════════════════════════════════════════
PROGRESSIVE PENALTY (softer than v1)
═══════════════════════════════════════════════════════════════════════

  1st violation:  1 minute
  2nd violation:  5 minutes
  3rd violation:  30 minutes  (was 60 — too aggressive)
  4th+:           2 hours     (was 24 hours — punitive)

Violation counter has 7-day TTL — repeated bad actors stay penalized.

═══════════════════════════════════════════════════════════════════════
ADMIN BYPASS
═══════════════════════════════════════════════════════════════════════

is_superuser or is_staff → no rate limit applied.

═══════════════════════════════════════════════════════════════════════
KEY SCHEMA
═══════════════════════════════════════════════════════════════════════

  rl:count:{tier}:{ident}     — request counter (TTL = window)
  rl:violations:{ident}       — violation count (TTL = 7 days)
  rl:block:{ident}            — active block (TTL = penalty seconds)
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


# Login — brute force protection (strict)
TIER_LOGIN = RateTier(name='login', limit=5, window=60)

# Browse — read-heavy, NAT-friendly
TIER_BROWSE = RateTier(name='browse', limit=120, window=60)

# API mutation — write operations, default for /api/, /admin/
TIER_API = RateTier(name='api', limit=60, window=60)

# Anti-cheat endpoint — spam guard
TIER_ANTICHEAT = RateTier(name='anticheat', limit=30, window=60)

# Static — fallback (Apache serves directly in production)
TIER_STATIC = RateTier(name='static', limit=200, window=60)


# ── Progressive penalty ladder (softer) ───────────────────────

PENALTY_LADDER = [
    60,  # 1st: 1 minute
    5 * 60,  # 2nd: 5 minutes
    30 * 60,  # 3rd: 30 minutes (was 1 hour)
    2 * 3600,  # 4th+: 2 hours (was 24 hours)
]
VIOLATION_TTL = 7 * 24 * 3600  # 7 days


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
    """Bitta request'ni tekshirish va counter'ni oshirish.

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


def check_with_org_aggregate(
    user_identifier: str, org_identifier: str, tier: RateTier, org_limit: int = 1000
) -> None:
    """Authenticated user + per-org aggregate check (B2B fairness).

    Use case: bir tenant'ning 100 ta user'i jamoasi qancha ko'p so'rov
    yuborsa ham, org aggregate limit (1000/min default) ushlab turadi.
    Boshqa tenant'lar uchun resource starvation'ni oldini oladi.

    Default'da ishlatilmaydi (middleware uchun cost qo'shadi) — services
    yoki cron task'larda explicit chaqirish mumkin.
    """
    check(user_identifier, tier)

    r = _redis()
    org_tier = RateTier(name=f'{tier.name}_org', limit=org_limit, window=60)
    count_key = f'rl:count:{org_tier.name}:{org_identifier}'
    pipe = r.pipeline()
    pipe.incr(count_key)
    pipe.expire(count_key, org_tier.window)
    count, _ = pipe.execute()

    if count > org_tier.limit:
        # Org aggregate over limit — block at org level (not penalize user)
        # User's individual limit still tracked separately
        raise RateLimitExceeded(retry_after=org_tier.window, tier=org_tier.name)


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
            for tier in (TIER_LOGIN, TIER_BROWSE, TIER_API, TIER_ANTICHEAT, TIER_STATIC)
        },
    }
