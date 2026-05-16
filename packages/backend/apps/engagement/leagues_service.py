"""
Task 9 — Leagues service.

Pattern (Duolingo style race):
  - Bir hafta — bir LeagueMembership per user
  - User mock submit qilganda, points_earned += score
  - Hafta oxirida (Sunday 23:59) Celery task:
    * Top 10 → next_league + Coin reward
    * Bottom 10 → previous_league
    * Yangi week uchun fresh memberships create

API:
  - get_or_create_membership(user, week_start) → LeagueMembership
  - add_points(user, points) — har activity'da chaqiriladi
  - weekly_recalc() — Celery beat task
"""

import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from .models import League, LeagueMembership

logger = logging.getLogger(__name__)

PROMOTE_TOP = 10
DEMOTE_BOTTOM = 10


# ── Period helpers ────────────────────────────────────────────────────────────


def _current_week_bounds() -> tuple[date, date]:
    today = timezone.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _previous_week_bounds() -> tuple[date, date]:
    monday, sunday = _current_week_bounds()
    return monday - timedelta(days=7), sunday - timedelta(days=7)


def _bronze_league() -> League | None:
    """Default starting league (rank_order=1) — yangi user uchun."""
    return League.objects.filter(rank_order=1).first()


# ── User membership flow ──────────────────────────────────────────────────────


def get_or_create_membership(user, *, week_start=None) -> LeagueMembership | None:
    """
    User'ning joriy hafta membership'ini topish yoki yaratish.
    League yo'q bo'lsa (yangi user) — bronze ligaga qo'shadi.
    """
    if week_start is None:
        week_start, _ = _current_week_bounds()
    week_end = week_start + timedelta(days=6)

    membership = LeagueMembership.objects.filter(user=user, period_start=week_start).first()
    if membership:
        return membership

    # Avvalgi hafta promotion'ini hisobga olish
    prev_membership = LeagueMembership.objects.filter(user=user).order_by('-period_start').first()
    if prev_membership:
        league = (
            prev_membership.next_league if prev_membership.next_league else prev_membership.league
        )
    else:
        league = _bronze_league()

    if league is None:
        return None  # League catalog bo'sh

    return LeagueMembership.objects.create(
        user=user,
        league=league,
        period_start=week_start,
        period_end=week_end,
        points_earned=0,
    )


def add_points(user, points: int) -> LeagueMembership | None:
    """
    User'ga joriy hafta membership'iga points qo'shish.
    Membership yo'q bo'lsa avtomat yaratiladi.
    """
    if points <= 0:
        return None
    membership = get_or_create_membership(user)
    if membership is None:
        return None
    LeagueMembership.objects.filter(pk=membership.pk).update(
        points_earned=models_F('points_earned') + points
    )
    membership.refresh_from_db(fields=['points_earned'])
    return membership


def models_F(field):
    """Lazy F() import (avoid top-level Django dep ordering)."""
    from django.db.models import F

    return F(field)


# ── Weekly recalc (Celery beat) ──────────────────────────────────────────────


@transaction.atomic
def weekly_recalc() -> dict:
    """
    Sunday 23:59 da chaqiriladi. Tugagan haftaning memberships'larini ko'rib:
      - Top PROMOTE_TOP → next_league + Coin reward
      - Bottom DEMOTE_BOTTOM → prev_league

    next_league va prev_league rank_order +1/-1 orqali topiladi.
    Yangi hafta uchun membershipslar yaratilmaydi (lazy: get_or_create_membership
    birinchi activity'da chaqiriladi).
    """
    from apps.commerce import wallet_service

    week_start, _ = _previous_week_bounds()
    leagues = list(League.objects.order_by('rank_order'))
    if not leagues:
        return {'promoted': 0, 'demoted': 0, 'leagues_processed': 0}

    promoted_total = 0
    demoted_total = 0

    for league in leagues:
        memberships_qs = LeagueMembership.objects.filter(
            league=league, period_start=week_start
        ).order_by('-points_earned')

        next_league = next((lg for lg in leagues if lg.rank_order == league.rank_order + 1), None)
        prev_league = next((lg for lg in leagues if lg.rank_order == league.rank_order - 1), None)

        # Top promote
        if next_league:
            top_memberships = list(memberships_qs[:PROMOTE_TOP])
            for m in top_memberships:
                m.promoted = True
                m.next_league = next_league
                m.save(update_fields=['promoted', 'next_league'])
                # Coin reward
                try:
                    wallet = wallet_service.get_or_create_wallet(m.user)
                    wallet_service.top_up(
                        wallet,
                        next_league.promote_reward_coins,
                        description=f'Promoted to {next_league.name}',
                    )
                except Exception as e:
                    logger.warning('promote reward failed for %s: %s', m.user, e)
                promoted_total += 1

        # Bottom demote (skip top'da bo'lganlarni)
        if prev_league:
            promoted_ids = {m.id for m in memberships_qs[:PROMOTE_TOP]} if next_league else set()
            bottom_memberships = list(memberships_qs.order_by('points_earned')[:DEMOTE_BOTTOM])
            for m in bottom_memberships:
                if m.id in promoted_ids:
                    continue  # ehtimol kichik liga'da kam user
                m.demoted = True
                m.next_league = prev_league
                m.save(update_fields=['demoted', 'next_league'])
                demoted_total += 1

    logger.info(
        'leagues weekly_recalc %s: %d promoted, %d demoted',
        week_start,
        promoted_total,
        demoted_total,
    )
    return {
        'period_start': week_start.isoformat(),
        'promoted': promoted_total,
        'demoted': demoted_total,
        'leagues_processed': len(leagues),
    }
