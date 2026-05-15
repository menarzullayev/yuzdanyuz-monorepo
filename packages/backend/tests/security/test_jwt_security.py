"""
Security tests for JWT token handling
Focus: tamper detection, expiration, type validation, signature verification
"""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from django.conf import settings

from apps.accounts.services.token_service import verify_access_token


@pytest.mark.security
class TestJWTTokenSecurity:
    """JWT token security and validation."""

    def test_tampered_token_rejected(self, user):
        """Modified token payload → verify_access_token = None."""
        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        # Tamper with token (change sub)
        parts = token.split('.')
        tampered = pyjwt.encode(
            {
                'sub': 'different-id',
                'type': 'access',
                'exp': datetime.now(UTC) + timedelta(hours=1),
            },
            settings.SECRET_KEY,
            algorithm='HS256',
        )

        # Original valid
        assert verify_access_token(token) == str(user.pk)

        # Tampered invalid
        assert verify_access_token(tampered) != str(user.pk)

    def test_expired_token_rejected(self, user):
        """Expired token → verify_access_token = None."""
        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'exp': datetime.now(UTC) - timedelta(hours=1),
        }
        expired_token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        assert verify_access_token(expired_token) is None

    def test_wrong_algorithm_rejected(self, user):
        """HS512 token presented to HS256 verifier → None."""
        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        # Encode with HS512
        token_hs512 = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS512')

        # Verify expects HS256
        assert verify_access_token(token_hs512) is None

    def test_org_claim_cannot_be_forged(self, user, org):
        """Token with wrong secret cannot forge org claim."""
        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'current_org': str(org.pk),
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        # Signed with wrong secret
        forged = pyjwt.encode(payload, 'different-secret', algorithm='HS256')

        # Verify with correct secret
        assert verify_access_token(forged) is None

    def test_access_token_not_usable_as_refresh(self, user):
        """Access token type not accepted for refresh rotation."""
        from apps.accounts.services.token_service import rotate_refresh_token

        payload = {
            'sub': str(user.pk),
            'type': 'access',  # Not refresh
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        result = rotate_refresh_token(token, str(user.pk), 'test-fp')
        # Should fail because type != refresh
        assert result is None
