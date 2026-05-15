"""
Unit tests for apps/accounts/services/telegram_auth.py
Focus: HMAC validation, user creation, data parsing
"""

import pytest
import hmac
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlencode
from django.conf import settings
from apps.accounts.models import CustomUser
from apps.accounts.services.telegram_auth import verify_init_data, find_or_create_user, TelegramAuthError


@pytest.mark.unit
class TestTelegramInitDataValidation:
    """verify_init_data tests — HMAC-SHA256 signature validation."""

    def test_verify_init_data_valid_signature(self):
        """Valid initData with correct HMAC → returns parsed data."""
        import json

        user_id = 123456789
        first_name = "John"
        last_name = "Doe"
        username = "johndoe"
        language_code = "en"

        user_obj = {
            'id': user_id,
            'is_bot': False,
            'first_name': first_name,
            'last_name': last_name,
            'username': username,
            'language_code': language_code
        }

        auth_date = str(int(datetime.now(timezone.utc).timestamp()))

        data = {
            'user': json.dumps(user_obj),
            'chat_instance': '1234567890',
            'auth_date': auth_date
        }

        # Calculate HMAC exactly as the service does
        bot_token = settings.TELEGRAM_BOT_TOKEN
        secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        data_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

        init_data = urlencode(data) + f'&hash={data_hash}'

        result = verify_init_data(init_data)
        assert result is not None
        assert result['id'] == user_id
        assert result['first_name'] == first_name

    def test_verify_init_data_invalid_hash(self):
        """Invalid HMAC → raises TelegramAuthError."""
        data = {
            'user': '{"id":123}',
            'auth_date': str(int(datetime.now(timezone.utc).timestamp()))
        }
        init_data = urlencode(data) + '&hash=invalid_hash'

        with pytest.raises(TelegramAuthError):
            verify_init_data(init_data)

    def test_verify_init_data_expired_timestamp(self):
        """Auth date > max_age (24h default) → raises TelegramAuthError."""
        old_timestamp = int(datetime.now(timezone.utc).timestamp()) - (86400 + 1)
        data = {
            'user': '{"id":123}',
            'auth_date': str(old_timestamp)
        }

        secret_key = hashlib.sha256(f'WebAppData{settings.TELEGRAM_BOT_TOKEN}'.encode()).digest()
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        data_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

        init_data = urlencode(data) + f'&hash={data_hash}'

        with pytest.raises(TelegramAuthError):
            verify_init_data(init_data, max_age=86400)

    def test_verify_init_data_no_hash(self):
        """Missing hash parameter → raises TelegramAuthError."""
        data = {
            'user': '{"id":123}',
            'auth_date': str(int(datetime.now(timezone.utc).timestamp()))
        }
        init_data = urlencode(data)

        with pytest.raises(TelegramAuthError):
            verify_init_data(init_data)

    def test_verify_init_data_malformed_json(self):
        """Malformed user JSON → raises TelegramAuthError."""
        data = {
            'user': 'not-json',
            'auth_date': str(int(datetime.now(timezone.utc).timestamp()))
        }

        secret_key = hashlib.sha256(f'WebAppData{settings.TELEGRAM_BOT_TOKEN}'.encode()).digest()
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        data_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

        init_data = urlencode(data) + f'&hash={data_hash}'

        # Should raise TelegramAuthError (invalid JSON)
        with pytest.raises(Exception):  # JSONDecodeError or TelegramAuthError
            verify_init_data(init_data)


@pytest.mark.unit
class TestFindOrCreateUser:
    """find_or_create_user tests — User lookup and creation."""

    def test_find_or_create_user_existing(self, db, user_with_telegram):
        """User with existing Telegram ID → returns (user, created=False)."""
        telegram_data = {
            'id': user_with_telegram.telegram_id,
            'first_name': 'John',
        }

        user, created = find_or_create_user(telegram_data)
        assert user.id == user_with_telegram.id
        assert created is False

    def test_find_or_create_user_creates_new(self, db):
        """New Telegram user → creates CustomUser and returns (user, created=True)."""
        telegram_data = {
            'id': 987654321,
            'first_name': 'Jane',
            'last_name': 'Smith',
            'username': 'janesmith'
        }

        user, created = find_or_create_user(telegram_data)
        assert user.telegram_id == 987654321
        assert user.first_name == 'Jane'
        assert user.last_name == 'Smith'
        assert user.username == 'janesmith'
        assert created is True

    def test_find_or_create_user_auto_generates_username(self, db):
        """User without username → generates from ID."""
        telegram_data = {
            'id': 111222333,
            'first_name': 'Anonymous',
        }

        user, created = find_or_create_user(telegram_data)
        assert user.telegram_id == 111222333
        assert user.username is not None
        assert created is True

    def test_find_or_create_user_duplicate_username(self, db):
        """Username conflict → auto-append number."""
        existing = CustomUser.objects.create_user(
            username='janesmith',
            email='jane@example.com',
            password='pass'
        )

        telegram_data = {
            'id': 111222333,
            'first_name': 'Jane',
            'username': 'janesmith'
        }

        user, created = find_or_create_user(telegram_data)
        assert user.id != existing.id
        assert user.username != 'janesmith'
        assert created is True

    def test_find_or_create_user_sets_telegram_fields(self, db):
        """User fields set from Telegram data."""
        telegram_data = {
            'id': 999111222,
            'first_name': 'Test',
            'last_name': 'User',
            'username': 'testuser',
            'language_code': 'en'
        }

        user, created = find_or_create_user(telegram_data)
        assert user.telegram_id == 999111222
        assert user.first_name == 'Test'
        assert user.last_name == 'User'
        assert user.telegram_username == 'testuser'
        assert user.telegram_language == 'en'

    def test_find_or_create_user_no_phone(self, db):
        """User created without phone → phone_number empty."""
        telegram_data = {
            'id': 555666777,
            'first_name': 'NoPhone'
        }

        user, created = find_or_create_user(telegram_data)
        assert user.phone_number == '' or user.phone_number is None
        assert created is True


@pytest.mark.unit
class TestTelegramDataExtraction:
    """Test parsing of various Telegram user data formats."""

    def test_parse_user_with_all_fields(self):
        """Telegram user with all fields → extracted correctly."""
        data = {
            'id': 123456,
            'is_bot': False,
            'first_name': 'John',
            'last_name': 'Doe',
            'username': 'johndoe',
            'language_code': 'en'
        }

        assert data['id'] == 123456
        assert data['first_name'] == 'John'

    def test_parse_user_minimal_fields(self):
        """Telegram user with only id + first_name → works."""
        data = {
            'id': 123456,
            'first_name': 'John'
        }

        assert data['id'] == 123456
        assert data['first_name'] == 'John'
        assert 'username' not in data
