"""
Task 9 — Streak + Leagues + Notifications + Search + SEO integration tests.

Tekshiriladi:
  - Streak update_on_activity: yangi/consecutive/broken
  - Streak milestone Coin reward (7, 30, 100)
  - check_broken_streaks task → notification queue
  - Leagues get_or_create_membership + add_points
  - Leagues weekly_recalc: top promote, bottom demote, Coin reward
  - Notifications queue + fan_out (TG stub, SMS stub)
  - Notifications mark as read
  - Search PG FTS fallback (stub mode)
  - SEO sitemap.xml endpoint
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.commerce import wallet_service
from apps.engagement import (
    leagues_service,
    notifications_service,
    search_service,
    streak_service,
)
from apps.engagement.models import (
    League,
    LeagueMembership,
    Notification,
    UserStreak,
)
from apps.engagement.tasks import (
    check_broken_streaks_task,
    leagues_weekly_recalc_task,
)
from core.tenant import tenant_context

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def league_catalog(db):
    """4 ta liga: Bronza, Kumush, Oltin, Olmos."""
    leagues = []
    for rank, name, color in [
        (1, 'Bronza', '#CD7F32'),
        (2, 'Kumush', '#C0C0C0'),
        (3, 'Oltin', '#FFD700'),
        (4, 'Olmos', '#B9F2FF'),
    ]:
        leagues.append(
            League.objects.create(
                name=name,
                slug=name.lower(),
                rank_order=rank,
                color_hex=color,
                promote_reward_coins=100,
            )
        )
    return leagues


# ─── Streak ──────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestStreak:
    def test_first_activity_starts_streak(self, db, user):
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 1
        assert s.max_streak == 1

    def test_consecutive_day_increments(self, db, user):
        yesterday = timezone.now().date() - timedelta(days=1)
        streak_service.update_on_activity(user, activity_date=yesterday)
        s = streak_service.update_on_activity(user)  # bugun
        assert s.current_streak == 2
        assert s.max_streak == 2

    def test_same_day_no_double_count(self, db, user):
        streak_service.update_on_activity(user)
        s = streak_service.update_on_activity(user)
        assert s.current_streak == 1

    def test_broken_streak_resets(self, db, user):
        three_days_ago = timezone.now().date() - timedelta(days=3)
        streak_service.update_on_activity(user, activity_date=three_days_ago)
        s = streak_service.update_on_activity(user)  # bugun, gap 3 kun
        assert s.current_streak == 1
        assert s.max_streak == 1  # 1 day was the original streak

    def test_milestone_7_days_awards_coin(self, db, user):
        # Manually build 7-day streak
        for i in range(6, -1, -1):
            d = timezone.now().date() - timedelta(days=i)
            streak_service.update_on_activity(user, activity_date=d)
        s = UserStreak.objects.get(user=user)
        assert s.current_streak == 7

        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        # Milestone 7 → 50 Coin
        assert wallet.balance_coins >= 50

    def test_check_broken_streaks_queues_notification(self, db, user):
        # User'ning oxirgi faolligi 3 kun oldin
        streak_service.update_on_activity(
            user, activity_date=timezone.now().date() - timedelta(days=3)
        )
        result = check_broken_streaks_task()
        assert result['warnings_queued'] >= 1
        # Notification queued
        assert Notification.objects.filter(user=user, priority='important').exists()


# ─── Leagues ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestLeagues:
    def test_get_or_create_membership_starts_in_bronze(self, db, user, league_catalog):
        m = leagues_service.get_or_create_membership(user)
        assert m.league.rank_order == 1  # Bronze
        assert m.points_earned == 0

    def test_idempotent_membership_per_week(self, db, user, league_catalog):
        m1 = leagues_service.get_or_create_membership(user)
        m2 = leagues_service.get_or_create_membership(user)
        assert m1.id == m2.id

    def test_add_points_increments(self, db, user, league_catalog):
        leagues_service.add_points(user, 50)
        leagues_service.add_points(user, 30)
        m = LeagueMembership.objects.get(user=user)
        assert m.points_earned == 80

    def test_weekly_recalc_promotes_top(self, db, user, user2, league_catalog):
        # Avvalgi haftaning membership'larini yaratish
        prev_week_start = timezone.now().date() - timedelta(
            days=timezone.now().date().weekday() + 7
        )
        prev_week_end = prev_week_start + timedelta(days=6)

        bronze = league_catalog[0]
        LeagueMembership.objects.create(
            user=user,
            league=bronze,
            period_start=prev_week_start,
            period_end=prev_week_end,
            points_earned=500,
        )
        LeagueMembership.objects.create(
            user=user2,
            league=bronze,
            period_start=prev_week_start,
            period_end=prev_week_end,
            points_earned=10,
        )

        result = leagues_weekly_recalc_task()
        assert result['promoted'] >= 1

        # Top user (500 points) promoted
        m1 = LeagueMembership.objects.get(user=user, period_start=prev_week_start)
        assert m1.promoted is True
        assert m1.next_league.rank_order == 2  # Kumush

        # Coin reward
        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        assert wallet.balance_coins >= 100  # promote_reward_coins


# ─── Notifications ───────────────────────────────────────────────────────────


@pytest.mark.integration
class TestNotifications:
    def test_queue_creates_notification(self, db, user):
        n = notifications_service.queue(user=user, title='Test', body='Hello', priority='normal')
        # Eager Celery → already SENT
        n.refresh_from_db()
        assert n.status == Notification.Status.SENT
        assert n.title == 'Test'

    def test_high_priority_notification(self, db, user):
        n = notifications_service.queue(user=user, title='Urgent', body='!', priority='urgent')
        n.refresh_from_db()
        assert n.priority == 'urgent'

    def test_mark_as_read(self, db, user, member):
        n = notifications_service.queue(user=user, title='X', body='Y')
        client = Client()
        client.force_login(user)
        resp = client.post(reverse('engagement:notification-read', args=[n.id]))
        assert resp.status_code == 200
        n.refresh_from_db()
        assert n.status == Notification.Status.READ
        assert n.read_at is not None

    def test_list_endpoint(self, db, user, member):
        notifications_service.queue(user=user, title='A', body='B')
        notifications_service.queue(user=user, title='C', body='D')

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:notifications'))
        assert resp.status_code == 200
        data = resp.json()
        assert data['count'] == 2

    def test_unread_filter(self, db, user, member):
        n1 = notifications_service.queue(user=user, title='A', body='B')
        notifications_service.queue(user=user, title='C', body='D')
        # Mark first as read
        n1.refresh_from_db()
        n1.status = Notification.Status.READ
        n1.save()

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:notifications'), {'unread': '1'})
        assert resp.json()['count'] == 1


# ─── Search (Meilisearch stub / PG FTS fallback) ─────────────────────────────


@pytest.mark.integration
class TestSearch:
    def test_search_finds_question_by_text(self, db, org, subject, member, user):
        with tenant_context(org):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 'fizika qonunlari haqida savol'},
                options=[],
            )

        results = search_service.search_questions('fizika', org=org, limit=10)
        assert len(results) >= 1

    def test_search_too_short_query_returns_empty(self, db):
        results = search_service.search_questions('a')
        assert results == []

    def test_search_excludes_quarantined(self, db, org, subject, member, user):
        with tenant_context(org):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 'matematika test'},
                options=[],
                is_quarantined=True,
            )
        results = search_service.search_questions('matematika', org=org)
        assert len(results) == 0

    def test_search_endpoint(self, db, org, subject, member, user):
        with tenant_context(org):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 'kimyo test'},
                options=[],
            )
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:search'), {'q': 'kimyo'})
        assert resp.status_code == 200
        assert resp.json()['count'] >= 1

    def test_search_endpoint_400_short_query(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:search'), {'q': 'a'})
        assert resp.status_code == 400


# ─── REST API: Streak/Leagues ────────────────────────────────────────────────


@pytest.mark.integration
class TestEngagementAPI:
    def test_streak_endpoint_default_zero(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:streak'))
        assert resp.status_code == 200
        assert resp.json()['current_streak'] == 0

    def test_streak_endpoint_after_activity(self, db, user, member):
        streak_service.update_on_activity(user)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:streak'))
        assert resp.json()['current_streak'] == 1

    def test_current_league_returns_membership(self, db, user, member, league_catalog):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:leagues-current'))
        assert resp.status_code == 200
        data = resp.json()
        assert data['league']['name'] == 'Bronza'
        assert data['my_points'] == 0

    def test_league_history_endpoint(self, db, user, member, league_catalog):
        # Create membership
        leagues_service.get_or_create_membership(user)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:leagues-history'))
        assert resp.status_code == 200
        assert len(resp.json()['history']) >= 1


# ─── SEO ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestSEO:
    def test_sitemap_xml_returns_200(self, db, org, subject, member, user):
        # Avval bir QuestionVersion qo'shamiz
        with tenant_context(org):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 'test'},
                options=[],
            )

        client = Client()
        # Sitemap public — auth kerakmas
        resp = client.get('/sitemap.xml')
        assert resp.status_code == 200
        assert b'<urlset' in resp.content

    def test_sitemap_excludes_quarantined(self, db, org, subject, member, user):
        with tenant_context(org):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            qv = QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': 't'},
                options=[],
                is_quarantined=True,
            )

        client = Client()
        resp = client.get('/sitemap.xml')
        # Quarantined question URL'i sitemap'da yo'q
        assert f'/questions/{q.id}/'.encode() not in resp.content
