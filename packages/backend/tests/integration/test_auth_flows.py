"""
Integration tests for the sign-in flows: username+password JSON API, SMS OTP
(behind a flag), token refresh and logout.
"""

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser, DeviceSession, OTPCode

STRONG = 'Tr0ub4dor&horse'


def _post(client, name, payload, **extra):
    return client.post(
        reverse(f'accounts:{name}'),
        data=json.dumps(payload),
        content_type='application/json',
        **extra,
    )


@pytest.fixture
def pw_user(db):
    return CustomUser.objects.create_user(username='aliyev', password=STRONG)


@pytest.mark.integration
class TestPasswordLogin:
    def test_login_success_sets_cookies_and_returns_user(self, pw_user):
        resp = _post(Client(), 'login', {'username': 'aliyev', 'password': STRONG})

        assert resp.status_code == 200
        assert resp.json()['user']['username'] == 'aliyev'
        assert resp.cookies['access_token'].value
        assert resp.cookies['refresh_token'].value
        assert resp.cookies['device_id'].value

    def test_username_is_case_insensitive(self, pw_user):
        resp = _post(Client(), 'login', {'username': 'ALIYEV', 'password': STRONG})
        assert resp.status_code == 200

    def test_wrong_password(self, pw_user):
        resp = _post(Client(), 'login', {'username': 'aliyev', 'password': 'nope-nope-1'})
        assert resp.status_code == 400
        assert resp.json()['error']['code'] == 'invalid_credentials'
        assert 'access_token' not in resp.cookies

    def test_unknown_user_gets_the_same_answer(self, pw_user):
        a = _post(Client(), 'login', {'username': 'aliyev', 'password': 'nope-nope-1'})
        b = _post(Client(), 'login', {'username': 'nobody', 'password': 'nope-nope-1'})
        assert (a.status_code, a.json()['error']['message']) == (
            b.status_code,
            b.json()['error']['message'],
        )

    def test_email_is_not_a_login_identifier(self, db):
        CustomUser.objects.create_user(username='ali', email='ali@example.com', password=STRONG)
        resp = _post(Client(), 'login', {'username': 'ali@example.com', 'password': STRONG})
        assert resp.status_code == 400

    def test_inactive_user_cannot_login(self, pw_user):
        pw_user.is_active = False
        pw_user.save()
        resp = _post(Client(), 'login', {'username': 'aliyev', 'password': STRONG})
        assert resp.status_code == 400

    def test_telegram_only_account_has_no_password_login(self, db):
        u = CustomUser(username='tg_user', telegram_id=42)
        u.set_unusable_password()
        u.save()
        resp = _post(Client(), 'login', {'username': 'tg_user', 'password': ''})
        assert resp.status_code == 400

    def test_remember_me_gives_persistent_cookies(self, pw_user):
        resp = _post(
            Client(), 'login', {'username': 'aliyev', 'password': STRONG, 'remember': True}
        )
        assert resp.cookies['refresh_token']['max-age'] == 30 * 24 * 3600
        assert DeviceSession.objects.get().remember is True

    def test_without_remember_me_cookies_are_session_cookies(self, pw_user):
        resp = _post(Client(), 'login', {'username': 'aliyev', 'password': STRONG})
        assert resp.cookies['refresh_token']['max-age'] == ''
        assert resp.cookies['access_token']['max-age'] == ''
        assert DeviceSession.objects.get().remember is False

    def test_second_device_does_not_log_out_the_first_P9(self, pw_user):
        """P9: logging in on a laptop must not kill the phone session."""
        phone, laptop = Client(), Client()
        _post(phone, 'login', {'username': 'aliyev', 'password': STRONG})
        _post(laptop, 'login', {'username': 'aliyev', 'password': STRONG})

        assert phone.post('/api/auth/refresh/').status_code == 200
        assert laptop.post('/api/auth/refresh/').status_code == 200

    def test_relogin_on_same_device_reuses_the_device_cookie(self, pw_user):
        c = Client()
        first = _post(c, 'login', {'username': 'aliyev', 'password': STRONG})
        _post(c, 'login', {'username': 'aliyev', 'password': STRONG})
        assert DeviceSession.objects.filter(revoked_at__isnull=True).count() == 1
        assert first.cookies['device_id'].value == c.cookies['device_id'].value

    def test_full_cycle_login_me_logout(self, pw_user):
        c = Client()
        _post(c, 'login', {'username': 'aliyev', 'password': STRONG})
        assert c.get('/api/auth/me/').json()['username'] == 'aliyev'
        c.post('/api/auth/logout/')
        assert c.get('/api/auth/me/').status_code == 401


@pytest.mark.integration
class TestOTPAuthFlow:
    """Phone OTP authentication (feature flag ENABLE_SMS_OTP, on in test settings)."""

    def test_otp_send_valid_phone(self, db, client, phone_number, mock_redis, mock_sms_backend):
        response = _post(client, 'otp_send', {'phone': phone_number})

        assert response.status_code == 200
        otp = OTPCode.objects.get(phone=phone_number)
        assert len(otp.code_hash) == 64

    def test_otp_send_invalid_phone(self, db, client):
        assert _post(client, 'otp_send', {'phone': 'not-a-phone'}).status_code == 400

    def test_otp_send_rate_limited(self, db, client, phone_number, mock_redis, mock_sms_backend):
        for _ in range(3):
            assert _post(client, 'otp_send', {'phone': phone_number}).status_code == 200
        assert _post(client, 'otp_send', {'phone': phone_number}).status_code == 429

    def test_otp_verify_success(self, db, client, user_with_phone, phone_number, mock_otp_code):
        response = _post(client, 'otp_verify', {'phone': phone_number, 'code': '123456'})

        assert response.status_code == 200
        assert 'access_token' in response.cookies
        assert response.json()['user']['id'] == str(user_with_phone.pk)

    def test_otp_verify_wrong_code(self, db, client, phone_number, mock_otp_code):
        response = _post(client, 'otp_verify', {'phone': phone_number, 'code': '000000'})
        assert response.status_code == 401

    def test_otp_verify_creates_user(self, db, client, phone_number, mock_otp_code):
        response = _post(client, 'otp_verify', {'phone': phone_number, 'code': '123456'})
        assert response.status_code == 200
        assert CustomUser.objects.filter(phone_number=phone_number).exists()

    def test_flag_off_disables_both_endpoints(self, db, client, phone_number, settings):
        """Negative test: with the flag off the SMS endpoints do not exist."""
        settings.ENABLE_SMS_OTP = False
        assert _post(client, 'otp_send', {'phone': phone_number}).status_code == 404
        assert (
            _post(client, 'otp_verify', {'phone': phone_number, 'code': '123456'}).status_code
            == 404
        )

    def test_flag_is_off_by_default(self, monkeypatch):
        import importlib.util
        from pathlib import Path

        monkeypatch.delenv('ENABLE_SMS_OTP', raising=False)
        path = Path(__file__).resolve().parents[2] / 'core' / 'settings' / 'base.py'
        spec = importlib.util.spec_from_file_location('base_settings_probe', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # a throwaway copy; core.settings.base stays untouched
        assert module.ENABLE_SMS_OTP is False
