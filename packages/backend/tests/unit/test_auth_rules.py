"""Unit tests for the auth rules: usernames, brute-force ladder, lock notices, OTP hashing (P7)."""

import json
from unittest.mock import patch

import pytest
from django.test import Client

from apps.accounts.models import CustomUser, OTPCode
from apps.accounts.services import usernames as un
from core.utils import login_throttle

STRONG = 'Tr0ub4dor&horse'


@pytest.mark.unit
class TestUsernameService:
    def test_generate_from_latin_name(self, db):
        assert un.generate_username('Ali', 'Valiyev') == 'ali.valiyev'

    def test_generate_transliterates_cyrillic_and_uzbek_letters(self, db):
        assert un.generate_username('Алишер', 'Қодиров') == 'alisher.qodirov'

    def test_generate_adds_digits_when_taken(self, db):
        CustomUser.objects.create_user(username='ali.valiyev', password=STRONG)
        name = un.generate_username('Ali', 'Valiyev')
        assert name != 'ali.valiyev' and name.startswith('ali.valiyev')
        assert un.username_format_error(name) is None

    def test_generate_avoids_reserved_words(self, db):
        name = un.generate_username('Admin', '')
        assert name != 'admin' and not un.is_reserved(name)

    def test_generate_falls_back_for_unusable_names(self, db):
        name = un.generate_username('', '', fallback='')
        assert un.username_format_error(name) is None

    def test_reserved_detection_ignores_separators(self, db):
        # look-alikes with separators inserted are blocked too
        assert un.is_reserved('ad.min') and un.is_reserved('a_d_m_i_n')
        assert not un.is_reserved('adminov')
        assert un.is_reserved('admin') and un.is_reserved('SUPPORT')

    def test_suggestions_are_free_valid_and_distinct(self, db):
        CustomUser.objects.create_user(username='taken', password=STRONG)
        s = un.suggest_usernames('taken')
        assert len(s) == 3 and len(set(s)) == 3
        assert all(un.available(x) for x in s)


@pytest.mark.unit
class TestLockLadder:
    """After 5 failures: 1 min, then 5, then 15 (cap)."""

    def test_ladder_steps(self, settings):
        settings.LOGIN_FAILURE_FREE_ATTEMPTS = 5
        steps = [login_throttle._lock_seconds(n) for n in range(10)]
        assert steps == [0, 0, 0, 0, 0, 60, 300, 900, 900, 900]

    def test_register_failure_sets_a_lock_only_from_the_fifth(self, mock_redis):
        key = login_throttle.account_key('someone')
        assert [login_throttle.register_failure(key) for _ in range(7)] == [
            0,
            0,
            0,
            0,
            60,
            300,
            900,
        ]


@pytest.mark.unit
class TestLockNotification:
    def _fail(self, client, username, n):
        for _ in range(n):
            client.post(
                '/api/auth/login/',
                data=json.dumps({'username': username, 'password': 'wrong-password-1'}),
                content_type='application/json',
            )

    def test_owner_is_told_on_telegram_when_the_account_locks(self, db):
        u = CustomUser(username='tg.owner', telegram_id=4242)
        u.set_password(STRONG)
        u.save()
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            self._fail(Client(), 'tg.owner', 5)

        assert send.call_count == 1
        chat_id, text = send.call_args.args
        assert chat_id == 4242 and '1' in text

    def test_no_notice_before_the_lock_starts(self, db):
        u = CustomUser(username='tg.owner', telegram_id=4242)
        u.set_password(STRONG)
        u.save()
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            self._fail(Client(), 'tg.owner', 4)
        send.assert_not_called()

    def test_accounts_without_telegram_get_nothing(self, db):
        CustomUser.objects.create_user(username='plain.user', password=STRONG)
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            self._fail(Client(), 'plain.user', 6)
        send.assert_not_called()

    def test_unknown_usernames_never_trigger_notices(self, db):
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            self._fail(Client(), 'ghost.user', 6)
        send.assert_not_called()


@pytest.mark.unit
class TestOtpHashing:
    """P7: secrets-based code, only a hash stored, constant-time compare."""

    def test_code_comes_from_secrets_not_random(self, db):
        with patch('apps.accounts.models.secrets.randbelow', return_value=42) as rb:
            otp, code = OTPCode.create_for_phone('+998901234567')
        rb.assert_called_once_with(10**6)
        assert code == '000042'
        assert otp.matches('000042')

    def test_models_do_not_import_random(self):
        import apps.accounts.models as m

        assert not hasattr(m, 'random')

    def test_plaintext_code_is_never_stored(self, db):
        otp, code = OTPCode.create_for_phone('+998901234567')
        row = OTPCode.objects.values().get(pk=otp.pk)
        assert code not in {str(v) for v in row.values()}
        assert otp.code_hash != code

    def test_wrong_code_does_not_match(self, db):
        with patch('apps.accounts.models.secrets.randbelow', return_value=7):
            otp, _ = OTPCode.create_for_phone('+998901234567')
        assert otp.matches('000007')
        assert not otp.matches('000008')
        assert not otp.matches('')

    def test_hash_is_bound_to_the_phone(self, db):
        assert OTPCode.hash_code('+998901111111', '123456') != OTPCode.hash_code(
            '+998902222222', '123456'
        )

    def test_comparison_is_constant_time(self, db):
        with patch('apps.accounts.models.hmac.compare_digest', return_value=False) as cd:
            otp, _ = OTPCode.create_for_phone('+998901234567')
            otp.matches('123456')
        cd.assert_called_once()
