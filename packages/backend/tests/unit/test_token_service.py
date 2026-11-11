"""
Unit tests for apps/accounts/services/token_service.py
Focus: JWT creation, verification, org claim, refresh rotation
"""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from django.conf import settings

from apps.accounts.services.token_service import (
    create_token_pair,
    verify_access_token,
)


@pytest.mark.unit
class TestTokenCreation:
    """create_token_pair and _create_access_token tests."""

    def test_create_token_pair_returns_tuple(self, user):
        """Returns (access, refresh) tuple."""
        access, refresh = create_token_pair(user, 'test-fp')
        assert isinstance(access, str)
        assert isinstance(refresh, str)
        assert len(access) > 0
        assert len(refresh) > 0

    def test_access_token_has_sub_claim(self, user):
        """Access token payload has 'sub' (user_id)."""
        access, _ = create_token_pair(user, 'test-fp')
        payload = pyjwt.decode(access, settings.SECRET_KEY, algorithms=['HS256'])
        assert payload['sub'] == str(user.pk)

    def test_access_token_has_type_access(self, user):
        """Access token has type='access'."""
        access, _ = create_token_pair(user, 'test-fp')
        payload = pyjwt.decode(access, settings.SECRET_KEY, algorithms=['HS256'])
        assert payload['type'] == 'access'

    def test_access_token_has_current_org_when_org_provided(self, user, org):
        """org param → JWT payload has 'current_org' claim."""
        access, _ = create_token_pair(user, 'test-fp', org=org)
        payload = pyjwt.decode(access, settings.SECRET_KEY, algorithms=['HS256'])
        assert payload['current_org'] == str(org.pk)

    def test_access_token_no_current_org_when_none(self, user):
        """org=None → no 'current_org' key in payload."""
        access, _ = create_token_pair(user, 'test-fp', org=None)
        payload = pyjwt.decode(access, settings.SECRET_KEY, algorithms=['HS256'])
        assert 'current_org' not in payload

    def test_access_token_has_exp_claim(self, user):
        """Access token has 'exp' (expiration)."""
        access, _ = create_token_pair(user, 'test-fp')
        payload = pyjwt.decode(
            access, settings.SECRET_KEY, algorithms=['HS256'], options={'verify_exp': False}
        )
        assert 'exp' in payload
        assert 'iat' in payload

    def test_refresh_token_stored_hashed(self, user):
        from apps.accounts.models import DeviceSession
        from apps.accounts.services.token_service import _hash_secret

        _, refresh = create_token_pair(user, 'test-fp-123456789012')

        session = DeviceSession.objects.get(user=user)
        assert refresh.startswith(session.pk.hex + '.')
        assert session.refresh_hash == _hash_secret(refresh.split('.', 1)[1])
        assert refresh not in (session.refresh_hash, session.prev_refresh_hash)


@pytest.mark.unit
class TestTokenVerification:
    """verify_access_token tests."""

    def test_verify_access_token_valid(self, user):
        """Valid token → user_id returned."""
        access, _ = create_token_pair(user, 'test-fp')
        user_id = verify_access_token(access)
        assert user_id == str(user.pk)

    def test_verify_access_token_expired(self, user):
        """Expired token → None."""
        access, _ = create_token_pair(user, 'test-fp')

        # Manually create expired token

        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'exp': datetime.now(UTC) - timedelta(hours=1),
        }
        expired_token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        assert verify_access_token(expired_token) is None

    def test_verify_access_token_wrong_type(self, user):
        """Refresh token → None (wrong type)."""
        # Create a token with type='refresh'

        payload = {
            'sub': str(user.pk),
            'type': 'refresh',
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        refresh_token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        assert verify_access_token(refresh_token) is None

    def test_verify_access_token_invalid(self, user):
        """Gibberish token → None."""
        assert verify_access_token('invalid.token.here') is None

    def test_verify_access_token_valid_returns_user_id(self, user):
        """Valid token with correct secret → returns user_id string."""
        access, _ = create_token_pair(user, 'test-fp')
        # Token is signed with settings.SECRET_KEY
        user_id = verify_access_token(access)
        assert user_id == str(user.pk)
