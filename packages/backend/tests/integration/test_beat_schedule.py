"""F11: every periodic task the code defines is scheduled, and every scheduled run is single-run.

Before this, only a handful of schedules were registered in code; subscription renewal,
mock publishing, streak warnings, cleanups and the SMS balance check never ran unless
someone created django-celery-beat rows by hand.
"""

import pytest

from core.celery import app

# Every task that is meant to run on a timer. A new periodic task must be added here
# and to core/celery.py's beat_schedule.
PERIODIC_TASKS = {
    'accounts.cleanup_expired_sessions',
    'accounts.cleanup_used_otp_codes',
    'accounts.cleanup_old_webhook_deliveries',
    'accounts.check_sms_balance',
    'analytics.refresh_question_quality',
    'commerce.auto_renew_subscriptions',
    'engagement.archive_leaderboards',
    'engagement.check_broken_streaks',
    'engagement.leagues_weekly_recalc',
    'exams.publish_scheduled_mocks',
    'intelligence.check_ai_token_expiry',
    'ops.cleanup_old_records',
    'ops.run_health_probe',
}


def _scheduled():
    return app.conf.beat_schedule


def test_every_periodic_task_is_scheduled():
    scheduled = {entry['task'] for entry in _scheduled().values()}
    missing = PERIODIC_TASKS - scheduled
    assert not missing, f'periodic tasks without a beat schedule: {sorted(missing)}'


def test_leaderboards_archived_for_every_period():
    periods = set()
    for entry in _scheduled().values():
        if entry['task'] != 'engagement.archive_leaderboards':
            continue
        args = entry.get('args') or ()
        periods.add((entry.get('kwargs') or {}).get('period', args[0] if args else 'weekly'))
    assert periods == {'weekly', 'monthly', 'yearly'}


def test_every_scheduled_task_exists_and_has_a_queue():
    app.loader.import_default_modules()
    routes = app.conf.task_routes
    for name, entry in _scheduled().items():
        assert entry['task'] in app.tasks, f'{name}: unknown task {entry["task"]}'
        assert entry['task'] in routes, f'{name}: {entry["task"]} is not routed to a queue'


class _BusyRedis:
    """Every lock is already held by another runner."""

    def set(self, *args, **kwargs):
        return None


@pytest.mark.django_db
def test_every_scheduled_run_is_single_run(monkeypatch):
    """A second beat (failover, double deploy) must not run the same job twice."""
    from core import locks

    monkeypatch.setattr(locks, '_redis', lambda: _BusyRedis())
    app.loader.import_default_modules()
    for name, entry in _scheduled().items():
        task = app.tasks[entry['task']]
        result = task.run(*entry.get('args', ()), **entry.get('kwargs', {}))
        assert isinstance(result, dict) and result.get('status') == 'skipped_lock_busy', (
            f'{name}: ran without the single-run lock ({result!r})'
        )
