"""Device sessions: refresh rotation (P1), revocation, device limit (P9)."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import DeviceSession
from apps.accounts.services.token_service import (
    create_token_pair,
    decode_access_token,
    is_session_revoked,
    revoke_session,
    revoke_token,
    rotate_refresh_token,
    verify_access_token,
)

DEV = 'device-aaaaaaaaaaaaaaaa'


@pytest.mark.unit
class TestTokenRotation:
    """rotate_refresh_token works from the refresh token alone (P1)."""

    def test_rotate_returns_new_pair(self, user):
        access, refresh = create_token_pair(user, DEV)
        issued = rotate_refresh_token(refresh)

        assert issued is not None
        assert issued.access != access
        assert issued.refresh != refresh
        assert verify_access_token(issued.access) == str(user.pk)

    def test_rotate_garbage_is_none(self, user):
        create_token_pair(user, DEV)
        assert rotate_refresh_token('invalid-token') is None
        assert rotate_refresh_token('0' * 32 + '.nope') is None
        assert rotate_refresh_token('') is None

    def test_wrong_secret_for_real_session_is_none(self, user):
        _, refresh = create_token_pair(user, DEV)
        sid = refresh.split('.', 1)[0]
        assert rotate_refresh_token(f'{sid}.wrong-secret') is None

    def test_user_deleted_after_issue(self, user):
        _, refresh = create_token_pair(user, DEV)
        user.delete()
        assert rotate_refresh_token(refresh) is None

    def test_inactive_user_cannot_refresh(self, user):
        _, refresh = create_token_pair(user, DEV)
        user.is_active = False
        user.save()
        assert rotate_refresh_token(refresh) is None

    def test_expired_session_cannot_refresh(self, user):
        _, refresh = create_token_pair(user, DEV)
        DeviceSession.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert rotate_refresh_token(refresh) is None

    def test_previous_token_accepted_within_grace(self, user):
        """Two tabs refreshing at once must not log the user out."""
        _, refresh = create_token_pair(user, DEV)
        assert rotate_refresh_token(refresh) is not None
        assert rotate_refresh_token(refresh) is not None

    def test_previous_token_replayed_after_grace_revokes_session(self, user):
        _, refresh = create_token_pair(user, DEV)
        newer = rotate_refresh_token(refresh)
        DeviceSession.objects.update(rotated_at=timezone.now() - timedelta(minutes=5))

        assert rotate_refresh_token(refresh) is None
        # theft signal: the legitimate holder newer token dies too
        assert rotate_refresh_token(newer.refresh) is None
        assert DeviceSession.objects.get().revoked_reason == 'reuse'

    def test_remember_flag_is_kept_across_rotation(self, user):
        _, refresh = create_token_pair(user, DEV, remember=False)
        rotate_refresh_token(refresh)
        assert DeviceSession.objects.get().remember is False


@pytest.mark.unit
class TestRevocation:
    def test_revoke_session_blocks_refresh_and_marks_access(self, user):
        access, refresh = create_token_pair(user, DEV)
        sid = decode_access_token(access)['sid']
        assert not is_session_revoked(sid)

        revoke_session(DeviceSession.objects.get())

        assert is_session_revoked(sid)
        assert rotate_refresh_token(refresh) is None

    def test_revoke_token_helper(self, user):
        _, refresh = create_token_pair(user, DEV)
        revoke_token(str(user.pk), DEV)
        assert rotate_refresh_token(refresh) is None


@pytest.mark.unit
class TestDeviceLimit:
    """P9: B2C accounts keep up to 3 devices; the 4th evicts the least recently used."""

    def _login(self, user, n):
        return create_token_pair(user, f'device-{n:0>16}')

    def test_three_devices_coexist(self, user):
        refreshes = [self._login(user, i)[1] for i in range(3)]
        # P9 regression: the first device is still logged in
        assert all(rotate_refresh_token(r) is not None for r in refreshes)

    def test_fourth_device_revokes_least_recently_used(self, user):
        refreshes = [self._login(user, i)[1] for i in range(3)]
        DeviceSession.objects.filter(device_id=f'device-{1:0>16}').update(
            last_used_at=datetime.now(UTC) - timedelta(hours=2)
        )
        DeviceSession.objects.filter(device_id=f'device-{2:0>16}').update(
            last_used_at=datetime.now(UTC) - timedelta(hours=1)
        )
        self._login(user, 3)

        assert rotate_refresh_token(refreshes[1]) is None
        assert rotate_refresh_token(refreshes[0]) is not None
        assert rotate_refresh_token(refreshes[2]) is not None
        assert DeviceSession.objects.filter(user=user, revoked_at__isnull=True).count() == 3

    def test_same_device_signing_in_again_does_not_use_a_slot(self, user):
        for _ in range(5):
            self._login(user, 0)
        assert DeviceSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1

    def test_org_single_device_policy_allows_one(self, user, member):
        assert member.organization.single_device_policy is True
        _, first = self._login(user, 0)
        self._login(user, 1)
        assert rotate_refresh_token(first) is None

    def test_org_multi_device_policy_allows_three(self, user, member):
        member.organization.single_device_policy = False
        member.organization.save()
        refreshes = [self._login(user, i)[1] for i in range(3)]
        assert all(rotate_refresh_token(r) is not None for r in refreshes)

    def test_expired_sessions_do_not_count(self, user):
        for i in range(3):
            self._login(user, i)
        DeviceSession.objects.update(expires_at=timezone.now() - timedelta(days=1))
        self._login(user, 9)
        assert DeviceSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1
