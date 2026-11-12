"""
JWT access tokens + DB-backed device sessions.

Model:
  - Access token: 15 minutes, HttpOnly cookie, carries ``sid`` (the session id).
  - Refresh token: ``<session id>.<secret>`` in an HttpOnly cookie. Only the
    SHA-256 of the secret is stored (``DeviceSession.refresh_hash``). Refresh
    needs the refresh cookie alone (the access cookie is already gone after
    15 minutes of inactivity).
  - Device: a random id in the HttpOnly ``device_id`` cookie. IP and
    User-Agent are NOT part of the identity.
  - Limits: up to ``AUTH_MAX_DEVICES`` (3) active sessions per account; the
    least recently used one is revoked when a new device signs in. An org
    with ``single_device_policy`` allows exactly one.
  - "Remember me": persistent cookies and a 30-day sliding session. Without
    it the cookies are browser-session cookies and the server side session
    slides over ``AUTH_SESSION_HOURS``.

Redis holds only one thing: ``session:revoked:{sid}`` markers (TTL a bit over
the access token lifetime) so a revoked device loses access immediately
instead of when its access token expires.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import redis
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomUser, DeviceSession
from apps.accounts.services.user_agents import parse_user_agent
from core.infra import redis_from_url

# ── Settings ────────────────────────────────────────────────
ACCESS_TOKEN_TTL = timedelta(minutes=15)
# Kept for callers that import it; the effective value is _remember_ttl().
REFRESH_TOKEN_TTL = timedelta(days=30)
REFRESH_GRACE_SECONDS = 10
DEVICE_COOKIE_MAX_AGE = 365 * 24 * 3600

_redis_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_from_url(
            getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
            decode_responses=True,
        )
    return _redis_client


def _remember_ttl() -> timedelta:
    return timedelta(days=int(getattr(settings, 'AUTH_REMEMBER_DAYS', 30)))


def _session_ttl() -> timedelta:
    return timedelta(hours=int(getattr(settings, 'AUTH_SESSION_HOURS', 24)))


def _refresh_cookie_path() -> str:
    return getattr(settings, 'AUTH_REFRESH_COOKIE_PATH', '/api/auth/')


def max_devices_for(user: CustomUser) -> int:
    """Org with ``single_device_policy`` -> 1; everyone else (B2C included) -> 3."""
    org = user.primary_organization
    if org is not None and org.single_device_policy:
        return 1
    return int(getattr(settings, 'AUTH_MAX_DEVICES', 3))


# ── Device id ───────────────────────────────────────────────


def new_device_id() -> str:
    return secrets.token_urlsafe(24)


def get_device_id(request) -> tuple[str, bool]:
    """Return ``(device_id, is_new)`` from the HttpOnly cookie, minting one if absent/garbled."""
    raw = request.COOKIES.get('device_id', '')
    if 16 <= len(raw) <= 64 and all(c.isalnum() or c in '-_' for c in raw):
        return raw, False
    return new_device_id(), True


# ── Token helpers ───────────────────────────────────────────


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _make_refresh(session_id: uuid.UUID) -> tuple[str, str]:
    secret = secrets.token_urlsafe(48)
    return f'{session_id.hex}.{secret}', _hash_secret(secret)


def _split_refresh(value: str) -> tuple[uuid.UUID, str] | None:
    sid_hex, sep, secret = (value or '').partition('.')
    if not sep or not secret or len(sid_hex) != 32:
        return None
    try:
        return uuid.UUID(hex=sid_hex), secret
    except ValueError:
        return None


def _create_access_token(user: CustomUser, org=None, sid: uuid.UUID | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        'sub': str(user.pk),
        'type': 'access',
        'iat': now,
        'exp': now + ACCESS_TOKEN_TTL,
        'jti': str(uuid.uuid4()),
    }
    if sid is not None:
        payload['sid'] = sid.hex
    if org is not None:
        payload['current_org'] = str(org.pk)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def decode_access_token(token: str, *, check_revocation: bool = True) -> dict | None:
    """Validated payload of an access token, or None.

    Signature and expiry alone are not enough: a token whose session was revoked
    (logout, "sign out device", password change) or whose own ``jti`` was denied
    is rejected here, so every consumer (HTTP backend, WebSocket auth) agrees.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    except jwt.PyJWTError:
        return None
    if payload.get('type') != 'access' or not payload.get('sub'):
        return None
    if check_revocation and is_access_revoked(payload):
        return None
    return payload


