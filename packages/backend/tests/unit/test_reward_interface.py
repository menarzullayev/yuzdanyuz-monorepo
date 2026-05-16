"""
Unit tests for `core.interfaces.reward` — RewardService Protocol kontrakti.

Coverage (ISSUE-X03):
  - get_reward_service: callable qaytaradi
  - default adapter: wallet + transaction yaratadi
  - settings.REWARD_SERVICE_PATH override: resolved funksiyani almashtiradi
  - streak milestone interface orqali reward chaqiradi
  - league promotion interface orqali reward chaqiradi
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.commerce import wallet_service
from apps.engagement import leagues_service, streak_service
from apps.engagement.models import League, LeagueMembership, UserStreak
from core.interfaces.reward import get_reward_service


@pytest.mark.unit
class TestRewardResolver:
    def test_default_returns_callable(self, db):
        reward = get_reward_service()
        assert callable(reward)
        # Default mapping → wallet_service.award_coins_adapter
        assert reward is wallet_service.award_coins_adapter

    def test_adapter_invokes_wallet_helpers_with_mapped_args(self, db, user):
        """Adapter top_up + get_or_create_wallet ni to'g'ri argument bilan chaqiradi.

        DB transaction layer test_wallet_service'da to'liq qoplanadi —
        bu yerda Protocol → wallet API mapping'ni tekshiramiz.
        """
        reward = get_reward_service()
        with (
            patch('apps.commerce.wallet_service.get_or_create_wallet') as mock_get,
            patch('apps.commerce.wallet_service.top_up') as mock_top_up,
        ):
            mock_get.return_value = object()  # opaque wallet stand-in
            reward(user.pk, 75, reason='Test award', ref_id='unit:1')

        mock_get.assert_called_once()
        called_user = mock_get.call_args.args[0]
        assert called_user.pk == user.pk

        mock_top_up.assert_called_once()
        kwargs = mock_top_up.call_args.kwargs
        assert kwargs.get('coins') == 75
        assert 'Test award' in kwargs.get('description', '')
        assert 'unit:1' in kwargs.get('description', '')

    def test_settings_override_changes_resolved_function(self, db):
        calls = []

        def fake_reward(user_id, amount, *, reason, ref_id=''):
            calls.append((user_id, amount, reason, ref_id))

        # Stick the fake into a module attribute so import_string finds it
        wallet_service._fake_reward_for_test = fake_reward
        try:
            with override_settings(
                REWARD_SERVICE_PATH='apps.commerce.wallet_service._fake_reward_for_test'
            ):
                reward = get_reward_service()
                assert reward is fake_reward
                reward('uid-1', 5, reason='r', ref_id='x')
            assert calls == [('uid-1', 5, 'r', 'x')]
        finally:
            del wallet_service._fake_reward_for_test


@pytest.mark.unit
class TestRewardInterfaceIntegration:
    def test_streak_milestone_uses_reward_interface(self, db, user):
        """Milestone (7-day) get_reward_service() resolver orqali ketishi kerak."""
        yesterday = timezone.now().date() - timedelta(days=1)
        UserStreak.objects.create(
            user=user, current_streak=6, max_streak=6, last_activity_date=yesterday
        )

        captured = {}

        def fake_reward(user_id, amount, *, reason, ref_id=''):
            captured['user_id'] = user_id
            captured['amount'] = amount
            captured['reason'] = reason
            captured['ref_id'] = ref_id

        with patch(
            'core.interfaces.reward.get_reward_service',
            return_value=fake_reward,
        ):
            s = streak_service.update_on_activity(user)

        assert s.current_streak == 7
        assert captured['user_id'] == user.pk
        assert captured['amount'] == 50  # MILESTONE_REWARDS[7]
        assert '7' in captured['reason']
        assert captured['ref_id'] == 'streak:7'

    def test_league_promotion_uses_reward_interface(self, db, user, user2):
        bronze = League.objects.create(
            name='Bronza', slug='bronza', rank_order=1, promote_reward_coins=100
        )
        silver = League.objects.create(
            name='Kumush', slug='kumush', rank_order=2, promote_reward_coins=250
        )
        prev_mon = timezone.now().date() - timedelta(days=timezone.now().date().weekday() + 7)
        # Top user (will be promoted)
        LeagueMembership.objects.create(
            user=user,
            league=bronze,
            period_start=prev_mon,
            period_end=prev_mon + timedelta(days=6),
            points_earned=900,
        )
        # Filler user — keeps user as clear top
        LeagueMembership.objects.create(
            user=user2,
            league=bronze,
            period_start=prev_mon,
            period_end=prev_mon + timedelta(days=6),
            points_earned=10,
        )

        captured = []

        def fake_reward(user_id, amount, *, reason, ref_id=''):
            captured.append({'user_id': user_id, 'amount': amount, 'ref_id': ref_id})

        # silver is the target league for promotion
        with patch(
            'core.interfaces.reward.get_reward_service',
            return_value=fake_reward,
        ):
            result = leagues_service.weekly_recalc()

        assert result['promoted'] >= 1
        # At least one reward dispatched via interface
        assert any(c['amount'] == silver.promote_reward_coins for c in captured)
        assert any(c['user_id'] == user.pk for c in captured)
        assert any('league:kumush' in c['ref_id'] for c in captured)
