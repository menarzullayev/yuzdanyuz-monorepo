"""
Task 9 — Streak service.

API:
  - update_on_activity(user, activity_date=None) — har faollik kuni chaqiriladi.
    Streak'ni yangilaydi (consecutive yoki broken).
  - check_broken_streaks() — Celery beat task: yesterday'dan keyin faollik
    bo'lmagan user'lar streak'ini reset + Telegram notification.

Milestone rewards (Coin):
  7 kun → 50 Coin
  30 kun → 200 Coin
  100 kun → 1000 Coin
"""

import logging
from datetime import timedelta

from django.utils import timezone

from .models import UserStreak

logger = logging.getLogger(__name__)

# TODO ISSUE-404: migrate to get_org_setting(user.organization, 'rewards.streak_milestone_coins')
MILESTONE_REWARDS = {7: 50, 30: 200, 100: 1000}


def update_on_activity(user, activity_date=None) -> UserStreak:
    """
    User faolligi (mock submit, practice answer, etc.) bo'lganda chaqiriladi.

    Logic:
      - Streak yo'q bo'lsa → boshlash (current=1)
      - Bugungi kunda allaqachon faol → no-op
      - Kechagi faollik bilan ketma-ket → current += 1
      - >1 kun gap → reset (current=1, oldingi max saqlanadi)

    Milestone (7/30/100) → Wallet'ga Coin reward (idempotent: faqat shu kun
    yangi qiymat bo'lsa beriladi).
    """
    today = activity_date or timezone.now().date()
    streak, created = UserStreak.objects.get_or_create(user=user)

    awarded_milestone = False
    if created or streak.last_activity_date is None:
        streak.current_streak = 1
        awarded_milestone = streak.current_streak in MILESTONE_REWARDS
    elif streak.last_activity_date == today:
        return streak  # already counted today
    elif streak.last_activity_date == today - timedelta(days=1):
        streak.current_streak += 1
        awarded_milestone = streak.current_streak in MILESTONE_REWARDS
    else:
        # Broken
        streak.current_streak = 1

    if streak.current_streak > streak.max_streak:
        streak.max_streak = streak.current_streak

    streak.last_activity_date = today
    streak.save(update_fields=['current_streak', 'max_streak', 'last_activity_date', 'updated_at'])

    if awarded_milestone:
        try:
            from core.interfaces.reward import get_reward_service

            reward = get_reward_service()
            coins = MILESTONE_REWARDS[streak.current_streak]
            reward(
                user.pk,
                coins,
                reason=f'Streak milestone {streak.current_streak} days',
                ref_id=f'streak:{streak.current_streak}',
            )
        except Exception as e:
            logger.warning('streak milestone Coin reward failed: %s', e)

    return streak


def check_broken_streaks() -> int:
    """
    Celery beat: every night 00:05. Find streaks where last_activity < yesterday
    and current_streak >= 1 → notify + (caller decides whether to reset).
    Returns: number of warnings queued.
    """
    from . import notifications_service

    yesterday = timezone.now().date() - timedelta(days=1)
    broken_qs = UserStreak.objects.filter(
        last_activity_date__lt=yesterday, current_streak__gte=1
    ).select_related('user')

    queued = 0
    for streak in broken_qs:
        try:
            notifications_service.queue(
                user=streak.user,
                title='Streak xavfda!',
                body=(
                    f"{streak.current_streak} kunlik streak'ingiz uziladi! "
                    "Bugun bitta test ish ko'r va davom et."
                ),
                priority='important',
                metadata={'streak_days': streak.current_streak},
            )
            queued += 1
        except Exception as e:
            logger.warning('check_broken_streaks queue failed for %s: %s', streak.user, e)

    return queued
