"""Unit tests for the redesigned rate limiter (2026-05-16 NAT-aware version).

Asosiy holatlar:
  - Tier limits: login (5), browse (120), api (60), anticheat (30), static (200)
  - Endpoint-tier mapping (path + method)
  - User vs IP identifier
  - Progressive penalty ladder (softer: 1m / 5m / 30m / 2h)
  - Org aggregate (optional)
  - Bypass for staff/superuser
"""

import pytest
from django.test import RequestFactory

from core.middleware.rate_limit import RateLimitMiddleware, _identifier, _select_tier
from core.utils import rate_limiter as rl


@pytest.fixture(autouse=True)
def isolate_rate_limiter(monkeypatch, mock_redis):
    """Har test fresh Redis bilan ishlasin (conftest mock_redis autouse)."""
    monkeypatch.setattr(rl, '_redis', lambda: mock_redis)
    yield


# ── Tier definitions ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestTierDefinitions:
    def test_login_strict_5_per_min(self):
        assert rl.TIER_LOGIN.limit == 5
        assert rl.TIER_LOGIN.window == 60

    def test_browse_generous_120(self):
        # NAT-friendly — office of 50 can navigate freely
        assert rl.TIER_BROWSE.limit == 120

    def test_api_default_60(self):
        # Default for mutations
        assert rl.TIER_API.limit == 60

    def test_anticheat_30(self):
        # Real anti-cheat fires 1-5x/exam — 30/min generous
        assert rl.TIER_ANTICHEAT.limit == 30

    def test_static_200(self):
        # Apache serves these in production, this is fallback
        assert rl.TIER_STATIC.limit == 200


# ── Penalty ladder ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPenaltyLadder:
    def test_softer_than_v1(self):
        # v1: 60 / 300 / 3600 / 86400  (24h)
        # v2: 60 / 300 / 1800 / 7200   (2h max)
        assert rl.PENALTY_LADDER == [60, 300, 1800, 7200]

    def test_max_penalty_is_2h(self):
        # 24h was too punitive — real users locked out for a day
        assert max(rl.PENALTY_LADDER) == 2 * 3600


# ── Endpoint → tier mapping ─────────────────────────────────────────────────


@pytest.mark.unit
class TestTierSelection:
    def setup_method(self):
        self.factory = RequestFactory()

    def _req(self, path, method='GET'):
        r = self.factory.generic(method, path)
        return r

    def test_login_path_uses_login_tier(self):
        assert _select_tier(self._req('/accounts/login/')) is rl.TIER_LOGIN
        assert _select_tier(self._req('/api/auth/login/')) is rl.TIER_LOGIN
        assert _select_tier(self._req('/api/v1/auth/login/')) is rl.TIER_LOGIN

    def test_otp_uses_login_tier(self):
        assert _select_tier(self._req('/auth/otp/send/')) is rl.TIER_LOGIN
        assert _select_tier(self._req('/api/auth/otp/verify/')) is rl.TIER_LOGIN

    def test_password_reset_uses_login_tier(self):
        assert _select_tier(self._req('/accounts/password/reset/')) is rl.TIER_LOGIN

    def test_signup_uses_login_tier(self):
        assert _select_tier(self._req('/accounts/signup/')) is rl.TIER_LOGIN

    def test_anticheat_uses_anticheat_tier(self):
        assert (
            _select_tier(self._req('/api/v1/exams/abc-123/anticheat/', method='POST'))
            is rl.TIER_ANTICHEAT
        )

    def test_static_uses_static_tier(self):
        assert _select_tier(self._req('/static/css/base.css')) is rl.TIER_STATIC
        assert _select_tier(self._req('/media/uploads/x.png')) is rl.TIER_STATIC
        assert _select_tier(self._req('/favicon.ico')) is rl.TIER_STATIC

    def test_api_get_uses_browse_tier(self):
        # Read-heavy navigation
        assert _select_tier(self._req('/api/v1/exams/', method='GET')) is rl.TIER_BROWSE
        assert _select_tier(self._req('/api/v1/leaderboard/', method='HEAD')) is rl.TIER_BROWSE

    def test_api_post_uses_api_tier(self):
        # Mutation
        assert _select_tier(self._req('/api/v1/exams/', method='POST')) is rl.TIER_API
        assert _select_tier(self._req('/api/v1/wallet/topup/', method='POST')) is rl.TIER_API
        assert _select_tier(self._req('/api/v1/exams/1/', method='DELETE')) is rl.TIER_API

    def test_admin_uses_api_tier(self):
        # Admin pages — moderate limit
        assert _select_tier(self._req('/admin/')) is rl.TIER_API
        assert _select_tier(self._req('/admin/auth/user/')) is rl.TIER_API

    def test_unknown_path_defaults_to_api(self):
        assert _select_tier(self._req('/foo/bar/')) is rl.TIER_API

    def test_priority_login_beats_api(self):
        # /api/v1/auth/login/ matches both login marker and /api/ marker
        # Login takes priority (more restrictive)
        assert _select_tier(self._req('/api/v1/auth/login/', method='POST')) is rl.TIER_LOGIN

    def test_subpath_prefix_works(self):
        # Production runs under /yuzdanyuz/ subpath — markers use 'in' not startswith
        # so they still match
        assert _select_tier(self._req('/yuzdanyuz/accounts/login/')) is rl.TIER_LOGIN
        assert _select_tier(self._req('/yuzdanyuz/api/v1/exams/', method='GET')) is rl.TIER_BROWSE