def verify_access_token(token: str) -> str | None:
    """Access token -> user_id (str UUID), or None when invalid/expired/revoked."""
    payload = decode_access_token(token)
    return payload['sub'] if payload else None


def _denied_key(jti: str) -> str:
    return f'access:denied:{jti}'


def is_access_revoked(payload: dict) -> bool:
    """True when the token's jti is denied or its session is revoked."""
    keys = []
    if payload.get('jti'):
        keys.append(_denied_key(str(payload['jti'])))
    if payload.get('sid'):
        keys.append(f'session:revoked:{payload["sid"]}')
    if not keys:
        return False
    try:
        return bool(_redis().exists(*keys))
    except Exception:  # Redis down: the DB-side session revocation still stops refresh.
        return False


def deny_access_token(token: str) -> None:
    """Put this access token's jti on the denylist until it would have expired anyway."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=['HS256'], options={'verify_exp': False}
        )
    except jwt.PyJWTError:
        return
    jti, exp = payload.get('jti'), payload.get('exp')
    if not jti or not isinstance(exp, int | float):
        return
    ttl = int(exp - datetime.now(UTC).timestamp()) + 1
    if ttl <= 0:
        return
    try:
        _redis().set(
            _denied_key(str(jti)), '1', ex=min(ttl, int(ACCESS_TOKEN_TTL.total_seconds()) + 60)
        )
    except Exception:
        pass


# ── Issuing sessions ────────────────────────────────────────


@dataclass
class IssuedSession:
    access: str
    # None when the presented token was the previous one inside the grace window:
    # the browser already holds (or is about to receive) the current refresh token.
    refresh: str | None
    session: DeviceSession
    device_id: str
    remember: bool


def _geo(request) -> tuple[str, str]:
    from core.utils.client_ip import get_trusted_proxy_header

    country = get_trusted_proxy_header(request, 'CF-IPCountry').upper()
    if len(country) != 2 or not country.isalpha() or country in ('XX', 'T1'):
        country = ''
    city = get_trusted_proxy_header(request, 'CF-IPCity')[:100]
    return country, city


def _describe(request) -> dict:
    from core.utils.client_ip import get_client_ip

    ua = (request.META.get('HTTP_USER_AGENT') or '')[:300]
    country, city = _geo(request)
    ip = get_client_ip(request)
    return {
        'user_agent': ua,
        'device_label': parse_user_agent(ua).label,
        'ip_address': ip if ip != '0.0.0.0' else None,
        'country': country,
        'city': city,
    }


def _expiry(remember: bool) -> datetime:
    return timezone.now() + (_remember_ttl() if remember else _session_ttl())


def _mark_revoked(session_id: uuid.UUID) -> None:
    try:
        _redis().set(
            f'session:revoked:{session_id.hex}',
            '1',
            ex=int(ACCESS_TOKEN_TTL.total_seconds()) + 60,
        )
    except Exception:  # Redis down: the DB row is still revoked, access tokens just age out.
        pass


def is_session_revoked(sid: str) -> bool:
    try:
        return bool(_redis().exists(f'session:revoked:{sid}'))
    except Exception:
        return False


def revoke_session(session: DeviceSession, reason: str = 'logout') -> None:
    if session.revoked_at is None:
        DeviceSession.objects.filter(pk=session.pk, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason=reason
        )
    _mark_revoked(session.pk)


def revoke_user_sessions(
    user: CustomUser, *, except_session_id=None, reason: str = 'revoked'
) -> int:
    """Revoke every active session of ``user`` (optionally sparing one). Returns how many."""
    qs = DeviceSession.objects.filter(user=user, revoked_at__isnull=True)
    if except_session_id is not None:
        qs = qs.exclude(pk=except_session_id)
    ids = list(qs.values_list('pk', flat=True))
    if not ids:
        return 0
    DeviceSession.objects.filter(pk__in=ids, revoked_at__isnull=True).update(
        revoked_at=timezone.now(), revoked_reason=reason
    )
    for sid in ids:
        _mark_revoked(sid)
    return len(ids)


def active_sessions(user: CustomUser):
    return DeviceSession.objects.filter(
        user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).order_by('-last_used_at')


def _create_session(user: CustomUser, device_id: str, remember: bool, info: dict, org):
    """Create the session row after making room under the device limit."""
    with transaction.atomic():
        # Serialise concurrent logins of one account so the device cap holds.
        CustomUser.objects.select_for_update().only('pk').get(pk=user.pk)
        now = timezone.now()
        live = DeviceSession.objects.filter(user=user, revoked_at__isnull=True)

        # Expired rows still counting as "active" would evict real devices.
        for stale in live.filter(expires_at__lte=now):
            revoke_session(stale, 'expired')
        # Signing in again on the same device replaces its session.
        for same in live.filter(device_id=device_id, expires_at__gt=now):
            revoke_session(same, 'replaced')

        limit = max_devices_for(user)
        keep = list(live.filter(expires_at__gt=now).order_by('last_used_at'))
        keep = [s for s in keep if s.revoked_at is None and s.device_id != device_id]
        # Free one slot for the new session.
        while len(keep) >= limit:
            revoke_session(keep.pop(0), 'device_limit')

        session_id = uuid.uuid4()
        refresh, refresh_hash = _make_refresh(session_id)
        session = DeviceSession.objects.create(
            id=session_id,
            user=user,
            device_id=device_id,
            refresh_hash=refresh_hash,
            remember=remember,
            expires_at=_expiry(remember),
            **info,
        )
    access = _create_access_token(user, org=org, sid=session_id)
    return IssuedSession(access, refresh, session, device_id, remember)


def issue_session(request, user: CustomUser, *, remember: bool, org=None) -> IssuedSession:
    """Create a device session for ``user`` and the token pair for its cookies."""
    device_id, is_new = get_device_id(request)
    if is_new:
        # Lets the response middleware set the cookie for flows that do not
        # build their own response (allauth's OAuth callback).
        request._device_id_to_set = device_id
    issued = _create_session(user, device_id, remember, _describe(request), org)
    return issued


def issue_login(request, user: CustomUser, *, remember: bool = True) -> IssuedSession:
    """``issue_session`` with the user's primary organization in the access token."""
    return issue_session(request, user, remember=remember, org=user.primary_organization)


