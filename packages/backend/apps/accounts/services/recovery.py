"""
Password recovery without email.

Channels:
  1. Telegram bot one-time link (15 min, single use, only the hash is stored)
  2. Org admin sets a temporary password (the user must change it at next sign-in)
  3. Platform admin request queue (PasswordResetRequest, handled in Django admin)
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomUser, PasswordResetRequest, RecoveryToken
from apps.accounts.services.password_policy import password_errors
from apps.accounts.services.security_notifications import notify
from apps.accounts.services.token_service import revoke_user_sessions
from core.utils import login_throttle
from core.utils import rate_limiter as rl

log = logging.getLogger(__name__)

TOKEN_TTL = timedelta(minutes=15)
REQUESTS_PER_HOUR = 3
IP_REQUESTS_PER_HOUR = 10
_TEMP_ALPHABET = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


class RecoveryError(Exception):
    def __init__(self, code: str, message: str, errors=None):
        self.code = code
        self.errors = errors or []
        super().__init__(message)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def allow_request(identifier: str, limit: int = REQUESTS_PER_HOUR) -> bool:
    """At most ``limit`` recovery requests per identifier per hour (fails open without Redis)."""
    key = f'recovery:req:{login_throttle.account_key(identifier)}'
    try:
        r = rl._redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, 3600, nx=True)
        count, _ = pipe.execute()
        return int(count) <= limit
    except Exception as e:
        log.error('Recovery limiter unavailable: %s', e)
        return True


def create_token(user: CustomUser) -> str:
    """New one-time token.

    Earlier unused tokens stay valid: otherwise anyone who knows a username could keep
    requesting links and invalidate the one the owner is about to use. The hourly limits
    cap how many are live; using any token (or a password change) retires the rest.
    """
    token = secrets.token_urlsafe(32)
    RecoveryToken.objects.create(
        user=user, token_hash=_hash(token), expires_at=timezone.now() + TOKEN_TTL
    )
    return token


def recovery_link(token: str) -> str:
    # In the fragment: it never reaches server logs, proxies or Referer headers.
    return f'{settings.FRONTEND_BASE_URL}/reset-password#token={token}'


def send_link_via_telegram(user: CustomUser, *, by_org_admin: bool = False) -> bool:
    """Create a token and queue the bot message. False when the account has no Telegram."""
    if not user.telegram_id or not user.is_active:
        return False
    event = 'recovery_link_admin' if by_org_admin else 'recovery_link'
    notify(user, event, link=recovery_link(create_token(user)))
    return True


# Higher number = more authority. Custom (non-system) roles rank between student and teacher.
ROLE_RANK = {'owner': 100, 'manager': 80, 'teacher': 60, 'observer': 40, 'student': 20}
CUSTOM_ROLE_RANK = 30


def _rank(role) -> int:
    return ROLE_RANK.get(role.name, CUSTOM_ROLE_RANK) if role.is_system_role else CUSTOM_ROLE_RANK


def org_admin_may_reset(actor: CustomUser, target: CustomUser) -> bool:
    """An org admin may start a reset only for a member with a STRICTLY lower role.

    Needs ``members:manage`` in a shared organization, and the target must not hold an
    equal or higher role in any organization the two share (an owner cannot be reset by
    a manager, a manager not by another manager). Platform staff and self are excluded.
    """
    if actor.pk == target.pk or target.is_staff or target.is_superuser:
        return False
    from apps.organizations.models import Membership, MembershipStatus

    active = Membership.objects.filter(status=MembershipStatus.ACTIVE).select_related('role')
    mine = {m.organization_id: m for m in active.filter(user=actor)}
    allowed = False
    for t in active.filter(user=target, organization_id__in=list(mine)):
        a = mine[t.organization_id]
        if _rank(t.role) >= _rank(a.role):
            return False
        if a.role.has_permission('members:manage'):
            allowed = True
    return allowed


def confirm_reset(token: str, new_password: str) -> CustomUser:
    """Spend ``token`` and set ``new_password``; raises RecoveryError."""
    row = (
        RecoveryToken.objects.select_related('user')
        .filter(token_hash=_hash(token or ''), used_at__isnull=True)
        .first()
    )
    if row is None or row.expires_at <= timezone.now() or not row.user.is_active:
        raise RecoveryError('invalid_token', 'The link is invalid or has expired.')
    user = row.user
    errs = password_errors(new_password, user=user)
    if errs:
        raise RecoveryError('validation_error', 'Password does not meet the policy.', errs)

    with transaction.atomic():
        # Conditional update: two parallel requests cannot both spend the token.
        spent = RecoveryToken.objects.filter(pk=row.pk, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        if spent != 1:
            raise RecoveryError('invalid_token', 'The link is invalid or has expired.')
        user.set_password(new_password)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])
        RecoveryToken.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
    revoke_user_sessions(user, reason='password')
    login_throttle.reset(login_throttle.account_key(f'user:{user.pk}'))
    notify(user, 'password_changed')
    return user


def generate_temporary_password() -> str:
    """A random 12-character password that passes the platform policy."""
    for _ in range(50):
        pw = ''.join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
        if (
            any(c.isdigit() for c in pw)
            and any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and not password_errors(pw)
        ):
            return pw
    raise RuntimeError('could not generate a policy-compliant password')  # pragma: no cover


def set_temporary_password(user: CustomUser, password: str | None = None) -> str:
    """Admin-issued temporary password: the user must replace it at next sign-in."""
    password = password or generate_temporary_password()
    errs = password_errors(password, user=user)
    if errs:
        raise RecoveryError('validation_error', 'Password does not meet the policy.', errs)
    user.set_password(password)
    user.must_change_password = True
    user.save(update_fields=['password', 'must_change_password'])
    revoke_user_sessions(user, reason='password')
    login_throttle.reset(login_throttle.account_key(f'user:{user.pk}'))
    notify(user, 'password_changed')
    return password


def queue_request(username: str, contact_note: str) -> None:
    """Add to the platform-admin queue unless an identical open request already exists."""
    user = CustomUser.objects.filter(username__iexact=username).first()
    pending = PasswordResetRequest.objects.filter(status=PasswordResetRequest.Status.PENDING)
    if user is not None and pending.filter(user=user).exists():
        return
    if (
        user is None
        and pending.filter(user__isnull=True, username_entered__iexact=username).exists()
    ):
        return
    PasswordResetRequest.objects.create(
        user=user, username_entered=username[:150], contact_note=contact_note[:300]
    )
