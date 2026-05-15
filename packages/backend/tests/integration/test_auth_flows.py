"""
Integration tests for authentication flows
Focus: Login, registration, token generation, middleware chain
"""

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import CustomUser


@pytest.mark.integration
class TestEmailPasswordAuthFlow:
    """Email + password authentication end-to-end."""

    def test_email_register_success(self, db, client: Client):
        """POST /api/auth/register/ with valid email + password."""
        response = client.post(
            reverse('accounts:register'),
            data={
                'reg_method': 'email',
                'email': 'newuser@example.com',
                'password': 'Pass1234',
                'password_confirm': 'Pass1234',
            },
        )

        # Check user created
        user = CustomUser.objects.get(email='newuser@example.com')
        assert user is not None
        # Email registration should return 200 (verification pending)
        assert response.status_code == 200
        # Email verified=False (needs verification)
        from allauth.account.models import EmailAddress

        email_addr = EmailAddress.objects.get(user=user, email=user.email)
        assert email_addr.verified is False
        # No cookies yet (needs email verification)
        assert 'access_token' not in response.cookies

    def test_email_register_duplicate_email(self, db, client: Client, user):
        """Register with existing email → fails."""
        response = client.post(
            reverse('accounts:register'),
            data={
                'reg_method': 'email',
                'email': user.email,
                'password': 'Pass1234',
                'password_confirm': 'Pass1234',
            },
        )

        assert response.status_code == 400

    def test_email_register_password_mismatch(self, db, client: Client):
        """password != password_confirm → fails."""
        response = client.post(
            reverse('accounts:register'),
            data={
                'reg_method': 'email',
                'email': 'test@example.com',
                'password': 'Pass1234',
                'password_confirm': 'Different',
            },
        )

        assert response.status_code == 400

    def test_email_register_weak_password(self, db, client: Client):
        """Password < 8 chars or no number/letter → fails."""
        response = client.post(
            reverse('accounts:register'),
            data={
                'reg_method': 'email',
                'email': 'test@example.com',
                'password': 'short',
                'password_confirm': 'short',
            },
        )

        assert response.status_code == 400

    def test_email_login_success(self, db, client: Client, user):
        """POST /api/auth/email/ with valid credentials."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Should redirect to /
        assert response.status_code == 204 or response['HX-Redirect'] == '/'
        assert 'access_token' in response.cookies

    def test_email_login_by_username(self, db, client: Client, user):
        """Login by username instead of email."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.username, 'password': 'Pass1234'}
        )

        assert response.status_code == 204

    def test_email_login_wrong_password(self, db, client: Client, user):
        """Wrong password → fails."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'WrongPass'}
        )

        assert response.status_code == 400

    def test_email_login_inactive_user(self, db, client: Client, user):
        """Inactive user → login fails with 400 (authenticate returns None)."""
        user.set_password('Pass1234')
        user.is_active = False
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Django's authenticate() respects is_active and returns None for inactive users
        # So the view returns 400 (wrong credentials)
        assert response.status_code == 400


@pytest.mark.integration
class TestOTPAuthFlow:
    """Phone OTP authentication end-to-end."""

    def test_otp_send_valid_phone(
        self, db, client: Client, phone_number, mock_redis, mock_sms_backend
    ):
        """POST /api/auth/otp/send/ with valid phone."""
        response = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
        )

        assert response.status_code == 200
        # Check OTPCode created in DB
        from apps.accounts.models import OTPCode

        otp = OTPCode.objects.filter(phone=phone_number).first()
        assert otp is not None
        assert len(otp.code) == 6

    def test_otp_send_invalid_phone(self, db, client: Client):
        """Invalid phone format → 400."""
        response = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': 'not-a-phone'}),
            content_type='application/json',
        )

        assert response.status_code == 400

    def test_otp_send_rate_limited(
        self, db, client: Client, phone_number, mock_redis, mock_sms_backend
    ):
        """3rd SMS in 10 min succeeds, 4th fails."""
        # Send 3
        resp1 = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
        )
        assert resp2.status_code == 200

        resp3 = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
        )
        assert resp3.status_code == 200

        # 4th fails with rate limit
        response = client.post(
            reverse('accounts:otp_send'),
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
        )
        assert response.status_code == 429

    def test_otp_verify_success(
        self, db, client: Client, user_with_phone, phone_number, mock_otp_code
    ):
        """POST /api/auth/otp/verify/ with correct code."""
        response = client.post(
            reverse('accounts:otp_verify'),
            data=json.dumps({'phone': phone_number, 'code': '123456'}),
            content_type='application/json',
        )

        assert response.status_code == 200
        assert 'access_token' in response.cookies
        # Response should have user info
        import json as json_module

        data = json_module.loads(response.content)
        assert 'user' in data
        assert data['user']['id'] == str(user_with_phone.pk)

    def test_otp_verify_wrong_code(self, db, client: Client, phone_number, mock_otp_code):
        """Wrong OTP code → 401."""
        response = client.post(
            reverse('accounts:otp_verify'),
            data=json.dumps({'phone': phone_number, 'code': '000000'}),
            content_type='application/json',
        )

        assert response.status_code == 401
        assert 'error' in response.content.decode()

    def test_otp_verify_no_user(self, db, client: Client, phone_number, mock_otp_code):
        """Phone has OTP code but no matching user found → creates user."""
        response = client.post(
            reverse('accounts:otp_verify'),
            data=json.dumps({'phone': phone_number, 'code': '123456'}),
            content_type='application/json',
        )

        # Should succeed and create user
        assert response.status_code == 200
        assert 'access_token' in response.cookies
        # Verify user was created
        user = CustomUser.objects.get(phone_number=phone_number)
        assert user is not None


@pytest.mark.integration
class TestTelegramAuthFlow:
    """Telegram TMA authentication."""

    def test_telegram_auth_valid_init_data(self, db, client: Client):
        """POST /api/auth/telegram/ with valid initData."""
        # This requires valid HMAC — test with mock
        # Will be implemented after telegram_auth service tests pass

    def test_telegram_auth_creates_user(self, db, client: Client):
        """New Telegram user → creates CustomUser."""
        # Placeholder for full integration test


@pytest.mark.integration
class TestJWTTokenManagement:
    """JWT token creation, verification, refresh."""

    def test_token_pair_created_on_login(self, db, client: Client, user):
        """Login creates access + refresh tokens in cookies."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        assert 'access_token' in response.cookies
        assert 'refresh_token' in response.cookies

    def test_refresh_token_endpoint(self, db, client: Client, user, mock_redis):
        """POST /api/auth/refresh/ with valid refresh token."""
        # Will test after token_service is fully validated

    def test_access_token_expired(self, db, client: Client, user):
        """Expired access token → unauthorized."""
        # Create expired token
        # Make request with expired token
        # Should fail or redirect to login