def create_token_pair(
    user: CustomUser,
    device_id: str,
    org=None,
    *,
    remember: bool = True,
    request=None,
) -> tuple[str, str]:
    """Low-level: session for an explicit device id -> ``(access, refresh)``.

    ``request`` (optional) only supplies the User-Agent/IP shown in the sessions list.
    """
    info = _describe(request) if request is not None else {}
    issued = _create_session(user, device_id, remember, info, org)
    return issued.access, issued.refresh


# ── Rotation ────────────────────────────────────────────────


def rotate_refresh_token(
    refresh_value: str, request=None, device_id: str | None = None
) -> IssuedSession | None:
    """Exchange a refresh token for a new pair, from the refresh token alone.

    Returns None for unknown/expired/revoked sessions and wrong secrets. Replaying
    the *previous* token after its grace window revokes the session (reuse
    detection); within the window it is accepted so concurrent refreshes from
    two tabs do not log the user out.
    """
    parsed = _split_refresh(refresh_value)
    if parsed is None:
        return None
    session_id, secret = parsed
    presented = _hash_secret(secret)

    with transaction.atomic():
        session = (
            DeviceSession.objects.select_for_update()
            .select_related('user')
            .filter(pk=session_id, revoked_at__isnull=True)
            .first()
        )
        if session is None:
            return None
        now = timezone.now()
        if session.expires_at <= now:
            revoke_session(session, 'expired')
            return None
        user = session.user
        if not user.is_active:
            revoke_session(session, 'inactive')
            return None

        # A refresh token only works together with the device cookie it was issued for.
        if device_id is not None and not hmac.compare_digest(
            device_id.encode(), session.device_id.encode()
        ):
            return None

        is_current = hmac.compare_digest(presented, session.refresh_hash)
        is_prev = bool(session.prev_refresh_hash) and hmac.compare_digest(
            presented, session.prev_refresh_hash
        )
        if not is_current:
            if not (is_prev and session.rotated_at is not None):
                return None
            if (now - session.rotated_at).total_seconds() > REFRESH_GRACE_SECONDS:
                revoke_session(session, 'reuse')
                return None
            # Second tab inside the grace window: hand out an access token only and do
            # NOT rotate again. Rotating twice would leave the shared cookie jar holding
            # an already-superseded token and turn the race into a 'reuse' revocation.
            DeviceSession.objects.filter(pk=session.pk).update(last_used_at=now)
            access = _create_access_token(user, org=user.primary_organization, sid=session.pk)
            return IssuedSession(access, None, session, session.device_id, session.remember)

        new_refresh, new_hash = _make_refresh(session.pk)
        updates = {
            'refresh_hash': new_hash,
            'prev_refresh_hash': session.refresh_hash,
            'rotated_at': now,
            'last_used_at': now,
            'expires_at': _expiry(session.remember),
        }
        if request is not None:
            info = _describe(request)
            updates.update(
                {k: v for k, v in info.items() if v and k in ('ip_address', 'country', 'city')}
            )
        DeviceSession.objects.filter(pk=session.pk).update(**updates)
        for k, v in updates.items():
            setattr(session, k, v)

    access = _create_access_token(user, org=user.primary_organization, sid=session.pk)
    return IssuedSession(access, new_refresh, session, session.device_id, session.remember)


