"""Client-IP spoofing and credential brute-force regression tests.

These go through the full middleware stack with the Django test client:

* An untrusted peer must not be able to pick its own rate-limit bucket by
  sending a fresh ``X-Forwarded-For`` / ``CF-Connecting-IP`` per request.
* Password login, register and password reset sit in the strict login tier.
* A per-account limit with time-based backoff applies across source IPs.
* A trusted proxy's ``CF-Connecting-IP`` is honoured, so real users behind
  Cloudflare keep separate buckets.
"""

import pytest
from django.test import Client

LOGIN_URL = '/api/auth/login/'
REGISTER_URL = '/api/auth/register/'

UNTRUSTED_PEER = '203.0.113.7'
TRUSTED_PROXY_PEER = '10.20.0.5'


def _fake_ip(i: int) -> str:
    return f'198.51.{i // 250}.{i % 250 + 1}'


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def victim(db):
    from apps.accounts.models import CustomUser

    return CustomUser.objects.create_user(
        username='victim_user', email='victim@example.com', password='Correct-horse-9'
    )


@pytest.mark.django_db
class TestSpoofedForwardedHeaders:
    def test_login_rotating_xff_from_untrusted_peer_hits_429(self, client, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        statuses = []
        for i in range(20):
            resp = client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=UNTRUSTED_PEER,
                HTTP_X_FORWARDED_FOR=_fake_ip(i),
            )
            statuses.append(resp.status_code)
        assert 429 in statuses, statuses
        # Strict tier: the 6th request from one peer is already refused.
        assert statuses[5] == 429, statuses

    def test_login_rotating_cf_connecting_ip_from_untrusted_peer_hits_429(self, client, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        statuses = [
            client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=UNTRUSTED_PEER,
                HTTP_CF_CONNECTING_IP=_fake_ip(i),
            ).status_code
            for i in range(20)
        ]
        assert statuses[5] == 429, statuses

    def test_xff_behind_trusted_proxy_uses_rightmost_untrusted_hop(self, client, settings):
        # Attacker-controlled left-most entries change every time; the hop the
        # trusted proxy appended (the real client) stays the same.
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8']
        statuses = [
            client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=TRUSTED_PROXY_PEER,
                HTTP_X_FORWARDED_FOR=f'{_fake_ip(i)}, {UNTRUSTED_PEER}',
            ).status_code
            for i in range(20)
        ]
        assert statuses[5] == 429, statuses

    def test_register_is_in_strict_tier(self, client, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        statuses = [
            client.post(
                REGISTER_URL,
                {'username': 'x', 'password': 'short'},
                REMOTE_ADDR=UNTRUSTED_PEER,
                HTTP_X_FORWARDED_FOR=_fake_ip(i),
            ).status_code
            for i in range(8)
        ]
        assert statuses[5] == 429, statuses

    def test_password_reset_is_in_strict_tier(self, client, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        statuses = [
            client.post(
                '/api/auth/recovery/telegram/',
                {'username': f'x{i}user'},
                REMOTE_ADDR=UNTRUSTED_PEER,
                HTTP_X_FORWARDED_FOR=_fake_ip(i),
            ).status_code
            for i in range(8)
        ]
        assert statuses[5] == 429, statuses


@pytest.mark.django_db
class TestTrustedProxy:
    def test_cf_connecting_ip_from_trusted_proxy_keeps_clients_separate(self, client, settings):
        """Many real users behind one Cloudflare/ingress hop must not share a bucket."""
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8']
        statuses = [
            client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=TRUSTED_PROXY_PEER,
                HTTP_CF_CONNECTING_IP=_fake_ip(i),
            ).status_code
            for i in range(20)
        ]
        assert 429 not in statuses, statuses

    def test_cf_connecting_ip_from_trusted_proxy_is_the_bucket(self, client, settings):
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8', '173.245.48.0/20']
        statuses = [
            client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=TRUSTED_PROXY_PEER,
                HTTP_CF_CONNECTING_IP='192.0.2.44',
                HTTP_X_FORWARDED_FOR='173.245.48.1',
            ).status_code
            for i in range(8)
        ]
        assert statuses[5] == 429, statuses

    def test_forged_cf_header_bypassing_cloudflare_hits_429(self, client, settings):
        # Direct hit on the origin: the ingress appends the real peer to XFF.
        settings.TRUSTED_PROXY_CIDRS = ['10.0.0.0/8', '173.245.48.0/20']
        statuses = [
            client.post(
                LOGIN_URL,
                {'username': f'nobody{i}', 'password': 'wrong-password-1'},
                REMOTE_ADDR=TRUSTED_PROXY_PEER,
                HTTP_CF_CONNECTING_IP=_fake_ip(i),
                HTTP_X_FORWARDED_FOR=f'{_fake_ip(i + 100)}, {UNTRUSTED_PEER}',
            ).status_code
            for i in range(8)
        ]
        assert statuses[5] == 429, statuses


@pytest.mark.django_db
class TestPerAccountThrottle:
    def _attempt(self, client, i, login='victim_user', password='wrong-password-1'):
        # Every attempt from a distinct, genuinely different peer: the IP tier
        # never trips, so only a per-account limit can stop this.
        return client.post(
            LOGIN_URL,
            {'username': login, 'password': password},
            REMOTE_ADDR=f'192.0.2.{i + 1}',
        )

    def test_per_account_limit_triggers_across_ips(self, client, settings, victim):
        settings.TRUSTED_PROXY_CIDRS = []
        statuses = [self._attempt(client, i).status_code for i in range(20)]
        assert 429 in statuses, statuses
        first_429 = statuses.index(429)
        assert first_429 <= 6, statuses
        assert all(s == 429 for s in statuses[first_429:]), statuses

    def test_correct_password_refused_while_backoff_active(self, client, settings, victim):
        settings.TRUSTED_PROXY_CIDRS = []
        for i in range(10):
            self._attempt(client, i)
        resp = self._attempt(client, 50, password='Correct-horse-9')
        assert resp.status_code == 429
        assert int(resp['Retry-After']) > 0

    def test_other_accounts_unaffected(self, client, settings, victim):
        settings.TRUSTED_PROXY_CIDRS = []
        for i in range(10):
            self._attempt(client, i)
        assert self._attempt(client, 70, login='someone_else').status_code == 400

    def test_backoff_expires_and_is_bounded(self, client, settings, victim, monkeypatch):
        """Time-based backoff, never a permanent lock — even under sustained attack."""
        from core.utils import login_throttle

        settings.TRUSTED_PROXY_CIDRS = []
        now = [1_000_000.0]
        monkeypatch.setattr(login_throttle, '_now', lambda: now[0])

        # Sustained attack: keep hammering, including while locked.
        for i in range(200):
            self._attempt(client, i % 250)
            now[0] += 1

        resp = self._attempt(client, 251, password='Correct-horse-9')
        assert resp.status_code == 429
        retry_after = int(resp['Retry-After'])
        assert 0 < retry_after <= max(settings.LOGIN_LOCK_LADDER_SECONDS)

        now[0] += retry_after + 1
        resp = self._attempt(client, 252, password='Correct-horse-9')
        assert resp.status_code == 200, resp.content

        # A successful login clears the counter.
        assert self._attempt(client, 253).status_code == 400
