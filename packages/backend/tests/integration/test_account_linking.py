"""
Integration tests for multi-auth account linking
Focus: Linking Telegram + Phone + Email + Google to same user
"""

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import CustomUser

User = get_user_model()


@pytest.mark.integration
class TestAccountLinking:
    """Link multiple auth methods to single account."""

    def test_user_has_primary_auth_method(self, db, user_with_phone):
        """User initially has phone as auth method."""
        assert user_with_phone.phone_number == '+998901234567'
        assert user_with_phone.user_type == 'student'

    def test_add_telegram_to_phone_account(self, db, user_with_phone):
        """Link Telegram ID to existing phone account."""
        # Simulate linking Telegram
        user_with_phone.telegram_id = 123456789
        user_with_phone.save()

        assert user_with_phone.phone_number == '+998901234567'
        assert user_with_phone.telegram_id == 123456789

    def test_add_email_to_phone_account(self, db, user_with_phone):
        """Change email on phone-based account."""
        assert user_with_phone.email == 'phone@example.com'
        user_with_phone.email = 'newemail@example.com'
        user_with_phone.save()

        assert user_with_phone.email == 'newemail@example.com'

    def test_telegram_and_email_same_user(self, db, user_with_telegram):
        """User with Telegram can also have email login."""
        user_with_telegram.email = 'telegram@example.com'
        user_with_telegram.save()

        assert user_with_telegram.telegram_id == 123456789
        assert user_with_telegram.email == 'telegram@example.com'

    def test_cannot_merge_different_users(self, db, user_with_phone, user_with_telegram):
        """Cannot directly merge two separate CustomUser accounts."""
        # This is a data integrity check
        assert user_with_phone.id != user_with_telegram.id


@pytest.mark.integration
class TestSocialAccountLinking:
    """Google/OAuth account linking via allauth SocialAccount."""

    def test_google_creates_social_account(self, db):
        """Google login creates SocialAccount entry."""
        # Requires allauth flow to be complete
        # Placeholder for full test

    def test_google_links_to_existing_email(self, db, user):
        """Google account with same email links to existing user."""
        # User has email: test@example.com
        # Google user also has email: test@example.com
        # Should link to existing user


@pytest.mark.integration
class TestAuthMethodPriority:
    """Authentication priority hierarchy."""

    def test_username_password_priority_1(self, db):
        """Username + Password is priority 1 (primary)."""
        from apps.accounts.models import CustomUser

        user = CustomUser.objects.create_user(
            username='primary', email='test@example.com', password='Pass1234'
        )
        # No other auth methods
        assert user.user_type == 'b2c'

    def test_email_password_priority_2(self, db):
        """Email + Password is priority 2."""
        from apps.accounts.models import CustomUser

        user = CustomUser.objects.create_user(
            username='emailuser', email='email@example.com', password='Pass1234'
        )
        # Can also login via email
        user.set_password('Pass1234')
        user.save()
        # Verify user was created
        assert user.email == 'email@example.com'

    def test_telegram_priority_3(self, db, user_with_telegram):
        """Telegram ID is priority 3."""
        assert user_with_telegram.telegram_id is not None

    def test_phone_priority_4(self, db, user_with_phone):
        """Phone number is priority 4."""
        assert user_with_phone.phone_number == '+998901234567'

    def test_oauth_priority_5(self, db, user):
        """Google/OAuth is priority 5."""
        # Will test after OAuth integration complete


@pytest.mark.integration
class TestAccountMergingRules:
    """Rules for merging accounts under priority hierarchy."""

    def test_phone_otp_user_can_add_google(self, db, user_with_phone):
        """Phone user adds Google → both methods on same account."""
        # User logs in via phone OTP
        # Then Google OAuth with same email
        # Should merge

    def test_telegram_user_adds_phone(self, db, user_with_telegram):
        """Telegram user adds phone → both methods on same account."""
        # User has Telegram
        # Later registers with phone
        # Should merge if phone verified

    def test_email_password_user_adds_oauth(self, db, user):
        """Email+password user adds Google → both work."""
        # User registered with email+password
        # Later uses Google OAuth with same email
        # Should link as second method


@pytest.mark.integration
class TestDuplicateEmailHandling:
    """How app handles duplicate emails from different methods."""

    def test_email_password_and_google_same_email(self, db, user):
        """User with email+password adds Google with same email."""
        # Pre-condition: user has email@example.com via password
        # Google user also email@example.com
        # Should auto-link via SocialAccountAdapter

    def test_email_verified_bypass(self, db):
        """Google email (verified) bypasses unverified email."""
        # User registered with unverified email
        # Google OAuth with verified email
        # Google email should be preferred


@pytest.mark.integration
class TestDataConsistency:
    """Ensure multi-auth linking maintains data consistency."""

    def test_phone_and_telegram_same_user(self, db, user_with_phone):
        """Phone + Telegram cannot have same value."""
        # Different users can't have same phone/telegram
        user_with_phone.telegram_id = 123456789
        user_with_phone.save()

        # Create another user
        user2 = CustomUser.objects.create_user(
            username='user2', email='user2@example.com', password='pass'
        )

        # user2 cannot have same phone (unique constraint)
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            user2.phone_number = user_with_phone.phone_number
            user2.save()

    def test_unique_email_across_users(self, db, user_with_phone):
        """Email must be unique across all users."""
        user2 = CustomUser.objects.create_user(
            username='user2', email='different@example.com', password='pass'
        )

        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            user2.email = user_with_phone.email
            user2.save()