def session_from_refresh(refresh_value: str) -> DeviceSession | None:
    """The live session a refresh cookie points at, without rotating it (for logout)."""
    parsed = _split_refresh(refresh_value)
    if parsed is None:
        return None
    session_id, secret = parsed
    session = DeviceSession.objects.filter(pk=session_id, revoked_at__isnull=True).first()
    if session is None:
        return None
    presented = _hash_secret(secret)
    if hmac.compare_digest(presented, session.refresh_hash) or (
        session.prev_refresh_hash and hmac.compare_digest(presented, session.prev_refresh_hash)
    ):
        return session
    return None


def revoke_token(user_id: str, device_id: str) -> None:
    """Logout helper: revoke the user's active session for ``device_id``."""
    for s in DeviceSession.objects.filter(
        user_id=user_id, device_id=device_id, revoked_at__isnull=True
    ):
        revoke_session(s, 'logout')


# ── Cookies ─────────────────────────────────────────────────


def set_auth_cookies(
    response,
    access: str,
    refresh: str | None,
    is_secure: bool = False,
    remember: bool = True,
    device_id: str | None = None,
) -> None:
    """Write the auth cookies. ``remember=False`` makes browser-session cookies."""
    response.set_cookie(
        'access_token',
        access,
        max_age=int(ACCESS_TOKEN_TTL.total_seconds()) if remember else None,
        httponly=True,
        secure=is_secure,
        samesite='Lax',
        path='/',
    )
    if refresh is not None:
        response.set_cookie(
            'refresh_token',
            refresh,
            max_age=int(_remember_ttl().total_seconds()) if remember else None,
            httponly=True,
            secure=is_secure,
            samesite='Lax',
            path=_refresh_cookie_path(),
        )
    if device_id:
        set_device_cookie(response, device_id, is_secure)


def set_device_cookie(response, device_id: str, is_secure: bool = False) -> None:
    response.set_cookie(
        'device_id',
        device_id,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=is_secure,
        samesite='Lax',
        path='/',
    )


def clear_auth_cookies(response) -> None:
    """Delete both auth cookies with the same path they were set with."""
    response.delete_cookie('access_token', path='/', samesite='Lax')
    response.delete_cookie('refresh_token', path=_refresh_cookie_path(), samesite='Lax')


def attach_session(response, issued: IssuedSession, request) -> None:
    """Set all cookies for a freshly issued session on ``response``."""
    from django.conf import settings as dj_settings

    secure = request.is_secure() or not dj_settings.DEBUG
    set_auth_cookies(
        response,
        issued.access,
        issued.refresh,
        is_secure=secure,
        remember=issued.remember,
        device_id=issued.device_id,
    )