# ── Identifier ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestIdentifier:
    def setup_method(self):
        self.factory = RequestFactory()

    def test_anonymous_uses_ip(self):
        r = self.factory.get('/', REMOTE_ADDR='1.2.3.4')
        r.user = type('AnonUser', (), {'is_authenticated': False})()
        assert _identifier(r) == 'ip:1.2.3.4'

    def test_anonymous_xff_first_ip(self):
        # X-Forwarded-For (proxy chain) — first IP is client
        r = self.factory.get('/', HTTP_X_FORWARDED_FOR='5.6.7.8, 10.0.0.1', REMOTE_ADDR='10.0.0.1')
        r.user = type('AnonUser', (), {'is_authenticated': False})()
        assert _identifier(r) == 'ip:5.6.7.8'

    def test_authenticated_uses_user_pk(self):
        # NAT immune — har user alohida bucket
        r = self.factory.get('/', REMOTE_ADDR='10.0.0.99')  # shared NAT
        r.user = type('User', (), {'is_authenticated': True, 'pk': 42})()
        assert _identifier(r) == 'user:42'


# ── Core check() behavior ───────────────────────────────────────────────────


@pytest.mark.unit
class TestCheck:
    def test_under_limit_passes(self):
        for i in range(5):
            rl.check('ip:test-1', rl.TIER_LOGIN)  # 5/min limit

    def test_over_limit_raises(self):
        for _ in range(5):
            rl.check('ip:test-2', rl.TIER_LOGIN)
        with pytest.raises(rl.RateLimitExceeded) as exc:
            rl.check('ip:test-2', rl.TIER_LOGIN)
        assert exc.value.tier == 'login'
        assert exc.value.retry_after == 60  # 1st penalty

    def test_browse_tier_120_limit(self):
        # NAT scenario — 100 requests OK, 121st fails
        for _ in range(120):
            rl.check('ip:test-nat', rl.TIER_BROWSE)
        with pytest.raises(rl.RateLimitExceeded):
            rl.check('ip:test-nat', rl.TIER_BROWSE)

    def test_block_persists_across_calls(self):
        # First violation → 60s block
        for _ in range(5):
            rl.check('ip:test-3', rl.TIER_LOGIN)
        with pytest.raises(rl.RateLimitExceeded):
            rl.check('ip:test-3', rl.TIER_LOGIN)
        # Same identifier still blocked even on different tier
        with pytest.raises(rl.RateLimitExceeded) as exc:
            rl.check('ip:test-3', rl.TIER_BROWSE)
        # retry_after reflects block TTL, not tier
        assert exc.value.retry_after > 0


# ── Penalty escalation ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestProgressivePenalty:
    def test_first_violation_1min(self):
        for _ in range(5):
            rl.check('ip:esc-1', rl.TIER_LOGIN)
        with pytest.raises(rl.RateLimitExceeded) as exc:
            rl.check('ip:esc-1', rl.TIER_LOGIN)
        assert exc.value.retry_after == 60

    def test_max_penalty_2_hours_not_24(self):
        # After many violations, cap at 2h (not v1's 24h)
        ident = 'ip:esc-2'
        # Trigger 5+ violations rapidly
        for _ in range(20):
            try:
                rl.check(ident, rl.TIER_LOGIN)
            except rl.RateLimitExceeded:
                # Force block expire to trigger next violation
                from core.utils.rate_limiter import _redis

                _redis().delete(f'rl:block:{ident}')
        # Final violation should cap at 7200s (2h)
        status = rl.get_status(ident)
        assert status['violations'] >= 4
        # Re-trigger to check current penalty cap
        try:
            for _ in range(10):
                rl.check(ident, rl.TIER_LOGIN)
        except rl.RateLimitExceeded as exc:
            assert exc.retry_after <= 7200


