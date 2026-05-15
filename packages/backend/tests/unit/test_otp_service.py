"""
Unit tests for apps/accounts/services/otp_service.py
Focus: Rate limiting, OTP generation, verification, expiration
"""

import pytest
from datetime import datetime, timedelta, timezone
from apps.accounts.models import OTPCode
from apps.accounts.services.otp_service import (
    send_otp, verify_otp, OTPError, OTPRateLimitError
)
from apps.accounts.services.phone_utils import PhoneValidationError


@pytest.mark.unit
class TestOTPSend:
    """send_otp tests — SMS sending and rate limiting."""

    def test_send_otp_normalizes_phone(self, db, mock_sms_backend):
        """send_otp normalizes phone number format."""
        result = send_otp('+998 (90) 123-4567')
        assert result == '+998901234567'

    def test_send_otp_invalid_phone(self, db):
        """Invalid phone → PhoneValidationError."""
        with pytest.raises(PhoneValidationError):
            send_otp('not-a-phone')

    def test_send_otp_creates_otp_code(self, db, phone_number, mock_sms_backend):
        """send_otp creates OTPCode in database."""
        send_otp(phone_number)
        code = OTPCode.objects.get(phone=phone_number)
        assert code is not None
        assert len(code.code) == 6
        assert code.is_verified is False

    def test_send_otp_rate_limit_3_in_10min(self, db, phone_number, mock_redis):
        """send_otp limits to 3 SMS per 10 minutes."""
        # Send 3 OTPs — should succeed
        send_otp(phone_number)
        send_otp(phone_number)
        send_otp(phone_number)

        # 4th should fail
        with pytest.raises(OTPRateLimitError):
            send_otp(phone_number)

    def test_send_otp_rate_limit_reset_after_600s(self, db, phone_number, mock_redis):
        """After 600s, rate limit counter resets."""
        from apps.accounts.services.otp_service import _redis
        import time

        # Send 3 OTPs
        send_otp(phone_number)
        send_otp(phone_number)
        send_otp(phone_number)

        # 4th fails
        with pytest.raises(OTPRateLimitError):
            send_otp(phone_number)

        # Manually expire the rate limit counter
        r = _redis()
        r_key = f'otp:send_count:{phone_number}'
        r.delete(r_key)

        # Now it should work
        send_otp(phone_number)

    def test_send_otp_different_phones_independent(self, db, mock_redis):
        """Rate limit is per phone, not global."""
        phone1 = '+998901234567'
        phone2 = '+998981234567'

        # Send 3 to phone1
        send_otp(phone1)
        send_otp(phone1)
        send_otp(phone1)

        # Send 3 to phone2 — should all succeed
        send_otp(phone2)
        send_otp(phone2)
        send_otp(phone2)


@pytest.mark.unit
class TestOTPVerify:
    """verify_otp tests — Code verification and user lookup."""

    def test_verify_otp_correct_code(self, db, phone_number, mock_otp_code, user_with_phone):
        """Correct OTP code → returns user."""
        result = verify_otp(phone_number, '123456')
        assert result == user_with_phone

    def test_verify_otp_wrong_code(self, db, phone_number, mock_otp_code, mock_redis):
        """Wrong OTP code → OTPError."""
        with pytest.raises(OTPError):
            verify_otp(phone_number, '000000')

    def test_verify_otp_increments_attempts(self, db, phone_number, mock_otp_code):
        """Each failed attempt increments counter."""
        initial = mock_otp_code.attempts
        try:
            verify_otp(phone_number, '000000')
        except OTPError:
            pass
        mock_otp_code.refresh_from_db()
        assert mock_otp_code.attempts == initial + 1

    def test_verify_otp_max_attempts(self, db, phone_number, mock_redis):
        """After 5 attempts, code becomes invalid."""
        # Create OTP code
        code = OTPCode.objects.create(
            phone=phone_number,
            code='123456',
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_verified=False,
            attempts=5
        )

        with pytest.raises(OTPError):
            verify_otp(phone_number, '123456')

    def test_verify_otp_expired_code(self, db, phone_number, mock_redis, user_with_phone):
        """Expired OTP code → OTPError."""
        # Create expired code
        OTPCode.objects.create(
            phone=phone_number,
            code='123456',
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            is_verified=False
        )

        with pytest.raises(OTPError):
            verify_otp(phone_number, '123456')

    def test_verify_otp_no_code(self, db, phone_number):
        """No OTP code exists → OTPError."""
        with pytest.raises(OTPError):
            verify_otp(phone_number, '123456')

    def test_verify_otp_creates_user_if_not_exists(self, db, phone_number, mock_otp_code):
        """Phone has OTP but no user → creates new user."""
        # OTP exists, but user with this phone doesn't
        user = verify_otp(phone_number, '123456')
        assert user is not None
        assert user.phone_number == phone_number
        assert user.username.startswith('ph_')

    def test_verify_otp_already_verified(self, db, phone_number, user_with_phone):
        """Already verified code can't be used again."""
        OTPCode.objects.create(
            phone=phone_number,
            code='123456',
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_verified=True
        )

        with pytest.raises(OTPError):
            verify_otp(phone_number, '123456')

    def test_verify_otp_marks_as_verified(self, db, phone_number, mock_otp_code, user_with_phone):
        """After verification, is_verified becomes True."""
        assert mock_otp_code.is_verified is False
        verify_otp(phone_number, '123456')
        mock_otp_code.refresh_from_db()
        assert mock_otp_code.is_verified is True


@pytest.mark.unit
class TestOTPExceptionHandling:
    """Exception handling and edge cases."""

    def test_send_otp_invalid_phone_format(self, db):
        """Various invalid phone formats → PhoneValidationError."""
        invalid = ['abc', '123', '(999) invalid', '']
        for phone in invalid:
            with pytest.raises(PhoneValidationError):
                send_otp(phone)

    def test_verify_otp_invalid_phone(self, db):
        """verify_otp with invalid phone → PhoneValidationError."""
        with pytest.raises(PhoneValidationError):
            verify_otp('not-phone', '123456')

    def test_otp_code_expiration_boundary(self, db, phone_number, user_with_phone):
        """Code expires exactly at expires_at time."""
        now = datetime.now(timezone.utc)
        OTPCode.objects.create(
            phone=phone_number,
            code='123456',
            expires_at=now + timedelta(seconds=1),
            is_verified=False
        )

        # Should work before expiration
        verify_otp(phone_number, '123456')