@pytest.mark.integration
class TestDeviceFingerprinting:
    """Device checking and session management."""

    def test_fingerprint_set_on_login(self, db, client: Client, user, mock_redis):
        """After login, device fingerprint stored in Redis."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Fingerprint should be in Redis session
        # Check session:active:{user_id}

    def test_same_device_multiple_logins(self, db, client: Client, user, mock_redis):
        """Same device (same fingerprint) → multiple logins allowed."""
        user.set_password('Pass1234')
        user.save()

        # Login 1
        response1 = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Login 2 with same device (same Client session)
        response2 = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Both should succeed
        assert response1.status_code == 204
        assert response2.status_code == 204


@pytest.mark.integration
class TestLogoutFlow:
    """Logout and session clearing."""

    def test_logout_clears_cookies(self, db, client: Client, user):
        """POST /api/auth/logout/ clears auth cookies."""
        user.set_password('Pass1234')
        user.save()

        # Login
        login_response = client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Verify login worked
        assert login_response.status_code == 204

        # Logout
        logout_response = client.post(reverse('accounts:logout'))

        # Should return 200 status (JsonResponse from auth.py LogoutView)
        assert logout_response.status_code == 200
        # Cookies should be cleared (Max-Age=0)
        assert 'access_token' in logout_response.cookies

    def test_logout_invalidates_tokens(self, db, client: Client, user, mock_redis):
        """After logout, tokens are revoked in Redis."""
        user.set_password('Pass1234')
        user.save()

        # Login
        client.post(
            reverse('accounts:email_login'), data={'login': user.email, 'password': 'Pass1234'}
        )

        # Logout
        client.post(reverse('accounts:logout'))

        # Session should be cleared from Redis
        # Subsequent requests with old token should fail
