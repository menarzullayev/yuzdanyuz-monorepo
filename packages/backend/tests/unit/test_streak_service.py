"""
Unit tests for `apps.engagement.streak_service`.

Coverage:
  - update_on_activity: new / consecutive / same-day / broken
  - max_streak tracking across resets
  - milestone Coin reward (7-day) via wallet_service stub
  - check_broken_streaks: queues notifications for stale streaks
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from apps.engagement import streak_service
from apps.engagement.models import Notification, UserStreak


@pytest.mark.unit
class TestUpdateOnActivity:
    def test_first_activity_creates_streak_one(self, db, user):
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 1
        assert s.max_streak == 1
        assert s.last_activity_date == timezone.now().date()

    def test_same_day_call_is_idempotent(self, db, user):
        s1 = streak_service.update_on_activity(user)
        s2 = streak_service.update_on_activity(user)
        assert s1.id == s2.id
        assert s2.current_streak == 1

    def test_consecutive_days_increment(self, db, user):
        yesterday = timezone.now().date() - timedelta(days=1)
        streak_service.update_on_activity(user, activity_date=yesterday)
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 2
        assert s.max_streak == 2

    def test_two_day_gap_resets_streak(self, db, user):
        # 3 days ago activity, then today => gap of >1 day → reset
        three_days_ago = timezone.now().date() - timedelta(days=3)
        streak_service.update_on_activity(user, activity_date=three_days_ago)
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 1

    def test_max_streak_preserved_after_reset(self, db, user):
        # Build a 3-day streak
        base = timezone.now().date() - timedelta(days=10)
        for i in range(3):
            streak_service.update_on_activity(user, activity_date=base + timedelta(days=i))
        s = UserStreak.objects.get(user=user)
        assert s.current_streak == 3
        assert s.max_streak == 3

        # Big gap → resets current, but max preserved
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 1
        assert s.max_streak == 3

    def test_explicit_activity_date_used(self, db, user):
        specific = timezone.now().date() - timedelta(days=5)
        s = streak_service.update_on_activity(user, activity_date=specific)
        assert s.last_activity_date == specific

    def test_milestone_triggers_wallet_top_up(self, db, user):
        """7-day milestone should call wallet_service.top_up with 50 coins."""
        # Pre-build a 6-day streak landing yesterday so today hits day 7
        yesterday = timezone.now().date() - timedelta(days=1)
        UserStreak.objects.create(
            user=user, current_streak=6, max_streak=6, last_activity_date=yesterday
        )

        with (
            patch('apps.commerce.wallet_service.top_up') as mock_top_up,
            patch('apps.commerce.wallet_service.get_or_create_wallet') as mock_get_wallet,
        ):
            mock_get_wallet.return_value = MagicMock()
            s = streak_service.update_on_activity(user)

        assert s.current_streak == 7
        mock_get_wallet.assert_called_once_with(user)
        # Second positional/kwarg should be 50 (MILESTONE_REWARDS[7])
        call_args = mock_top_up.call_args
        assert 50 in call_args.args or call_args.kwargs.get('coins') == 50

    def test_non_milestone_day_no_reward(self, db, user):
        """Day 2 should not trigger wallet top-up."""
        yesterday = timezone.now().date() - timedelta(days=1)
        UserStreak.objects.create(
            user=user, current_streak=1, max_streak=1, last_activity_date=yesterday
        )
        with patch('apps.commerce.wallet_service.top_up') as mock_top_up:
            s = streak_service.update_on_activity(user)
        assert s.current_streak == 2
        mock_top_up.assert_not_called()

    def test_wallet_failure_does_not_break_streak(self, db, user):
        """Reward failure should be logged and swallowed."""
        yesterday = timezone.now().date() - timedelta(days=1)
        UserStreak.objects.create(
            user=user, current_streak=6, max_streak=6, last_activity_date=yesterday
        )
        with patch(
            'apps.commerce.wallet_service.get_or_create_wallet',
            side_effect=RuntimeError('wallet down'),
        ):
            s = streak_service.update_on_activity(user)
        # streak still updated despite wallet failure
        assert s.current_streak == 7


@pytest.mark.unit
class TestCheckBrokenStreaks:
    def test_no_streaks_returns_zero(self, db):
        assert streak_service.check_broken_streaks() == 0

    def test_warns_user_with_stale_activity(self, db, user):
        # last_activity = 3 days ago, current_streak = 5 → broken
        UserStreak.objects.create(
            user=user,
            current_streak=5,
            max_streak=5,
            last_activity_date=timezone.now().date() - timedelta(days=3),
        )
        queued = streak_service.check_broken_streaks()
        assert queued == 1
        assert Notification.objects.filter(user=user, priority='important').exists()

    def test_skips_users_active_yesterday(self, db, user):
        # last_activity = yesterday → not broken (still on the chain edge)
        UserStreak.objects.create(
            user=user,
            current_streak=3,
            max_streak=3,
            last_activity_date=timezone.now().date() - timedelta(days=1),
        )
        assert streak_service.check_broken_streaks() == 0
        assert not Notification.objects.filter(user=user).exists()

    def test_skips_users_with_zero_streak(self, db, user):
        UserStreak.objects.create(
            user=user,
            current_streak=0,
            max_streak=0,
            last_activity_date=timezone.now().date() - timedelta(days=10),
        )
        assert streak_service.check_broken_streaks() == 0

    def test_notification_metadata_includes_days(self, db, user):
        UserStreak.objects.create(
            user=user,
            current_streak=12,
            max_streak=12,
            last_activity_date=timezone.now().date() - timedelta(days=2),
        )
        streak_service.check_broken_streaks()
        n = Notification.objects.get(user=user)
        assert n.metadata.get('streak_days') == 12
        assert '12' in n.body

    def test_notification_queue_failure_logged_not_raised(self, db, user):
        UserStreak.objects.create(
            user=user,
            current_streak=2,
            max_streak=2,
            last_activity_date=timezone.now().date() - timedelta(days=2),
        )
        with patch(
            'apps.engagement.notifications_service.queue',
            side_effect=RuntimeError('redis down'),
        ):
            # Should not raise; returns 0 successful queues
            queued = streak_service.check_broken_streaks()
        assert queued == 0
