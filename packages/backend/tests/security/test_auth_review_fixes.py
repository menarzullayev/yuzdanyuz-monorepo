"""Regression tests for the independent security review of the auth rewrite.

Each class names the finding it pins down. Every test has a "bad case" that must be
refused and, where it matters, a "good case" proving legitimate use still works.
"""

import hashlib
import hmac
import itertools
import json
import time
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.accounts.models import CustomUser, DeviceSession, TOTPDevice
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services import two_factor as tf
from apps.accounts.services.token_service import create_token_pair, rotate_refresh_token
from apps.organizations.models import OrgInvite, OrgRole

STRONG = 'Tr0ub4dor&horse'
_ips = itertools.count(1)


def ip():
    n = next(_ips)
    return f'198.51.{n // 250 % 250}.{n % 250 + 1}'


def post(client, url, payload=None, **extra):
    extra.setdefault('REMOTE_ADDR', ip())
    return client.post(
        url, data=json.dumps(payload or {}), content_type='application/json', **extra
    )


def login(username, password=STRONG, client=None, **extra):
    return post(
        client or Client(),
        '/api/auth/login/',
        {'username': username, 'password': password},
        **extra,
    )


@pytest.fixture
def victim(db):
    return CustomUser.objects.create_user(username='victim.user', password=STRONG)


# ── 2. Telegram deep-link login is bound to the initiating browser ───────────


