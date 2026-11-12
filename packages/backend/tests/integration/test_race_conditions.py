"""E08: concurrent requests against real Postgres (threads, one connection each).

The read-modify-write paths are slowed down on purpose so every thread reads the
same starting state; without row locks they all write the same value back.
"""

import threading
import time
from datetime import timedelta

import pytest
from django.db import connection
from django.db.models.query import QuerySet
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.engagement import streak_service
from apps.engagement.models import UserStreak
from apps.exams.models import AntiCheatEvent, ExamAttempt
from core.tenant import unscoped_context
from tests.integration.test_exam_api import _make_published_mock, _make_qv

THREADS = 6
_rewards = []
_rewards_lock = threading.Lock()


def record_reward(user_id, amount, *, reason, ref_id=''):
    with _rewards_lock:
        _rewards.append((user_id, amount, ref_id))


def _run_in_threads(fn, n=THREADS):
    barrier = threading.Barrier(n)
    errors = []

    def worker():
        try:
            barrier.wait()
            fn()
        except Exception as e:  # surfaced below
            errors.append(e)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors, errors


def _slow(original, delay=0.3):
    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(delay)
        return result

    return wrapper


@pytest.mark.django_db(transaction=True)
def test_parallel_anticheat_reports_count_every_strike(org, user, member, subject, monkeypatch):
    org.settings = {'anti_cheat': {'max_strikes': 100}}
    org.save(update_fields=['settings'])
    mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])

    client = Client()
    client.force_login(user)
    attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt']['id']
    url = reverse('exams:attempt-anticheat', args=[attempt_id])
    # widen the window between reading `strikes` and writing it back
    monkeypatch.setattr(AntiCheatEvent.objects, 'create', _slow(AntiCheatEvent.objects.create))

    def report():
        c = Client()
        c.force_login(user)
        resp = c.post(url, data={'event_type': 'tab_switch'}, content_type='application/json')
        assert resp.status_code == 200, resp.content

    _run_in_threads(report)

    with unscoped_context():
        attempt = ExamAttempt.objects.get(pk=attempt_id)
        assert AntiCheatEvent.objects.filter(attempt=attempt).count() == THREADS
        assert attempt.strikes == THREADS


@pytest.mark.django_db(transaction=True)
def test_parallel_anticheat_cancels_exactly_at_the_limit(org, user, member, subject):
    mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
    client = Client()
    client.force_login(user)
    attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt']['id']
    url = reverse('exams:attempt-anticheat', args=[attempt_id])
    statuses = []

    def report():
        c = Client()
        c.force_login(user)
        statuses.append(
            c.post(url, data={'event_type': 'tab_switch'}, content_type='application/json')
        )

    _run_in_threads(report)
    with unscoped_context():
        attempt = ExamAttempt.objects.get(pk=attempt_id)
    assert attempt.status == ExamAttempt.Status.CANCELLED
    # default max is 3: later reports see the cancelled attempt instead of adding strikes
    assert attempt.strikes == 3
    assert sum(r.status_code == 200 for r in statuses) == 3


@pytest.mark.django_db(transaction=True)
def test_parallel_activity_awards_a_streak_milestone_once(user, settings, monkeypatch):
    settings.REWARD_SERVICE_PATH = 'tests.integration.test_race_conditions.record_reward'
    _rewards.clear()
    today = timezone.now().date()
    UserStreak.objects.create(
        user=user, current_streak=6, max_streak=6, last_activity_date=today - timedelta(days=1)
    )
    # every thread reads the streak (6, yesterday) before any of them writes 7
    monkeypatch.setattr(QuerySet, 'get_or_create', _slow(QuerySet.get_or_create))

    _run_in_threads(lambda: streak_service.update_on_activity(user, activity_date=today))

    streak = UserStreak.objects.get(user=user)
    assert streak.current_streak == 7
    assert streak.last_activity_date == today
    assert _rewards == [(user.pk, 50, 'streak:7')]