# ── Reset / status ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestResetAndStatus:
    def test_reset_clears_block(self):
        ident = 'ip:reset-1'
        for _ in range(5):
            rl.check(ident, rl.TIER_LOGIN)
        with pytest.raises(rl.RateLimitExceeded):
            rl.check(ident, rl.TIER_LOGIN)
        rl.reset(ident)
        # No block, no counter
        rl.check(ident, rl.TIER_LOGIN)  # passes again

    def test_get_status_returns_all_tiers(self):
        rl.check('ip:stat-1', rl.TIER_BROWSE)
        rl.check('ip:stat-1', rl.TIER_API)
        status = rl.get_status('ip:stat-1')
        assert 'browse' in status['counters']
        assert 'api' in status['counters']
        assert 'login' in status['counters']
        assert 'anticheat' in status['counters']
        assert 'static' in status['counters']


# ── Org aggregate (optional advanced feature) ───────────────────────────────


@pytest.mark.unit
class TestOrgAggregate:
    def test_org_aggregate_blocks_at_limit(self):
        # 20 users in same org, each makes 5 browse requests = 100 org-aggregate
        # Org limit set to 100 → 101st blocks at org level
        org = 'org:test-org-1'
        for user_id in range(20):
            for _ in range(5):
                rl.check_with_org_aggregate(
                    f'user:org-user-{user_id}', org, rl.TIER_BROWSE, org_limit=100
                )
        # Now any user makes 1 more request → blocked at org tier
        with pytest.raises(rl.RateLimitExceeded) as exc:
            rl.check_with_org_aggregate('user:org-user-99', org, rl.TIER_BROWSE, org_limit=100)
        assert 'org' in exc.value.tier

    def test_org_aggregate_passes_per_user_limit(self):
        # Each user well under their individual limit (120 browse) → org aggregate kicks in
        org = 'org:fair-org'
        for user_id in range(15):
            for _ in range(10):  # 10 < 120 individual limit
                rl.check_with_org_aggregate(
                    f'user:fair-{user_id}', org, rl.TIER_BROWSE, org_limit=200
                )
        # Total org requests: 150, still under org_limit=200, no block
        rl.check_with_org_aggregate('user:fair-99', org, rl.TIER_BROWSE, org_limit=200)


# ── Middleware bypass ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestMiddlewareBypass:
    def setup_method(self):
        self.factory = RequestFactory()

    def test_superuser_bypasses(self, mock_redis):
        called = []
        mw = RateLimitMiddleware(lambda req: called.append(req) or 'response')
        r = self.factory.get('/api/v1/exams/', REMOTE_ADDR='1.2.3.4')
        r.user = type(
            'Su', (), {'is_authenticated': True, 'is_superuser': True, 'is_staff': True, 'pk': 1}
        )()
        # Many requests — should all pass
        for _ in range(1000):
            assert mw(r) == 'response'
        assert len(called) == 1000

    def test_staff_bypasses(self, mock_redis):
        mw = RateLimitMiddleware(lambda req: 'ok')
        r = self.factory.get('/', REMOTE_ADDR='1.2.3.4')
        r.user = type(
            'Staff',
            (),
            {'is_authenticated': True, 'is_superuser': False, 'is_staff': True, 'pk': 2},
        )()
        for _ in range(500):
            assert mw(r) == 'ok'


# ── Regression: NAT scenario ───────────────────────────────────────────────


@pytest.mark.unit
class TestNATScenario:
    """Asosiy load test topilmasi — shared NAT'da real user'lar bloklanmasligi."""

    def test_office_of_50_browsing_does_not_block(self, mock_redis):
        # 50 user same office IP, each makes 2 browse req/min = 100 total
        # TIER_BROWSE = 120 → should pass
        for i in range(50):
            for _ in range(2):
                rl.check('ip:office-nat', rl.TIER_BROWSE)
        # Buffer remaining: 20 more requests OK
        for _ in range(20):
            rl.check('ip:office-nat', rl.TIER_BROWSE)
        # 121st blocks
        with pytest.raises(rl.RateLimitExceeded):
            rl.check('ip:office-nat', rl.TIER_BROWSE)

    def test_authenticated_users_in_office_get_individual_buckets(self, mock_redis):
        # Same scenario but authenticated — each user has separate bucket
        # Office NAT no longer a problem
        for user_id in range(50):
            for _ in range(60):  # 60/min limit for API tier (mutations)
                rl.check(f'user:{user_id}', rl.TIER_API)
        # All 50 users got 60 requests each = 3000 total — no blocks
        for user_id in range(50):
            status = rl.get_status(f'user:{user_id}')
            assert not status['blocked']
