"""
Task 5 — Leaderboard archive (Celery + history API) tests.

Tekshiriladi:
  - period_key formatlari (weekly/monthly/yearly)
  - _discover_active_scopes Redis SCAN orqali to'g'ri topadi
  - archive_leaderboards bir scope uchun snapshot yaratadi
  - Idempotent: ikkinchi run update qiladi (duplicate yo'q)
  - GET /api/leaderboard/history/ snapshot qaytaradi
  - Yo'q period_key → 404
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from apps.engagement import leaderboard
from apps.engagement.models import LeaderboardSnapshot
from apps.engagement.tasks import (
    _period_key,
    archive_leaderboards,
)

# ─── period_key format tests ─────────────────────────────────────────────────


@pytest.mark.unit
class TestPeriodKey:
    def test_weekly_iso_format(self):
        # 2026-05-11 = Monday, ISO week 20
        when = datetime(2026, 5, 11, 12, 0, tzinfo=ZoneInfo('UTC'))
        assert _period_key('weekly', when) == '2026-W20'

    def test_monthly_format(self):
        when = datetime(2026, 3, 15, 0, 0, tzinfo=ZoneInfo('UTC'))
        assert _period_key('monthly', when) == '2026-03'

    def test_yearly_format(self):
        when = datetime(2026, 12, 31, 0, 0, tzinfo=ZoneInfo('UTC'))
        assert _period_key('yearly', when) == '2026'

    def test_unknown_period_raises(self):
        with pytest.raises(ValueError):
            _period_key('hourly')


# ─── archive_leaderboards Celery task ────────────────────────────────────────


@pytest.mark.integration
class TestArchiveLeaderboards:
    def test_archives_global_scope(self, db, org, user, user2):
        # Redis ZSET'ga ma'lumot qo'shish
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m1', org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=85, mock_id='m1', org_id=org.id)

        result = archive_leaderboards(period='weekly')

        assert result['period'] == 'weekly'
        assert result['snapshots_saved'] >= 1  # global + mock + tenant

        # Global snapshot tekshirish
        snap = LeaderboardSnapshot.objects.get(period='weekly', scope_kind='global', scope_id='')
        assert snap.total == 2
        # Birinchi - eng yuqori (user2)
        assert snap.entries[0]['user_id'] == str(user2.id)
        assert snap.entries[0]['score'] == 85.0
        assert snap.entries[0]['rank'] == 1

    def test_archives_multiple_scopes(self, db, org, user):
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id='m1', org_id=org.id)

        archive_leaderboards(period='weekly')

        # Global + mock + tenant snapshot — kamida 3 ta
        snaps = LeaderboardSnapshot.objects.filter(period='weekly')
        kinds = set(snaps.values_list('scope_kind', flat=True))
        assert 'global' in kinds
        assert 'mock' in kinds
        assert 'tenant' in kinds

    def test_idempotent_second_run_updates(self, db, org, user):
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m', org_id=org.id)
        archive_leaderboards(period='weekly')
        first_count = LeaderboardSnapshot.objects.count()

        # Yana score qo'shish va qayta archive
        leaderboard.record_attempt(user_id=user.id, score=95, mock_id='m', org_id=org.id)
        archive_leaderboards(period='weekly')
        second_count = LeaderboardSnapshot.objects.count()

        # Yangi snapshots qo'shilmaydi (idempotent)
        assert first_count == second_count
        # Lekin global snapshot yangilangan (best=95)
        snap = LeaderboardSnapshot.objects.get(period='weekly', scope_kind='global', scope_id='')
        assert snap.entries[0]['score'] == 95.0

    def test_invalid_period_raises(self, db):
        with pytest.raises(ValueError):
            archive_leaderboards(period='daily')


# ─── REST API: GET /api/leaderboard/history/ ─────────────────────────────────


@pytest.mark.integration
class TestLeaderboardHistoryAPI:
    def test_get_latest_snapshot(self, db, org, user, user2, member):
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id='m', org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=90, mock_id='m', org_id=org.id)
        archive_leaderboards(period='weekly')

        client = Client()
        client.force_login(user)
        resp = client.get(
            reverse('engagement:lb-history'),
            {'period': 'weekly', 'scope_kind': 'global'},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['period'] == 'weekly'
        assert data['scope_kind'] == 'global'
        assert data['total'] == 2
        assert data['entries'][0]['user_id'] == str(user2.id)

    def test_get_specific_period_key(self, db, org, user, member):
        leaderboard.record_attempt(user_id=user.id, score=75, mock_id='m', org_id=org.id)
        archive_leaderboards(period='weekly')

        client = Client()
        client.force_login(user)
        # period_key ni real snapshot'dan olamiz
        snap = LeaderboardSnapshot.objects.filter(scope_kind='global').first()

        resp = client.get(
            reverse('engagement:lb-history'),
            {
                'period': 'weekly',
                'scope_kind': 'global',
                'period_key': snap.period_key,
            },
        )
        assert resp.status_code == 200
        assert resp.json()['period_key'] == snap.period_key

    def test_404_when_no_snapshot(self, db, user, member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(
            reverse('engagement:lb-history'),
            {'period': 'weekly', 'scope_kind': 'global', 'period_key': '2099-W01'},
        )
        assert resp.status_code == 404

    def test_invalid_period_400(self, db, user, member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(
            reverse('engagement:lb-history'),
            {'period': 'daily', 'scope_kind': 'global'},
        )
        assert resp.status_code == 400

    def test_invalid_scope_kind_400(self, db, user, member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(
            reverse('engagement:lb-history'),
            {'period': 'weekly', 'scope_kind': 'platform'},
        )
        assert resp.status_code == 400
