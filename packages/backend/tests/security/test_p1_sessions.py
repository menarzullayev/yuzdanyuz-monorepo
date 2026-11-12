"""B03 — access tokens die with their session; B08 — a deep-link login is single use."""

import json
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django.test import Client, RequestFactory

from apps.accounts.backends import JWTBackend
from apps.accounts.models import CustomUser
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.token_service import create_token_pair, verify_access_token
from core import ws_auth

pytestmark = [pytest.mark.security, pytest.mark.django_db]

STRONG = 'Tr0ub4dor&horse'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36'


def _session(user, device='device-aaaaaaaaaaaaaaaa'):
    req = RequestFactory().get('/', HTTP_USER_AGENT=UA)
    access, refresh = create_token_pair(user, device, request=req)
    c = Client(HTTP_USER_AGENT=UA)
    c.cookies['access_token'] = access
    c.cookies['refresh_token'] = refresh
    c.cookies['device_id'] = device
    return c, access


def _jwt_user(token):
    return JWTBackend().authenticate(None, token=token)


def _ws_user(token):
    return async_to_sync(ws_auth._verify_token_and_get_user)(token)


@pytest.fixture
def pw_user(db):
    return CustomUser.objects.create_user(username='pw.user', password=STRONG)


class TestLogoutKillsAccessToken:
    def test_access_token_is_rejected_everywhere_after_logout(self, pw_user):
        client, access = _session(pw_user)
        assert _jwt_user(access) == pw_user
        client.post('/api/auth/logout/')

        assert verify_access_token(access) is None
        assert _jwt_user(access) is None
        assert _ws_user(access) is None

    def test_logout_with_only_the_access_cookie_denies_that_token(self, pw_user):
        client, access = _session(pw_user)
        del client.cookies['refresh_token']
        client.post('/api/auth/logout/')
        assert _jwt_user(access) is None

    def test_other_devices_survive_a_logout(self, pw_user):
        client, access = _session(pw_user, 'device-aaaaaaaaaaaaaaaa')
        _, other = _session(pw_user, 'device-bbbbbbbbbbbbbbbb')
        client.post('/api/auth/logout/')
        assert _jwt_user(other) == pw_user


class TestPasswordChangeRevokesSessions:
    def test_password_set_outside_the_api_revokes_every_session(self, pw_user):
        # e.g. Django admin or `manage.py changepassword`
        _, access = _session(pw_user)
        pw_user.set_password('An0ther&Passw0rd')
        pw_user.save()
        assert _jwt_user(access) is None
        assert _ws_user(access) is None

    def test_api_password_change_keeps_only_the_current_session(self, pw_user):
        client, current = _session(pw_user, 'device-aaaaaaaaaaaaaaaa')
        _, other = _session(pw_user, 'device-bbbbbbbbbbbbbbbb')
        resp = client.post(
            '/api/auth/password/change/',
            data=json.dumps({'current_password': STRONG, 'new_password': 'An0ther&Passw0rd!'}),
            content_type='application/json',
        )
        assert resp.status_code == 200, resp.content
        assert _jwt_user(other) is None
        assert _jwt_user(current) == pw_user

    def test_saving_other_fields_keeps_sessions(self, pw_user):
        _, access = _session(pw_user)
        pw_user.first_name = 'Ali'
        pw_user.save()
        assert _jwt_user(access) == pw_user


class TestDeeplinkSingleUse:
    def _verified(self, tg_id=4242):
        browser = Client(HTTP_USER_AGENT=UA)
        token = browser.post('/api/auth/tg/init/').json()['token']
        assert dl.on_bot_start(token, tg_id)
        assert dl.on_bot_contact(tg_id, '+998901234567', tg_id)
        return browser, token

    def _complete(self, browser, token):
        return browser.post(
            '/api/auth/tg/complete/',
            data=json.dumps({'token': token}),
            content_type='application/json',
        )

    def test_token_cannot_be_redeemed_twice_even_if_cleanup_is_late(self):
        """Two concurrent completes both read 'verified' before either deletes it.
        Disabling the trailing delete reproduces that window deterministically."""
        browser, token = self._verified()
        with patch.object(dl, 'expire_session', lambda token: None):
            first = self._complete(browser, token)
            second = self._complete(browser, token)
        assert first.status_code == 200
        assert second.status_code == 401
        assert 'access_token' not in second.cookies

    def test_unverified_session_is_not_consumed(self):
        browser = Client(HTTP_USER_AGENT=UA)
        token = browser.post('/api/auth/tg/init/').json()['token']
        assert self._complete(browser, token).status_code == 401
        assert dl.get_session(token) is not None