def signed_init_data(tg_user, bot_token, age=0):
    params = {
        'auth_date': str(int(time.time()) - age),
        'user': json.dumps(tg_user, separators=(',', ':')),
    }
    check = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
    secret = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    params['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


@pytest.mark.security
class TestDeeplinkBoundToBrowser:
    def _verified_session(self, tg_id=4242):
        browser = Client(HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537')
        body = browser.post('/api/auth/tg/init/', REMOTE_ADDR='203.0.113.77').json()
        token = body['token']
        assert dl.on_bot_start(token, tg_id)
        assert dl.on_bot_contact(tg_id, '+998901234567', tg_id)
        return browser, token

    def test_init_sets_an_httponly_nonce_cookie_scoped_to_the_tg_path(self, db):
        resp = Client().post('/api/auth/tg/init/')
        cookie = resp.cookies['tg_nonce']
        assert cookie['httponly'] and cookie['path'] == '/api/auth/tg/' and cookie.value
        assert cookie.value not in resp.content.decode()  # never in the body, only the cookie

    def test_initiating_browser_can_complete(self, db):
        browser, token = self._verified_session()
        resp = post(browser, '/api/auth/tg/complete/', {'token': token})
        assert resp.status_code == 200 and 'access_token' in resp.cookies

    def test_another_browser_with_the_token_cannot_complete(self, db):
        _, token = self._verified_session()
        resp = post(Client(), '/api/auth/tg/complete/', {'token': token})
        assert resp.status_code == 401 and 'access_token' not in resp.cookies
        assert dl.get_session(token) is not None  # session untouched, the owner can still finish

    def test_a_wrong_nonce_is_refused(self, db):
        _, token = self._verified_session()
        attacker = Client()
        attacker.cookies['tg_nonce'] = 'a' * 43
        assert post(attacker, '/api/auth/tg/complete/', {'token': token}).status_code == 401

    def test_a_nonce_from_another_session_is_refused(self, db):
        _, token = self._verified_session(4242)
        other_browser = Client()
        other_browser.post('/api/auth/tg/init/')  # gets its own nonce for its own session
        assert post(other_browser, '/api/auth/tg/complete/', {'token': token}).status_code == 401

    def test_session_without_a_nonce_cannot_complete(self, db):
        token = dl.create_session()['token']  # e.g. created by older code
        dl.on_bot_start(token, 4242)
        dl.on_bot_contact(4242, '+998901234567', 4242)
        browser = Client()
        browser.cookies['tg_nonce'] = 'anything'
        assert post(browser, '/api/auth/tg/complete/', {'token': token}).status_code == 401

    def test_bot_message_names_the_requesting_device_and_ip(self, db):
        browser = Client(HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537')
        token = browser.post('/api/auth/tg/init/', REMOTE_ADDR='203.0.113.77').json()['token']
        with patch('apps.accounts.views.telegram_deeplink.send_contact_request') as ask:
            Client().post(
                '/api/bot/webhook/',
                data=json.dumps(
                    {'message': {'text': f'/start tgauth_{token}', 'from': {'id': 4242}}}
                ),
                content_type='application/json',
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-webhook-secret',
            )
        chat_id, requester = ask.call_args.args
        assert chat_id == 4242 and '203.0.113.77' in requester and 'Chrome on Windows' in requester


# ── 3. Lockout cannot be used to lock the owner out ──────────────────────────


@pytest.mark.security
class TestLockoutDoS:
    def test_a_stranger_locks_only_their_own_address(self, victim, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        attacker_ip = '203.0.113.9'
        results = [login('victim.user', 'wrong-pass-1', REMOTE_ADDR=attacker_ip) for _ in range(5)]
        assert [r.status_code for r in results[:5]] == [400] * 5
        assert login('victim.user', 'wrong-pass-1', REMOTE_ADDR=attacker_ip).status_code == 429
        # the real owner, from another address and a device the account never saw, still gets in
        assert login('victim.user', REMOTE_ADDR='198.51.100.200').status_code == 200

    def test_distributed_guessing_trips_the_account_wide_lock(self, victim, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        for i in range(settings.LOGIN_GLOBAL_FREE_ATTEMPTS):
            login('victim.user', 'wrong-pass-1', REMOTE_ADDR=f'192.0.2.{i + 1}')
        assert login('victim.user', REMOTE_ADDR='192.0.2.250').status_code == 429

    def test_a_known_device_bypasses_the_account_wide_lock(self, victim, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        owner = Client()
        assert login('victim.user', client=owner, REMOTE_ADDR='198.51.100.1').status_code == 200
        assert 'device_id' in owner.cookies
        for i in range(settings.LOGIN_GLOBAL_FREE_ATTEMPTS + 1):
            login('victim.user', 'wrong-pass-1', REMOTE_ADDR=f'192.0.2.{i + 1}')
        # strangers are refused, the owner's own browser is not
        assert login('victim.user', REMOTE_ADDR='192.0.2.250').status_code == 429
        assert login('victim.user', client=owner, REMOTE_ADDR='198.51.100.1').status_code == 200

    def test_a_forged_device_cookie_does_not_bypass(self, victim, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        for i in range(settings.LOGIN_GLOBAL_FREE_ATTEMPTS + 1):
            login('victim.user', 'wrong-pass-1', REMOTE_ADDR=f'192.0.2.{i + 1}')
        forged = Client()
        forged.cookies['device_id'] = 'x' * 32
        assert login('victim.user', client=forged, REMOTE_ADDR='192.0.2.250').status_code == 429

    def test_a_known_device_on_a_locked_address_is_still_throttled_for_guessing(
        self, victim, settings
    ):
        settings.TRUSTED_PROXY_CIDRS = []
        owner = Client()
        login('victim.user', client=owner, REMOTE_ADDR='198.51.100.1')
        for _ in range(5):
            login('victim.user', 'wrong-pass-1', client=owner, REMOTE_ADDR='198.51.100.1')
        assert (
            login(
                'victim.user', 'wrong-pass-1', client=owner, REMOTE_ADDR='198.51.100.1'
            ).status_code
            == 429
        )

    def test_lock_notices_are_rate_limited(self, db, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        u = CustomUser(username='tg.owner', telegram_id=4242)
        u.set_password(STRONG)
        u.save()
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            for n in range(4):  # four different attackers each trigger a lock
                for _ in range(5):
                    login('tg.owner', 'wrong-pass-1', REMOTE_ADDR=f'203.0.113.{n + 1}')
        assert send.call_count == 1


# ── 4. Recovery links cannot be killed or exhausted by strangers ─────────────
# (see tests/integration/test_recovery.py: new link keeps the old one, per-IP hourly cap)


# ── 5. Origin check on public cookie-setting views ───────────────────────────


@pytest.mark.security
class TestOriginOnPublicViews:
    EVIL = 'https://evil.example'

    def _cases(self, settings):
        init = signed_init_data({'id': 7001, 'first_name': 'Mini'}, settings.TELEGRAM_BOT_TOKEN)
        return [
            ('/api/auth/login/', {'username': 'victim.user', 'password': STRONG}),
            (
                '/api/auth/register/',
                {
                    'first_name': 'Ali',
                    'last_name': 'Valiyev',
                    'username': 'ali.valiyev',
                    'password': STRONG,
                },
            ),
            ('/api/auth/telegram/', {'init_data': init}),
            ('/api/auth/tg/complete/', {'token': 'x'}),
            ('/api/auth/recovery/telegram/', {'username': 'victim.user'}),
        ]

    def test_foreign_origin_is_refused_and_sets_no_cookies(self, victim, settings):
        for url, payload in self._cases(settings):
            resp = post(Client(), url, payload, HTTP_ORIGIN=self.EVIL)
            assert resp.status_code == 403, url
            assert 'access_token' not in resp.cookies, url
        assert not CustomUser.objects.filter(username='ali.valiyev').exists()
        assert not CustomUser.objects.filter(telegram_id=7001).exists()

    def test_own_and_trusted_origins_and_no_origin_work(self, victim, settings):
        settings.CORS_ALLOWED_ORIGINS = ['https://app.example']
        for origin in ('https://app.example', 'http://testserver', None):
            extra = {'HTTP_ORIGIN': origin} if origin else {}
            resp = post(
                Client(),
                '/api/auth/login/',
                {'username': 'victim.user', 'password': STRONG},
                **extra,
            )
            assert resp.status_code == 200, origin

    def test_get_requests_are_not_origin_checked(self, db):
        assert (
            Client()
            .get('/api/auth/username/check/?username=abc', HTTP_ORIGIN=self.EVIL)
            .status_code
            == 200
        )


# ── 7. Non-ASCII digits in a TOTP code ───────────────────────────────────────


@pytest.mark.security
class TestTotpNonAscii:
    ARABIC = '١٢٣٤٥٦'  # six Arabic-Indic digits: str.isdigit() is True for them
    SUPERSCRIPT = '²²²²²²'

    def test_matching_step_rejects_them_without_raising(self):
        secret = tf.generate_secret()
        for bad in (self.ARABIC, self.SUPERSCRIPT, '１２３４５６'):
            assert tf.matching_step(secret, bad) is None

    def test_verify_second_factor_returns_false_instead_of_500(self, victim):
        TOTPDevice.objects.create(
            user=victim,
            secret_enc=tf.encrypt_secret(tf.generate_secret()),
            confirmed_at=timezone.now(),
        )
        for bad in (self.ARABIC, self.SUPERSCRIPT, 'ünïcödé-bäckup', '١' * 10):
            assert tf.verify_second_factor(victim, bad) is False

    def test_login_with_such_a_code_is_a_counted_400(self, victim, settings):
        settings.TRUSTED_PROXY_CIDRS = []
        TOTPDevice.objects.create(
            user=victim,
            secret_enc=tf.encrypt_secret(tf.generate_secret()),
            confirmed_at=timezone.now(),
        )
        statuses = []
        for _ in range(6):
            r = post(
                Client(),
                '/api/auth/login/',
                {'username': 'victim.user', 'password': STRONG, 'code': self.ARABIC},
                REMOTE_ADDR='203.0.113.5',
            )
            statuses.append(r.status_code)
        assert statuses[:5] == [400] * 5 and statuses[5] == 429  # counted toward the lock

    def test_a_real_code_still_works(self, victim):
        secret = tf.generate_secret()
        TOTPDevice.objects.create(
            user=victim, secret_enc=tf.encrypt_secret(secret), confirmed_at=timezone.now()
        )
        assert tf.verify_second_factor(victim, tf.totp_code(secret, tf.current_step())) is True


# ── 9. Refresh token is bound to the device cookie ───────────────────────────


@pytest.mark.security
class TestRefreshBoundToDevice:
    def _client(self, user, device='device-aaaaaaaaaaaaaaaa'):
        _, refresh = create_token_pair(user, device)
        c = Client()
        c.cookies['refresh_token'] = refresh
        return c, refresh

    def test_stolen_refresh_cookie_without_the_device_cookie_fails(self, victim):
        thief, _ = self._client(victim)  # has the refresh cookie only
        assert thief.post('/api/auth/refresh/').status_code == 401

    def test_refresh_cookie_with_someone_elses_device_cookie_fails(self, victim):
        thief, _ = self._client(victim)
        thief.cookies['device_id'] = 'device-bbbbbbbbbbbbbbbb'
        assert thief.post('/api/auth/refresh/').status_code == 401

    def test_failed_binding_does_not_revoke_the_real_session(self, victim):
        owner, _ = self._client(victim)
        owner.cookies['device_id'] = 'device-aaaaaaaaaaaaaaaa'
        thief, refresh = Client(), owner.cookies['refresh_token'].value
        thief.cookies['refresh_token'] = refresh
        thief.post('/api/auth/refresh/')
        assert owner.post('/api/auth/refresh/').status_code == 200
        assert DeviceSession.objects.get().revoked_at is None

    def test_matching_device_cookie_works(self, victim):
        c, _ = self._client(victim)
        c.cookies['device_id'] = 'device-aaaaaaaaaaaaaaaa'
        assert c.post('/api/auth/refresh/').status_code == 200


# ── 10. Two tabs refreshing together ─────────────────────────────────────────


@pytest.mark.security
class TestRefreshRace:
    def test_second_tab_in_the_grace_window_does_not_rotate_again(self, victim):
        _, t0 = create_token_pair(victim, 'device-aaaaaaaaaaaaaaaa')
        tab_a = rotate_refresh_token(t0)
        tab_b = rotate_refresh_token(t0)  # same token, a moment later

        assert tab_a.refresh and tab_b is not None
        assert tab_b.refresh is None  # no second rotation: the cookie jar keeps tab A's token
        # whichever response the browser kept, the next refresh works and nothing is revoked
        later = rotate_refresh_token(tab_a.refresh)
        assert later is not None
        assert DeviceSession.objects.get().revoked_at is None

    def test_no_reuse_revocation_even_after_the_grace_window_when_the_cookie_is_current(
        self, victim
    ):
        _, t0 = create_token_pair(victim, 'device-aaaaaaaaaaaaaaaa')
        tab_a = rotate_refresh_token(t0)
        rotate_refresh_token(t0)
        DeviceSession.objects.update(rotated_at=timezone.now() - timedelta(minutes=5))
        assert rotate_refresh_token(tab_a.refresh) is not None
        assert DeviceSession.objects.get().revoked_reason == ''

    def test_replaying_the_old_token_after_the_window_is_still_a_theft_signal(self, victim):
        _, t0 = create_token_pair(victim, 'device-aaaaaaaaaaaaaaaa')
        tab_a = rotate_refresh_token(t0)
        DeviceSession.objects.update(rotated_at=timezone.now() - timedelta(minutes=5))
        assert rotate_refresh_token(t0) is None
        assert DeviceSession.objects.get().revoked_reason == 'reuse'
        assert rotate_refresh_token(tab_a.refresh) is None

    def test_over_http_the_second_tab_only_gets_an_access_cookie(self, victim):
        c = Client()
        login('victim.user', client=c)
        refresh_before = c.cookies['refresh_token'].value
        device = c.cookies['device_id'].value
        tab_a, tab_b = Client(), Client()
        for t in (tab_a, tab_b):
            t.cookies['refresh_token'] = refresh_before
            t.cookies['device_id'] = device
        ra = tab_a.post('/api/auth/refresh/')
        rb = tab_b.post('/api/auth/refresh/')
        assert ra.status_code == rb.status_code == 200
        assert 'refresh_token' in ra.cookies and 'refresh_token' not in rb.cookies
        assert 'access_token' in rb.cookies


# ── 11. initData freshness ───────────────────────────────────────────────────


@pytest.mark.security
class TestInitDataAge:
    def test_login_rejects_initdata_older_than_ten_minutes(self, db, settings):
        old = signed_init_data(
            {'id': 7002, 'first_name': 'Old'}, settings.TELEGRAM_BOT_TOKEN, age=601
        )
        resp = post(Client(), '/api/auth/telegram/', {'init_data': old})
        assert resp.status_code == 401
        assert not CustomUser.objects.filter(telegram_id=7002).exists()

    def test_login_accepts_fresh_initdata(self, db, settings):
        fresh = signed_init_data(
            {'id': 7003, 'first_name': 'New'}, settings.TELEGRAM_BOT_TOKEN, age=300
        )
        assert post(Client(), '/api/auth/telegram/', {'init_data': fresh}).status_code == 200

    def test_linking_rejects_old_initdata(self, victim, settings):
        c = Client()
        c.cookies['access_token'] = create_token_pair(victim, 'device-aaaaaaaaaaaaaaaa')[0]
        old = signed_init_data({'id': 7004}, settings.TELEGRAM_BOT_TOKEN, age=601)
        assert post(c, '/api/auth/linked/telegram/', {'init_data': old}).status_code == 400
        victim.refresh_from_db()
        assert victim.telegram_id is None

    def test_initdata_without_auth_date_is_refused(self, db, settings):
        params = {'user': json.dumps({'id': 7005}, separators=(',', ':'))}
        check = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
        secret = hmac.new(
            b'WebAppData', settings.TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        params['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        assert (
            post(Client(), '/api/auth/telegram/', {'init_data': urlencode(params)}).status_code
            == 401
        )


# ── 12. Unlink takes a row lock ──────────────────────────────────────────────


@pytest.mark.security
class TestUnlinkLocksTheUserRow:
    def test_delete_telegram_selects_the_user_for_update(self, db):
        user = CustomUser.objects.create_user(username='both.user', password=STRONG, telegram_id=9)
        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        with CaptureQueriesContext(connection) as ctx:
            assert c.delete('/api/auth/linked/telegram/').status_code == 200
        assert any(
            'FOR UPDATE' in q['sql'] and 'accounts_customuser' in q['sql']
            for q in ctx.captured_queries
        )

    def test_a_second_unlink_after_the_first_cannot_remove_the_last_method(self, db):
        """Sequential stand-in for the race: with the lock the check sees fresh state."""
        from allauth.socialaccount.models import SocialAccount

        user = CustomUser(username='tg.google', telegram_id=9)
        user.set_unusable_password()
        user.save()
        SocialAccount.objects.create(user=user, provider='google', uid='g1', extra_data={})
        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        assert c.delete('/api/auth/linked/telegram/').status_code == 200
        assert c.delete('/api/auth/linked/google/').status_code == 400
        assert SocialAccount.objects.filter(user=user).exists()


# ── 13. OrgInvite.use() is atomic ────────────────────────────────────────────


@pytest.mark.security
class TestInviteUseAtomic:
    def _invite(self, org, **kw):
        creator = CustomUser.objects.create_user(username='org.owner', password=STRONG)
        role = OrgRole.objects.get(organization=org, name='student')
        return OrgInvite.objects.create(organization=org, role=role, created_by=creator, **kw)

    def test_stale_copies_cannot_both_take_the_last_seat(self, db, org):
        invite = self._invite(org, max_uses=1)
        copy_a, copy_b = OrgInvite.objects.get(pk=invite.pk), OrgInvite.objects.get(pk=invite.pk)
        assert copy_a.use() is True
        assert copy_b.use() is False  # old code overwrote used_count and returned nothing
        assert OrgInvite.objects.get(pk=invite.pk).used_count == 1

    def test_unlimited_invites_keep_counting(self, db, org):
        invite = self._invite(org)
        assert invite.use() and invite.use()
        assert OrgInvite.objects.get(pk=invite.pk).used_count == 2

    def test_disabled_and_expired_invites_do_not_count(self, db, org):
        invite = self._invite(org, is_active=False)
        assert invite.use() is False
        invite2 = self._invite_with_expiry(org)
        assert invite2.use() is False

    def _invite_with_expiry(self, org):
        creator = CustomUser.objects.get(username='org.owner')
        role = OrgRole.objects.get(organization=org, name='student')
        return OrgInvite.objects.create(
            organization=org,
            role=role,
            created_by=creator,
            expires_at=timezone.now() - timedelta(days=1),
        )

    def test_losing_the_race_rolls_the_membership_back(self, db, org):
        from apps.accounts.services import onboarding as ob
        from apps.organizations.models import Membership

        org.status = 'active'
        org.save()
        invite = self._invite(org, max_uses=1)
        user = CustomUser.objects.create_user(username='late.joiner', password=STRONG)
        with patch.object(OrgInvite, 'use', return_value=False):
            with pytest.raises(ob.InviteError):
                ob.join_with_invite(user, str(invite.token), True)
        assert not Membership.objects.filter(user=user).exists()
        assert not user.org_consents.exists()
