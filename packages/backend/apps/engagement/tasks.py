"""
Task 5 + Task 9 — Engagement Celery tasks.

Task 5:
  - archive_leaderboards('weekly')    har dushanba 00:05
  - archive_leaderboards('monthly')   har oy 1-kuni 00:10
  - archive_leaderboards('yearly')    har 1-yanvar 00:15

Task 9:
  - check_broken_streaks                   har kecha 00:05 (broken streak warn)
  - leagues_weekly_recalc                  har dushanba 00:10 (promote/demote)
"""

import logging
from datetime import datetime

from celery import shared_task
from django.utils import timezone

from core.locks import single_runner_lock

from . import leaderboard
from .models import LeaderboardSnapshot

logger = logging.getLogger(__name__)

DEFAULT_TOP_N = 1000


# ── Period key builders ───────────────────────────────────────────────────────


def _period_key(period: str, when: datetime | None = None) -> str:
    """
    "weekly" → "2026-W19"
    "monthly" → "2026-05"
    "yearly" → "2026"
    """
    when = when or timezone.now()
    if period == 'weekly':
        iso_year, iso_week, _ = when.isocalendar()
        return f'{iso_year}-W{iso_week:02d}'
    if period == 'monthly':
        return f'{when.year}-{when.month:02d}'
    if period == 'yearly':
        return str(when.year)
    raise ValueError(f'Unknown period: {period}')


# ── Scope discovery ──────────────────────────────────────────────────────────


def _discover_active_scopes(client) -> list[tuple[str, str]]:
    """
    Redis'dagi mavjud lb:* key'larni topib, scope_kind + scope_id list'ini qaytaradi.
    SCAN cursor — production'da KEYS o'rniga ishlatish kerak (bloklamaydi).

    Returns: [('global', ''), ('region', '5'), ('tenant', 'uuid'), ('mock', 'uuid'), ...]
    """
    scopes = []
    for raw_key in client.scan_iter(match='lb:*', count=200):
        # raw_key: "lb:global" yoki "lb:region:5" yoki "lb:tenant:<uuid>"
        parts = raw_key.split(':', 2)
        if len(parts) < 2:
            continue
        kind = parts[1]
        if kind not in ('global', 'region', 'tenant', 'mock'):
            continue
        scope_id = parts[2] if len(parts) > 2 else ''
        scopes.append((kind, scope_id))
    return scopes


# ── Snapshot writer ──────────────────────────────────────────────────────────


def _snapshot_one(
    period: str,
    period_key: str,
    scope_kind: str,
    scope_id: str,
    *,
    top_n: int = DEFAULT_TOP_N,
) -> LeaderboardSnapshot:
    """Bir scope uchun snapshot yaratadi yoki yangilaydi."""
    if scope_kind == 'global':
        scope_key = leaderboard.key_global()
    elif scope_kind == 'region':
        scope_key = leaderboard.key_region(scope_id)
    elif scope_kind == 'tenant':
        scope_key = leaderboard.key_tenant(scope_id)
    elif scope_kind == 'mock':
        scope_key = leaderboard.key_mock(scope_id)
    else:
        raise ValueError(f'Unknown scope_kind: {scope_kind}')

    top_results = leaderboard.top(scope_key, limit=top_n)
    entries = [
        {'user_id': uid, 'score': score, 'rank': idx + 1}
        for idx, (uid, score) in enumerate(top_results)
    ]
    total = leaderboard.total(scope_key)

    snapshot, _created = LeaderboardSnapshot.objects.update_or_create(
        period=period,
        period_key=period_key,
        scope_kind=scope_kind,
        scope_id=scope_id or '',
        defaults={'total': total, 'entries': entries},
    )

    # ISSUE-304: normalized LeaderboardEntry jadvaliga ham yozish.
    # JSON `entries` field back-compat uchun saqlanadi (3 oy), keyin deprecate.
    from .models import LeaderboardEntry

    LeaderboardEntry.objects.filter(snapshot=snapshot).delete()  # replace strategy
    LeaderboardEntry.objects.bulk_create(
        [
            LeaderboardEntry(
                snapshot=snapshot,
                user_id=e['user_id'],
                rank=e['rank'],
                score=e['score'],
            )
            for e in entries
        ],
        batch_size=500,
    )
    return snapshot


# ── Celery tasks ─────────────────────────────────────────────────────────────


@shared_task(name='engagement.archive_leaderboards')
def archive_leaderboards(period: str = 'weekly', top_n: int = DEFAULT_TOP_N) -> dict:
    """
    Beat schedule entry. Joriy vaqtning period_key'i uchun barcha aktiv
    Redis lb:* scope'larini snapshot qiladi.

    Args:
      period: "weekly" / "monthly" / "yearly"
      top_n: snapshotda saqlanadigan top entries soni

    ISSUE-102: distributed lock (TTL 1h) — beat failover'da duplicate snapshot
    bo'lmasligi uchun. Per-period alohida lock (weekly/monthly/yearly parallel).
    """
    if period not in ('weekly', 'monthly', 'yearly'):
        raise ValueError(f'Invalid period: {period}')

    with single_runner_lock(f'archive_leaderboards:{period}', expire=3600) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy', 'period': period}

        period_key = _period_key(period)
        client = leaderboard._redis()
        scopes = _discover_active_scopes(client)

        saved = 0
        for kind, scope_id in scopes:
            try:
                _snapshot_one(period, period_key, kind, scope_id, top_n=top_n)
                saved += 1
            except Exception as e:
                logger.warning(
                    'archive_leaderboards: snapshot %s/%s/%s failed: %s',
                    period_key,
                    kind,
                    scope_id,
                    e,
                )

        logger.info(
            'archive_leaderboards %s/%s: %d/%d scopes archived',
            period,
            period_key,
            saved,
            len(scopes),
        )
        return {
            'period': period,
            'period_key': period_key,
            'scopes_found': len(scopes),
            'snapshots_saved': saved,
        }


# ── Task 9 — Streak + Leagues beat tasks ─────────────────────────────────────


@shared_task(name='engagement.check_broken_streaks')
def check_broken_streaks_task() -> dict:
    """Beat: every night 00:05. Broken streak'larga warning yuboradi.

    ISSUE-102: distributed lock (TTL 30min). Beat failover'da SMS notification
    duplikati bo'lmasligi uchun (bir kun 1 marta yetadi).
    """
    from . import streak_service

    with single_runner_lock('check_broken_streaks', expire=1800) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        queued = streak_service.check_broken_streaks()
        return {'warnings_queued': queued}


@shared_task(name='engagement.leagues_weekly_recalc')
def leagues_weekly_recalc_task() -> dict:
    """Beat: every Monday 00:10. Tugagan haftaning promote/demote.

    ISSUE-102: distributed lock (TTL 1h). Promote rewards Coin wallet'ga
    yozadi — duplikat run 2x reward bersa, foydalanuvchi balansi noto'g'ri.
    """
    from . import leagues_service

    with single_runner_lock('leagues_weekly_recalc', expire=3600) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        return leagues_service.weekly_recalc()
