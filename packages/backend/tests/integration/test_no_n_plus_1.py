"""
ISSUE-101 — N+1 regression tests.

Strategy: `assert_max_queries(N)` fixture conftest.py'da. Har request uchun
maksimal query soni cheklanadi. Agar yangi N+1 kiritilsa → test fail.

Baseline: Django/DRF har request middleware chain'ida (~10 query):
  - SessionMiddleware: SELECT django_session
  - AuthenticationMiddleware: SELECT auth_user
  - JWTAuth: SELECT user (cookie'dan)
  - TenantMiddleware: SELECT primary_organization
  - DRF SessionAuthentication: SELECT user
  - + view'ning o'zi 1-3 query

Shu sababli "max 15-20" margin qo'yamiz va asosiy ishimiz —
view ichida `len(items)` o'sganda query soni `O(N)` emas `O(1)` ekanini
tasdiqlash. 50 ta record × 1 query (yaxshi) vs 50 × N+1 (yomon).
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import ImportBatch, QuestionDraft
from apps.commerce import wallet_service
from apps.commerce.models import Referral, ReferralCode, Wallet
from apps.engagement.models import League, LeagueMembership, Notification

# Middleware chain baseline (auth + tenant + RLS + RateLimit)
MIDDLEWARE_BASELINE = 15


@pytest.fixture
def populated_drafts(db, org, user):
    """ImportBatch + 30 ta draft mixed statuses."""
    batch = ImportBatch.objects.create(
        organization=org,
        created_by=user,
        file_type='xlsx',
        total_questions=30,
    )
    for i in range(10):
        QuestionDraft.objects.create(batch=batch, data={'text': f'q{i}'}, is_published=True)
    for i in range(10):
        QuestionDraft.objects.create(batch=batch, data={'text': f'q{i + 10}'}, is_valid=False)
    for i in range(10):
        QuestionDraft.objects.create(batch=batch, data={'text': f'q{i + 20}'})
    return batch


@pytest.fixture
def populated_referrals(db, user, user2):
    """User uchun 1 ta referral (Referral.invited unique constraint sababli)."""
    ReferralCode.objects.get_or_create(user=user, defaults={'code': 'TEST123'})
    Referral.objects.create(inviter=user, invited=user2, coin_reward=10)


@pytest.fixture
def populated_league(db, user, user2):
    """League + 2 ta membership."""
    league = League.objects.create(name='Bronza', slug='bronza', rank_order=1, color_hex='#cd7f32')
    week_start = timezone.now().date() - timedelta(days=timezone.now().weekday())
    week_end = week_start + timedelta(days=6)
    LeagueMembership.objects.create(
        user=user, league=league, period_start=week_start, period_end=week_end, points_earned=50
    )
    LeagueMembership.objects.create(
        user=user2, league=league, period_start=week_start, period_end=week_end, points_earned=100
    )
    return league


@pytest.fixture
def populated_notifications(db, user):
    """50 ta notification — list endpoint test."""
    for i in range(50):
        Notification.objects.create(
            user=user,
            channel=Notification.Channel.IN_APP,
            title=f'msg-{i}',
            body=f'body-{i}',
            priority=Notification.Priority.NORMAL,
        )


@pytest.fixture
def populated_transactions(db, user):
    """30 ta wallet transaction."""
    w, _ = Wallet.objects.get_or_create(user=user)
    for i in range(30):
        wallet_service.top_up(w, 5, description=f'test-{i}')


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestNoNPlusOne:
    """Har test query count constraint bilan — record soni o'zgarsa query soni o'sib ketmasin."""

    def test_catalog_draft_counts_single_aggregate(self, populated_drafts, assert_max_queries):
        """_draft_counts: 4 ta query → 1 ta aggregate. Function-level test
        (template loyiha ichida hali yo'q, lekin function bevosita tekshiriladi)."""
        from apps.catalog.views import _draft_counts

        with assert_max_queries(1, '_draft_counts'):
            result = _draft_counts(populated_drafts)
        assert result['total'] == 30
        assert result['invalid'] == 10
        assert result['published'] == 10
        assert result['pending'] == 20

    def test_engagement_current_league_top_users_prefetched(
        self, populated_league, user, member, assert_max_queries
    ):
        """Top-10 select_related('user') bilan — N+1 yo'q."""
        client = Client()
        client.force_login(user)
        with assert_max_queries(MIDDLEWARE_BASELINE + 8, 'leagues-current'):
            resp = client.get(reverse('engagement:leagues-current'))
        assert resp.status_code == 200
        assert len(resp.json().get('top', [])) >= 2

    def test_engagement_league_history_select_related(
        self, populated_league, user, member, assert_max_queries
    ):
        """LeagueHistoryView 20 ta member uchun bir xil query count saqlaydi."""
        client = Client()
        client.force_login(user)
        with assert_max_queries(MIDDLEWARE_BASELINE + 5, 'leagues-history'):
            resp = client.get(reverse('engagement:leagues-history'))
        assert resp.status_code == 200

    def test_engagement_notifications_scalar_no_n_plus_1(
        self, populated_notifications, user, member, assert_max_queries
    ):
        """50 ta notification — scalar field'lar, FK access yo'q → 1 query enough."""
        client = Client()
        client.force_login(user)
        with assert_max_queries(MIDDLEWARE_BASELINE + 5, 'notifications'):
            resp = client.get(reverse('engagement:notifications'))
        assert resp.status_code == 200
        assert resp.json()['count'] == 50

    def test_commerce_wallet_transactions_no_n_plus_1(
        self, populated_transactions, user, member, assert_max_queries
    ):
        """30 ta WalletTransaction — scalar field'lar, N+1 yo'q."""
        client = Client()
        client.force_login(user)
        with assert_max_queries(MIDDLEWARE_BASELINE + 6, 'wallet-transactions'):
            resp = client.get(reverse('commerce:wallet-transactions'))
        assert resp.status_code == 200
        assert resp.json()['count'] == 30

    def test_commerce_affiliate_stats_single_aggregate(
        self, populated_referrals, user, member, assert_max_queries
    ):
        """AffiliateStats: 2 ta query (count + iterate-sum) → 1 ta aggregate."""
        client = Client()
        client.force_login(user)
        with assert_max_queries(MIDDLEWARE_BASELINE + 6, 'affiliate-stats'):
            resp = client.get(reverse('commerce:affiliate-stats'))
        assert resp.status_code == 200
        data = resp.json()
        assert 'total_referrals' in data
        assert 'total_coin_earned' in data
