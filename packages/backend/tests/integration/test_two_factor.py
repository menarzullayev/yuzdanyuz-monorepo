"""Two-factor authentication: TOTP primitives, setup/confirm/disable, login gate, backup codes."""

import base64
import json
import time

import pytest
from django.test import Client

from apps.accounts.models import BackupCode, CustomUser, TOTPDevice
from apps.accounts.services import two_factor as tf

STRONG = 'Tr0ub4dor&horse'
RFC_SECRET = base64.b32encode(b'12345678901234567890').decode()  # RFC 6238 appendix B key


def j(client, url, payload=None, method='post'):
    return getattr(client, method)(
        url, data=json.dumps(payload or {}), content_type='application/json'
    )


def code_now(secret, offset=0):
    return tf.totp_code(secret, tf.current_step() + offset)


@pytest.mark.unit
class TestTotpPrimitives:
    def test_rfc6238_vectors(self):
        # RFC 6238 appendix B (SHA-1), truncated to 6 digits
        assert tf.totp_code(RFC_SECRET, 59 // 30) == '287082'
        assert tf.totp_code(RFC_SECRET, 1111111109 // 30) == '081804'
        assert tf.totp_code(RFC_SECRET, 2000000000 // 30) == '279037'

    def test_window_accepts_previous_and_next_step_only(self):
        now = 1_700_000_000.0
        step = tf.current_step(now)
        for off in (-1, 0, 1):
            assert (
                tf.matching_step(RFC_SECRET, tf.totp_code(RFC_SECRET, step + off), now)
                == step + off
            )
        for off in (-2, 2):
            assert tf.matching_step(RFC_SECRET, tf.totp_code(RFC_SECRET, step + off), now) is None

    @pytest.mark.parametrize('bad', ['', '12345', '1234567', 'abcdef', '12 456', '000000'])
    def test_malformed_or_wrong_codes(self, bad):
        assert tf.matching_step(RFC_SECRET, bad, 59.0) is None or bad == '287082'

    def test_secret_roundtrip_encryption(self):
        enc = tf.encrypt_secret(RFC_SECRET)
        assert RFC_SECRET not in enc
        assert tf.decrypt_secret(enc) == RFC_SECRET

    def test_uri_contains_issuer_and_secret(self, settings):
        uri = tf.provisioning_uri(RFC_SECRET, 'ali')
        assert uri.startswith('otpauth://totp/') and f'secret={RFC_SECRET}' in uri
        assert 'period=30' in uri and 'digits=6' in uri


@pytest.fixture
def signed_in(db):
    user = CustomUser.objects.create_user(username='twofa.user', password=STRONG)
    c = Client()
    assert (
        j(c, '/api/auth/login/', {'username': 'twofa.user', 'password': STRONG}).status_code == 200
    )
    return c, user


def enable(c, user):
    secret = j(c, '/api/auth/2fa/setup/').json()['secret']
    body = j(c, '/api/auth/2fa/confirm/', {'code': code_now(secret)}).json()
    return secret, body['backup_codes']


@pytest.mark.integration
class TestSetup:
    def test_status_off_by_default(self, signed_in):
        c, _ = signed_in
        assert c.get('/api/auth/2fa/').json() == {'enabled': False, 'backup_codes_remaining': 0}

    def test_setup_then_confirm_enables_and_returns_10_backup_codes(self, signed_in):
        c, user = signed_in
        secret, codes = enable(c, user)
        assert len(codes) == 10 and len(set(codes)) == 10
        assert tf.is_enabled(user)
        assert c.get('/api/auth/2fa/').json() == {'enabled': True, 'backup_codes_remaining': 10}
        # the DB holds neither the secret nor any backup code in clear
        dev = TOTPDevice.objects.get(user=user)
        assert secret not in dev.secret_enc
        stored = set(BackupCode.objects.values_list('code_hash', flat=True))
        assert not stored & set(codes)

    def test_confirm_with_wrong_code_keeps_2fa_off(self, signed_in):
        c, user = signed_in
        j(c, '/api/auth/2fa/setup/')
        resp = j(c, '/api/auth/2fa/confirm/', {'code': '000000'})
        assert resp.status_code == 400
        assert not tf.is_enabled(user)

    def test_confirm_without_setup(self, signed_in):
        c, _ = signed_in
        assert j(c, '/api/auth/2fa/confirm/', {'code': '123456'}).status_code == 400

    def test_setup_when_already_enabled_is_409(self, signed_in):
        c, user = signed_in
        enable(c, user)
        assert j(c, '/api/auth/2fa/setup/').status_code == 409

    def test_anonymous_cannot_touch_2fa(self, db):
        for url in ('/api/auth/2fa/setup/', '/api/auth/2fa/confirm/', '/api/auth/2fa/disable/'):
            assert j(Client(), url).status_code == 401

    def test_a_code_cannot_be_used_twice(self, signed_in):
        c, user = signed_in
        secret = j(c, '/api/auth/2fa/setup/').json()['secret']
        code = code_now(secret)
        assert j(c, '/api/auth/2fa/confirm/', {'code': code}).status_code == 200
        # replay of the very same code (e.g. at login) is refused
        assert tf.verify_second_factor(user, code) is False


@pytest.mark.integration
class TestLoginGate:
    def test_password_alone_does_not_sign_in_when_2fa_is_on(self, signed_in):
        c, user = signed_in
        enable(c, user)
        fresh = Client()
        resp = j(fresh, '/api/auth/login/', {'username': 'twofa.user', 'password': STRONG})

        assert resp.status_code == 200
        assert resp.json() == {'need_code': True}
        assert 'access_token' not in resp.cookies
        assert fresh.get('/api/auth/me/').status_code == 401

    def test_wrong_code_is_refused(self, signed_in):
        c, user = signed_in
        enable(c, user)
        resp = j(
            Client(),
            '/api/auth/login/',
            {'username': 'twofa.user', 'password': STRONG, 'code': '000000'},
        )
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'invalid_code'
        assert 'access_token' not in resp.cookies

    def test_right_code_signs_in(self, signed_in):
        c, user = signed_in
        secret, _ = enable(c, user)
        # next step so the confirm-step code (already spent) is not reused
        resp = j(
            Client(),
            '/api/auth/login/',
            {'username': 'twofa.user', 'password': STRONG, 'code': code_now(secret, +1)},
        )
        assert resp.status_code == 200 and 'access_token' in resp.cookies

    def test_correct_code_with_wrong_password_is_refused(self, signed_in):
        c, user = signed_in
        secret, _ = enable(c, user)
        resp = j(
            Client(),
            '/api/auth/login/',
            {'username': 'twofa.user', 'password': 'wrong-pass-1', 'code': code_now(secret, +1)},
        )
        assert resp.status_code == 400

    def test_replayed_login_code_is_refused(self, signed_in):
        c, user = signed_in
        secret, _ = enable(c, user)
        payload = {'username': 'twofa.user', 'password': STRONG, 'code': code_now(secret, +1)}
        assert j(Client(), '/api/auth/login/', payload).status_code == 200
        assert j(Client(), '/api/auth/login/', payload).status_code == 400

    def test_backup_code_works_once(self, signed_in):
        c, user = signed_in
        _, codes = enable(c, user)
        payload = {'username': 'twofa.user', 'password': STRONG, 'code': codes[0].upper()}
        assert j(Client(), '/api/auth/login/', payload).status_code == 200
        assert j(Client(), '/api/auth/login/', payload).status_code == 400
        assert tf.backup_codes_remaining(user) == 9

    def test_unused_backup_codes_other_users_do_not_work(self, signed_in):
        c, user = signed_in
        _, codes = enable(c, user)
        other = CustomUser.objects.create_user(username='other.user', password=STRONG)
        tf.generate_backup_codes(other)
        TOTPDevice.objects.create(
            user=other, secret_enc=tf.encrypt_secret(tf.generate_secret()), confirmed_at=time_now()
        )
        payload = {'username': 'other.user', 'password': STRONG, 'code': codes[0]}
        assert j(Client(), '/api/auth/login/', payload).status_code == 400

    def test_guessing_codes_hits_the_account_lock(self, signed_in):
        c, user = signed_in
        enable(c, user)
        statuses = [
            j(
                Client(),
                '/api/auth/login/',
                {'username': 'twofa.user', 'password': STRONG, 'code': f'{i:06d}'},
            ).status_code
            for i in range(8)
        ]
        assert 429 in statuses, statuses

    def test_accounts_without_2fa_are_unaffected(self, db):
        CustomUser.objects.create_user(username='plain.user', password=STRONG)
        resp = j(Client(), '/api/auth/login/', {'username': 'plain.user', 'password': STRONG})
        assert resp.status_code == 200 and 'user' in resp.json()


def time_now():
    from django.utils import timezone

    return timezone.now()


@pytest.mark.integration
class TestDisableAndRegenerate:
    def test_disable_needs_password_and_code(self, signed_in):
        c, user = signed_in
        secret, _ = enable(c, user)
        assert (
            j(
                c, '/api/auth/2fa/disable/', {'password': 'bad-pass-1', 'code': code_now(secret, 1)}
            ).status_code
            == 400
        )
        assert tf.is_enabled(user)
        assert (
            j(c, '/api/auth/2fa/disable/', {'password': STRONG, 'code': '000000'}).status_code
            == 400
        )
        assert tf.is_enabled(user)
        assert j(c, '/api/auth/2fa/disable/', {'password': STRONG}).status_code == 400
        assert tf.is_enabled(user)

    def test_disable_with_backup_code(self, signed_in):
        c, user = signed_in
        _, codes = enable(c, user)
        resp = j(c, '/api/auth/2fa/disable/', {'password': STRONG, 'code': codes[3]})
        assert resp.status_code == 200
        assert not tf.is_enabled(user)
        assert not BackupCode.objects.filter(user=user).exists()
        assert not TOTPDevice.objects.filter(user=user).exists()

    def test_regenerate_replaces_old_codes(self, signed_in):
        c, user = signed_in
        secret, old = enable(c, user)
        resp = j(c, '/api/auth/2fa/backup-codes/regenerate/', {'code': code_now(secret, 1)})
        new = resp.json()['backup_codes']
        assert len(new) == 10 and not set(new) & set(old)
        # an old code no longer works
        assert tf.verify_second_factor(user, old[0]) is False
        assert tf.verify_second_factor(user, new[0]) is True

    def test_regenerate_requires_a_valid_code(self, signed_in):
        c, user = signed_in
        _, old = enable(c, user)
        assert j(c, '/api/auth/2fa/backup-codes/regenerate/', {'code': '000000'}).status_code == 400
        assert tf.verify_second_factor(user, old[0]) is True

    def test_telegram_only_account_disables_with_code_alone(self, db):
        user = CustomUser(username='tg.only', telegram_id=9)
        user.set_unusable_password()
        user.save()
        from apps.accounts.services.token_service import create_token_pair

        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        secret, _ = enable(c, user)
        assert j(c, '/api/auth/2fa/disable/', {'code': code_now(secret, 1)}).status_code == 200


def test_clock_is_utc_based():
    assert abs(tf.current_step() - int(time.time() // 30)) <= 1
