"""
Task 5 — Redis Sorted Set Leaderboard service.

Architecture (docs/milliy_sertifikat_django.md, Bosqich 10):
  Redis ZSET — gaming industry standarti, PostgreSQL'ni yuklama ostida qoldirmaydi.
  ZREVRANK O(log N) → 50,000+ concurrent user uchun ham instant ranking.

ZSET key layout:
  lb:mock:<mock_id>          — per-mock leaderboard (score = attempt.score)
  lb:global                   — best score across all mocks (per user)
  lb:region:<region_id>       — best score, filtered to region members
  lb:tenant:<org_id>          — best score, filtered to org members

Member: str(user_pk)  |  Score: float (0.00 — 100.00)

Update flow (apps/exams/signals.py orqali):
  ExamAttempt.SUBMITTED + score not None → record_attempt(...)

Read flow (apps/engagement/views.py):
  top(scope) → ZREVRANGE WITHSCORES
  rank(user, scope) → ZREVRANK
  score(user, scope) → ZSCORE
"""

import logging

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
            decode_responses=True,
        )
    return _redis_client


# ── Key builders ──────────────────────────────────────────────────────────────


def key_mock(mock_id) -> str:
    return f'lb:mock:{mock_id}'


def key_global() -> str:
    return 'lb:global'


def key_region(region_id) -> str:
    return f'lb:region:{region_id}'


def key_tenant(org_id) -> str:
    return f'lb:tenant:{org_id}'


# ── Write API ────────────────────────────────────────────────────────────────


def record_attempt(user_id, score: float, *, mock_id, region_id=None, org_id=None) -> None:
    """
    ExamAttempt SUBMITTED bo'lganda chaqiriladi.

    - Mock-specific ZSET: aynan shu attempt score
    - Global/region/tenant: faqat agar yangi score eski max'dan katta bo'lsa update
    """
    if score is None:
        logger.debug('record_attempt: score=None, skipping')
        return

    user_key = str(user_id)
    score = float(score)

    try:
        client = _redis()
        # Mock-specific: har doim aynan shu attempt'ning score'ini yozamiz
        client.zadd(key_mock(mock_id), {user_key: score})

        # Global/region/tenant: best-of (GT modifier — Redis 6.2+)
        # Fakeredis compat: agar GT qo'llab-quvvatlanmasa, manual compare-and-swap
        _zadd_max(client, key_global(), user_key, score)
        if region_id is not None:
            _zadd_max(client, key_region(region_id), user_key, score)
        if org_id is not None:
            _zadd_max(client, key_tenant(org_id), user_key, score)

    except redis.RedisError as e:
        logger.warning('leaderboard record_attempt failed: %s', e)
        # Leaderboard fail = analytics layer fail. Asosiy flow buzilmaydi.


def _zadd_max(client: redis.Redis, key: str, member: str, score: float) -> None:
    """
    GT semantics: faqat yangi score eski'dan katta bo'lsa update.
    Redis 6.2+ ZADD GT — fakeredis ham qo'llab-quvvatlaydi (ko'p versiya).
    Fallback: ZSCORE + conditional ZADD.
    """
    try:
        client.zadd(key, {member: score}, gt=True)
    except (redis.ResponseError, TypeError):
        # Fallback: read-modify-write
        existing = client.zscore(key, member)
        if existing is None or score > existing:
            client.zadd(key, {member: score})


# ── Read API ─────────────────────────────────────────────────────────────────


def top(scope_key: str, limit: int = 100) -> list[tuple[str, float]]:
    """
    Top-N scorers. Returns [(user_id_str, score), ...] sorted by score desc.
    """
    try:
        results = _redis().zrevrange(scope_key, 0, limit - 1, withscores=True)
        return [(member, float(score)) for member, score in results]
    except redis.RedisError as e:
        logger.warning('leaderboard top failed: %s', e)
        return []


def rank(scope_key: str, user_id) -> int | None:
    """
    User'ning rank'i (0-indexed). None bo'lsa ZSET'da yo'q.
    Frontend'da +1 qo'shish kerak (#1, #2, ...).
    """
    try:
        return _redis().zrevrank(scope_key, str(user_id))
    except redis.RedisError as e:
        logger.warning('leaderboard rank failed: %s', e)
        return None


def score_of(scope_key: str, user_id) -> float | None:
    try:
        s = _redis().zscore(scope_key, str(user_id))
        return float(s) if s is not None else None
    except redis.RedisError as e:
        logger.warning('leaderboard score_of failed: %s', e)
        return None


def total(scope_key: str) -> int:
    try:
        return _redis().zcard(scope_key)
    except redis.RedisError as e:
        logger.warning('leaderboard total failed: %s', e)
        return 0


# ── Maintenance ──────────────────────────────────────────────────────────────


def reset(scope_key: str) -> None:
    """Bir scope'ni butunlay tozalash (test'lar yoki manual admin uchun)."""
    try:
        _redis().delete(scope_key)
    except redis.RedisError as e:
        logger.warning('leaderboard reset failed: %s', e)
