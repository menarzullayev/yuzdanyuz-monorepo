"""
ISSUE-102 — Distributed lock tests.

Scheduled task'lar concurrent fire'da (beat failover, manual trigger)
duplikat ishlamasligini tasdiqlash.

Test strategiyasi: bitta task'ni 2 marta apply qilamiz (eager mode), ikkinchisi
"skipped_lock_busy" qaytarishi kerak.
"""

from unittest.mock import patch

import pytest

from core.locks import single_runner_lock


@pytest.mark.integration
class TestSingleRunnerLock:
    def test_acquire_first_caller(self):
        """Birinchi caller lock oladi."""
        with single_runner_lock('test_lock_1', expire=10) as acquired:
            assert acquired is True

    def test_second_concurrent_caller_blocked(self):
        """Lock band bo'lsa, ikkinchi caller False oladi (block emas)."""
        with single_runner_lock('test_lock_2', expire=10) as outer:
            assert outer is True
            # Nested call — boshqa "process" simulyatsiyasi
            with single_runner_lock('test_lock_2', expire=10) as inner:
                assert inner is False

    def test_lock_released_after_context_exit(self):
        """Context exit lock'ni ozod qiladi — keyingi caller oladi."""
        with single_runner_lock('test_lock_3', expire=10) as first:
            assert first is True
        with single_runner_lock('test_lock_3', expire=10) as second:
            assert second is True


@pytest.mark.integration
class TestScheduledTaskIdempotency:
    """Scheduled task'lar concurrent fire'da skip qilishi tasdiqlash."""

    def test_check_broken_streaks_skips_on_busy_lock(self, db):
        from apps.engagement.tasks import check_broken_streaks_task

        # Lock'ni qo'lda oldindan band qilamiz
        with single_runner_lock('check_broken_streaks', expire=10) as acquired:
            assert acquired
            # Task chaqirilsa, lock band ekanini sezishi va skip qilishi kerak
            result = check_broken_streaks_task()
            assert result == {'status': 'skipped_lock_busy'}

    def test_leagues_weekly_recalc_skips_on_busy_lock(self, db):
        from apps.engagement.tasks import leagues_weekly_recalc_task

        with single_runner_lock('leagues_weekly_recalc', expire=10) as acquired:
            assert acquired
            result = leagues_weekly_recalc_task()
            assert result == {'status': 'skipped_lock_busy'}

    def test_auto_renew_subscriptions_skips_on_busy_lock(self, db):
        from apps.commerce.tasks import auto_renew_subscriptions

        with single_runner_lock('auto_renew_subscriptions', expire=10) as acquired:
            assert acquired
            result = auto_renew_subscriptions()
            assert result == {'status': 'skipped_lock_busy'}

    def test_archive_leaderboards_skips_per_period(self, db):
        """Per-period alohida lock — weekly band bo'lsa, monthly parallel ishlaydi."""
        from apps.engagement.tasks import archive_leaderboards

        with single_runner_lock('archive_leaderboards:weekly', expire=10) as acquired:
            assert acquired
            result_weekly = archive_leaderboards('weekly')
            assert result_weekly == {'status': 'skipped_lock_busy', 'period': 'weekly'}
            # Monthly hali ham ishlay oladi (alohida lock key)
            result_monthly = archive_leaderboards('monthly')
            assert 'period' in result_monthly
            assert result_monthly.get('period') == 'monthly'
            assert 'snapshots_saved' in result_monthly  # ishladi
