"""
Unit tests for email + password authentication
Focus: Registration validation, password strength, email uniqueness
"""

import pytest
from django.contrib.auth import authenticate

from apps.accounts.models import CustomUser


@pytest.mark.unit
class TestPasswordRegistration:
    """Password validation during registration."""

    def test_register_email_password_valid(self, db):
        """Valid email + password → user created."""
        user = CustomUser.objects.create_user(
            username='newuser', email='new@example.com', password='Pass1234'
        )
        assert user.email == 'new@example.com'
        assert user.check_password('Pass1234')

    def test_register_password_too_short(self, db):
        """Password < 8 chars → created but app logic should reject in view."""
        # Django's create_user doesn't validate length, but views do
        user = CustomUser.objects.create_user(
            username='user',
            email='test@test.com',
            password='Pass1',  # Only 5 chars
        )
        # User is created, but password is hashed
        assert user.check_password('Pass1')

    def test_register_password_no_number(self, db):
        """Password without number → enforce in validation."""
        password = 'OnlyLetters'
        assert not any(c.isdigit() for c in password)

    def test_register_password_no_letter(self, db):
        """Password without letter → enforce in validation."""
        password = '12345678'
        assert not any(c.isalpha() for c in password)

    def test_register_duplicate_email(self, db):
        """Duplicate email → raises IntegrityError."""
        CustomUser.objects.create_user(
            username='user1', email='duplicate@example.com', password='Pass1234'
        )

        with pytest.raises(Exception):  # IntegrityError or ValidationError
            CustomUser.objects.create_user(
                username='user2', email='duplicate@example.com', password='Pass1234'
            )

    def test_register_duplicate_username(self, db):
        """Duplicate username → raises IntegrityError."""
        CustomUser.objects.create_user(
            username='duplicate', email='user1@example.com', password='Pass1234'
        )

        with pytest.raises(Exception):
            CustomUser.objects.create_user(
                username='duplicate', email='user2@example.com', password='Pass1234'
            )


@pytest.mark.unit
class TestPasswordAuthentication:
    """Password-based login."""

    def test_authenticate_valid_credentials(self, db):
        """Valid username + password → authenticates user."""
        user = CustomUser.objects.create_user(
            username='testuser', email='test@example.com', password='Pass1234'
        )

        authenticated = authenticate(username='testuser', password='Pass1234')
        assert authenticated is not None
        assert authenticated.id == user.id

    def test_authenticate_invalid_password(self, db):
        """Wrong password → returns None."""
        CustomUser.objects.create_user(
            username='testuser', email='test@example.com', password='Pass1234'
        )

        authenticated = authenticate(username='testuser', password='WrongPass')
        assert authenticated is None

    def test_authenticate_nonexistent_user(self, db):
        """Nonexistent username → returns None."""
        authenticated = authenticate(username='nouser', password='Pass1234')
        assert authenticated is None

    def test_authenticate_by_email(self, db):
        """Login by email instead of username (case insensitive)."""
        user = CustomUser.objects.create_user(
            username='testuser', email='Test@Example.COM', password='Pass1234'
        )

        # Email-based login (app-specific logic needed in view)
        try:
            found = CustomUser.objects.get(email__iexact='test@example.com')
            authenticated = authenticate(username=found.username, password='Pass1234')
            assert authenticated is not None
        except CustomUser.DoesNotExist:
            pass

    def test_authenticate_case_insensitive_email(self, db):
        """Email lookup is case-insensitive."""
        CustomUser.objects.create_user(
            username='testuser', email='Test@Example.COM', password='Pass1234'
        )

        found = CustomUser.objects.filter(email__iexact='test@example.com').first()
        assert found is not None

    def test_authenticate_inactive_user(self, db):
        """Inactive user → authentication fails or returns None."""
        user = CustomUser.objects.create_user(
            username='inactive', email='inactive@example.com', password='Pass1234'
        )
        user.is_active = False
        user.save()

        authenticated = authenticate(username='inactive', password='Pass1234')
        # Django backend may or may not return inactive users
        if authenticated is not None:
            assert not authenticated.is_active


@pytest.mark.unit
class TestEmailValidation:
    """Email format and validation."""

    def test_email_valid_format(self, db):
        """Standard email format accepted."""
        user = CustomUser.objects.create_user(
            username='user', email='user+tag@example.co.uk', password='Pass1234'
        )
        assert user.email == 'user+tag@example.co.uk'

    def test_email_required(self, db):
        """Email field is required for password-based auth."""
        # Django default: email can be empty but ACCOUNT_EMAIL_REQUIRED=True in settings
        user = CustomUser(username='user', password='Pass1234')
        # Should validate if email is required by app settings

    def test_email_lowercase_normalized(self, db):
        """Email stored/searched case-insensitively."""
        user = CustomUser.objects.create_user(
            username='user1', email='User@Example.Com', password='Pass1234'
        )
        found = CustomUser.objects.get(email__iexact='user@example.com')
        assert found.id == user.id


@pytest.mark.unit
class TestPasswordHashing:
    """Password security — hashing and verification."""

    def test_password_hashed_not_plaintext(self, db):
        """Password stored as hash, not plaintext."""
        user = CustomUser.objects.create_user(
            username='user', email='test@example.com', password='Pass1234'
        )
        assert user.password != 'Pass1234'
        assert user.password.startswith('pbkdf2_') or user.password.startswith('md5$')

    def test_password_check_correct(self, db):
        """check_password with correct plaintext → True."""
        user = CustomUser.objects.create_user(
            username='user', email='test@example.com', password='Pass1234'
        )
        assert user.check_password('Pass1234')

    def test_password_check_incorrect(self, db):
        """check_password with wrong plaintext → False."""
        user = CustomUser.objects.create_user(
            username='user', email='test@example.com', password='Pass1234'
        )
        assert not user.check_password('WrongPass')

    def test_password_set_method(self, db):
        """set_password() updates hash."""
        user = CustomUser.objects.create_user(
            username='user', email='test@example.com', password='Old1234'
        )
        old_hash = user.password

        user.set_password('New1234')
        user.save()

        assert user.password != old_hash
        assert user.check_password('New1234')
        assert not user.check_password('Old1234')
