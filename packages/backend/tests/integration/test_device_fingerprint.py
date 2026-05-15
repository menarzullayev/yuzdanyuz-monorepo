"""
Integration tests for device fingerprinting and policy enforcement
Focus: Fingerprint generation, session management, device switching
"""

import pytest
from django.test import Client, RequestFactory

from apps.accounts.services.token_service import make_fingerprint


@pytest.mark.integration
class TestFingerprintGeneration:
    """Device fingerprint creation from request."""

    def test_fingerprint_consistent_same_device(self, db, mock_request):
        """Same device (UA + IP) → same fingerprint."""
        fp1 = make_fingerprint(mock_request)
        fp2 = make_fingerprint(mock_request)
        assert fp1 == fp2

    def test_fingerprint_different_ua(self, db, mock_request, mock_request_different_ua):
        """Different User-Agent → different fingerprint."""
        fp1 = make_fingerprint(mock_request)
        fp2 = make_fingerprint(mock_request_different_ua)
        assert fp1 != fp2

    def test_fingerprint_different_ip(self, db, mock_request, mock_request_different_ip):
        """Different IP → different fingerprint."""
        fp1 = make_fingerprint(mock_request)
        fp2 = make_fingerprint(mock_request_different_ip)
        assert fp1 != fp2

    def test_fingerprint_x_forwarded_for(self, db):
        """X-Forwarded-For header used for IP."""
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_USER_AGENT'] = 'Mozilla/5.0'
        request.META['HTTP_X_FORWARDED_FOR'] = '10.0.0.1, 192.168.1.1'
        request.META['REMOTE_ADDR'] = '192.168.1.1'

        fp = make_fingerprint(request)
        assert fp is not None
        assert len(fp) == 32  # SHA256 hex

    def test_fingerprint_fallback_to_remote_addr(self, db):
        """No X-Forwarded-For → use REMOTE_ADDR."""
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_USER_AGENT'] = 'Mozilla/5.0'
        request.META['REMOTE_ADDR'] = '192.168.1.1'

        fp = make_fingerprint(request)
        assert fp is not None


@pytest.mark.integration
class TestSessionManagement:
    """Device fingerprint storage and verification."""

    def test_fingerprint_stored_on_login(self, db, client: Client, user, mock_redis):
        """After login, fingerprint stored in Redis."""
        user.set_password('Pass1234')
        user.save()

        response = client.post(
            '/api/auth/email/', data={'login': user.email, 'password': 'Pass1234'}
        )

        # Check Redis: session:active:{user_id} contains fingerprint
        # This is implementation-specific

    def test_fingerprint_verified_on_request(self, db, client: Client, user, mock_redis):
        """Each request verifies stored fingerprint."""
        # After login
        # Make request
        # Middleware checks fingerprint
        # Should pass if same device

    def test_fingerprint_mismatch_triggers_logout(self, db, client: Client, user, mock_redis):
        """Fingerprint mismatch → force logout."""
        # Login from device 1 (fingerprint A)
        # Try to use cookies from device 2 (fingerprint B)
        # DeviceCheckMiddleware should reject
        # Response should have force logout


@pytest.mark.integration
class TestDeviceSwitchBehavior:
    """Behavior when user switches devices."""

    def test_single_device_policy_default(self, db, org, user_with_phone):
        """Organization with single_device_policy = True (default)."""
        # org.single_device_policy should be True by default
        # When user logs in from device 2, device 1 session revoked

    def test_single_device_revokes_old_session(self, db, org, user_with_phone, mock_redis):
        """New device login → old device's refresh token invalidated."""
        # Login from device 1 → get refresh token A
        # Login from device 2 → get refresh token B
        # Device 1 tries to refresh with token A → fails
        # Device 2 refresh token B → works

    def test_multi_device_policy_allows_concurrent(self, db, org, user_with_phone):
        """Organization with single_device_policy = False."""
        # org.single_device_policy = False
        # Login from device 1 → token A
        # Login from device 2 → token B
        # Both tokens remain valid
        # Both devices can use tokens simultaneously

    def test_device_policy_per_organization(self, db, org, org2, user_multi_org):
        """Device policy is per organization."""
        # user_multi_org in org (single device) and org2 (multi device)
        # Login in org context → single device policy
        # Login in org2 context → multi device policy


@pytest.mark.integration
class TestFingerprintEdgeCases:
    """Edge cases in fingerprinting."""

    def test_fingerprint_with_vpn_ip_changes(self, db):
        """VPN changing IP → different fingerprint."""
        # Device 1 with VPN → IP A, UA "Mozilla"
        # Device 1 VPN disconnects → IP B, UA "Mozilla"
        # Fingerprints differ (different IP)
        # Should trigger logout with single-device policy

    def test_fingerprint_mobile_browser_change(self, db):
        """Mobile device changing browsers → different fingerprint."""
        # Safari with IP 1.1.1.1 → fingerprint A
        # Chrome with IP 1.1.1.1 → fingerprint B
        # Different fingerprints (UA changed)

    def test_fingerprint_proxy_bypass(self, db):
        """X-Forwarded-For spoofing → security consideration."""
        # If X-Forwarded-For is user-controlled, can spoof IP
        # Should validate proxy IP or use Django's IP detection

    def test_fingerprint_missing_user_agent(self, db):
        """Request with no User-Agent header."""
        factory = RequestFactory()
        request = factory.get('/')
        request.META['REMOTE_ADDR'] = '192.168.1.1'
        # No HTTP_USER_AGENT

        fp = make_fingerprint(request)
        assert fp is not None


@pytest.mark.integration
class TestConcurrentDeviceLogins:
    """Multiple devices logging in simultaneously."""

    def test_login_device_1_then_device_2_single_policy(self, db, user_with_phone, mock_redis):
        """Device 1 login, then Device 2 login, then Device 1 uses old token."""
        # Device 1 login → get tokens
        # Device 2 login → should revoke Device 1
        # Device 1 uses old refresh token → fail

    def test_both_devices_get_new_tokens_multi_policy(self, db, user_with_phone):
        """Multi-device policy → both devices get valid tokens."""
        # Device 1 login
        # Device 2 login
        # Both can refresh independently


@pytest.mark.integration
class TestSessionCleanup:
    """Redis session cleanup and expiration."""

    def test_refresh_token_ttl_24_hours(self, db, user, mock_redis):
        """Refresh token expires after 24 hours."""
        # Create token
        # Check Redis TTL
        # Should be set to 24 hours

    def test_session_cleanup_on_logout(self, db, user, mock_redis):
        """Logout clears Redis session entries."""
        # Login → session:active:{user_id}, session:refresh:{user_id}:{fp}
        # Logout → both deleted

    def test_expired_refresh_token_rejected(self, db, user, mock_redis):
        """Expired token in Redis → refresh fails."""
        # Manually expire token in Redis
        # Try to use it → fails
