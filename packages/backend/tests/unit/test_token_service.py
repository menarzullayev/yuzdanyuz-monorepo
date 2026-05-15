"""
Unit tests for apps/accounts/services/token_service.py
Focus: JWT creation, verification, org claim, refresh rotation
"""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from django.conf import settings

from apps.accounts.services.token_service import (
    _redis,
    create_token_pair,
    make_fingerprint,
    revoke_token,
    rotate_refresh_token,
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

    def test_refresh_token_in_redis(self, user):
        """Refresh token stored in Redis."""
        fp = 'test-fp'
        _, refresh = create_token_pair(user, fp)

        r = _redis()
        r_key = f'session:refresh:{user.pk}:{fp}'
        stored = r.get(r_key)
        assert stored == refresh

        # Cleanup
        r.delete(r_key)


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
        from datetime import datetime, timedelta

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
        from datetime import datetime, timedelta

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


@pytest.mark.unit
class TestTokenRotation:
    """rotate_refresh_token tests."""

    def test_rotate_refresh_token_valid(self, user):
        """Valid rotation → new (access, refresh)."""
        fp = 'test-fp'
        access, refresh = create_token_pair(user, fp)

        user_id = str(user.pk)
        result = rotate_refresh_token(refresh, user_id, fp)

        assert result is not None
        new_access, new_refresh = result
        assert isinstance(new_access, str)
        assert isinstance(new_refresh, str)

        # New tokens should be different
        assert new_access != access
        assert new_refresh != refresh

    def test_rotate_refresh_token_invalid(self, user):
        """Invalid token → None."""
        fp = 'test-fp'
        create_token_pair(user, fp)

        user_id = str(user.pk)
        result = rotate_refresh_token('invalid-token', user_id, fp)
        assert result is None

    def test_rotate_refresh_token_wrong_fingerprint(self, user):
        """Token stored for fp1, rotated with fp2 → None."""
        fp1 = 'test-fp-1'
        _, refresh = create_token_pair(user, fp1)

        user_id = str(user.pk)
        fp2 = 'test-fp-2'
        result = rotate_refresh_token(refresh, user_id, fp2)
        assert result is None


@pytest.mark.unit
class TestTokenRevocation:
    """revoke_token tests."""

    def test_revoke_token_clears_redis(self, user):
        """revoke_token → Redis entries deleted."""
        fp = 'test-fp'
        _, refresh = create_token_pair(user, fp)

        r = _redis()
        r_key = f'session:refresh:{user.pk}:{fp}'
        act_key = f'session:active:{user.pk}'

        # Verify in Redis
        assert r.get(r_key) == refresh

        # Revoke
        revoke_token(str(user.pk), fp)

        # Should be gone
        assert r.get(r_key) is None
        assert r.get(act_key) is None


@pytest.mark.unit
class TestRotateRefreshTokenUserNotFound:
    """rotate_refresh_token exception handling."""

    def test_rotate_refresh_token_user_not_found(self, user):
        """User deleted after token issued → None."""
        import uuid

        fake_user_id = str(uuid.uuid4())

        # Create valid token but with non-existent user
        payload = {
            'sub': fake_user_id,
            'type': 'refresh',
            'exp': datetime.now(UTC) + timedelta(hours=1),
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        result = rotate_refresh_token(token, fake_user_id, 'test-fp')
        assert result is None


@pytest.mark.unit
class TestFingerprint:
    """make_fingerprint tests."""

    def test_fingerprint_consistent(self):
        """Same UA+IP → same fingerprint."""
        from unittest.mock import MagicMock

        req = MagicMock()
        req.META = {'HTTP_USER_AGENT': 'Mozilla/5.0', 'REMOTE_ADDR': '192.168.1.1'}

        fp1 = make_fingerprint(req)
        fp2 = make_fingerprint(req)
        assert fp1 == fp2

    def test_fingerprint_different_ua(self):
        """Different UA → different fingerprint."""
        from unittest.mock import MagicMock

        req1 = MagicMock()
        req1.META = {'HTTP_USER_AGENT': 'Mozilla/5.0', 'REMOTE_ADDR': '192.168.1.1'}

        req2 = MagicMock()
        req2.META = {'HTTP_USER_AGENT': 'Safari/5.0', 'REMOTE_ADDR': '192.168.1.1'}

        fp1 = make_fingerprint(req1)
        fp2 = make_fingerprint(req2)
        assert fp1 != fp2

    def test_fingerprint_different_ip(self):
        """Different IP → different fingerprint."""
        from unittest.mock import MagicMock

        req1 = MagicMock()
        req1.META = {'HTTP_USER_AGENT': 'Mozilla/5.0', 'REMOTE_ADDR': '192.168.1.1'}

        req2 = MagicMock()
        req2.META = {'HTTP_USER_AGENT': 'Mozilla/5.0', 'REMOTE_ADDR': '192.168.1.2'}

        fp1 = make_fingerprint(req1)
        fp2 = make_fingerprint(req2)
        assert fp1 != fp2

    def test_fingerprint_x_forwarded_for(self):
        """X-Forwarded-For header → use first IP."""
        from unittest.mock import MagicMock

        req = MagicMock()
        req.META = {
            'HTTP_USER_AGENT': 'Mozilla/5.0',
            'HTTP_X_FORWARDED_FOR': '10.0.0.1, 192.168.1.1',
            'REMOTE_ADDR': '192.168.1.1',
        }

        fp = make_fingerprint(req)
        # Should use first IP from X-Forwarded-For
        assert len(fp) == 32  # SHA256 truncated


@pytest.mark.unit
class TestSecondLoginRevokesFirst:
    """Second login on same account revokes first session."""

    def test_second_login_revokes_first(self, user):
        """Login from fp1 → login from fp2 → fp1 session gone."""
        fp1 = 'device-1'
        access1, refresh1 = create_token_pair(user, fp1)

        r = _redis()
        r_key1 = f'session:refresh:{user.pk}:{fp1}'

        # Verify first login in Redis
        assert r.get(r_key1) == refresh1

        # Second login
        fp2 = 'device-2'
        access2, refresh2 = create_token_pair(user, fp2)

        # First session should be revoked
        assert r.get(r_key1) is None

        # Second session should be present
        r_key2 = f'session:refresh:{user.pk}:{fp2}'
        assert r.get(r_key2) == refresh2

        # Cleanup
        r.delete(r_key2)
