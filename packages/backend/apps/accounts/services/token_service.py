"""
JWT + Redis session tizimi.

Arxitektura:
  - Access token:  15 daqiqa, httpOnly cookie
  - Refresh token: 30 kun, Redis da + httpOnly cookie
  - Device lock:   User-Agent + IP → fingerprint → Redis key
                   Yangi qurilma kirsa — oldingi session o'chiriladi (bir akkaunt = bir qurilma)

Redis key pattern:
  session:refresh:{user_id}:{fingerprint}  → refresh_token (string, TTL=30d)
  session:active:{user_id}                 → fingerprint   (string, TTL=30d)
                                             (joriy faol qurilmani kuzatish uchun)
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import redis
from django.conf import settings

from apps.accounts.models import CustomUser


# ── Sozlamalar ──────────────────────────────────────────────
ACCESS_TOKEN_TTL  = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)

_redis_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
            decode_responses=True,
        )
    return _redis_client


# ── Device fingerprint ───────────────────────────────────────

def make_fingerprint(request) -> str:
    """User-Agent + IP asosida qurilma identifikatori."""
    ua  = request.META.get('HTTP_USER_AGENT', '')
    ip  = _get_client_ip(request)
    raw = f'{ua}:{ip}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _get_client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


# ── Token yaratish ───────────────────────────────────────────

def create_token_pair(user: CustomUser, fingerprint: str, org=None) -> tuple[str, str]:
    """
    Access + Refresh token juftligi yaratadi.
    Eski qurilma sessionini o'chiradi (bir akkaunt = bir qurilma).

    Args:
        user: Foydalanuvchi
        fingerprint: Qurilma identifikatori
        org: Tashkilot (ixtiyoriy). Mavjud bo'lsa JWT access token'ga qo'shiladi.

    Returns:
        (access_token, refresh_token)
    """
    _revoke_existing_session(user.pk)

    access  = _create_access_token(user, org=org)
    refresh = _create_refresh_token(user, fingerprint)
    return access, refresh


def _create_access_token(user: CustomUser, org=None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        'sub'  : str(user.pk),
        'type' : 'access',
        'iat'  : now,
        'exp'  : now + ACCESS_TOKEN_TTL,
        'jti'  : str(uuid.uuid4()),
    }
    if org is not None:
        payload['current_org'] = str(org.pk)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


def _create_refresh_token(user: CustomUser, fingerprint: str) -> str:
    token = secrets.token_urlsafe(48)
    r = _redis()
    r_key   = f'session:refresh:{user.pk}:{fingerprint}'
    act_key = f'session:active:{user.pk}'
    ttl_sec = int(REFRESH_TOKEN_TTL.total_seconds())
    pipe = r.pipeline()
    pipe.setex(r_key,   ttl_sec, token)
    pipe.setex(act_key, ttl_sec, fingerprint)
    pipe.execute()
    return token


# ── Token tekshirish ─────────────────────────────────────────

def verify_access_token(token: str) -> str | None:
    """
    Access token ni tekshiradi.

    Returns:
        user_id (str UUID) yoki None (noto'g'ri/muddati o'tgan)
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        if payload.get('type') != 'access':
            return None
        return payload['sub']
    except jwt.PyJWTError:
        return None


def rotate_refresh_token(old_token: str, user_id: str, fingerprint: str) -> tuple[str, str] | None:
    """
    Refresh token ni yangilaydi (rotation).

    Returns:
        (new_access, new_refresh) yoki None (noto'g'ri token)
    """
    r = _redis()
    r_key = f'session:refresh:{user_id}:{fingerprint}'
    stored = r.get(r_key)
    if not stored or stored != old_token:
        return None

    try:
        user = CustomUser.objects.get(pk=user_id)
    except CustomUser.DoesNotExist:
        return None

    r.delete(r_key)
    org = user.primary_organization
    return create_token_pair(user, fingerprint, org=org)


def revoke_token(user_id: str, fingerprint: str) -> None:
    """Logout — refresh token ni o'chiradi."""
    r = _redis()
    r.delete(f'session:refresh:{user_id}:{fingerprint}')
    r.delete(f'session:active:{user_id}')


# ── Cookie yozuvchi yordamchilar ─────────────────────────────

def set_auth_cookies(response, access: str, refresh: str, is_secure: bool = False) -> None:
    """Ikki tokenni httpOnly cookie ga yozadi."""
    response.set_cookie(
        'access_token', access,
        max_age=int(ACCESS_TOKEN_TTL.total_seconds()),
        httponly=True,
        secure=is_secure,
        samesite='Lax',
        path='/',
    )
    response.set_cookie(
        'refresh_token', refresh,
        max_age=int(REFRESH_TOKEN_TTL.total_seconds()),
        httponly=True,
        secure=is_secure,
        samesite='Lax',
        path='/api/auth/',  # faqat refresh endpoint ga yuboriladi
    )


def clear_auth_cookies(response) -> None:
    response.delete_cookie('access_token')
    response.delete_cookie('refresh_token')


# ── Ichki ────────────────────────────────────────────────────

def _revoke_existing_session(user_id: str) -> None:
    """
    Oldingi qurilmadagi sessionni bekor qiladi.
    Faqat single_device_policy=True bo'lsa revoking qiladi.
    """
    try:
        user = CustomUser.objects.get(pk=user_id)
        org = user.primary_organization

        # Multi-device policy — revoke skip
        if org and not org.single_device_policy:
            return
    except CustomUser.DoesNotExist:
        pass

    # Single-device policy — oldingi session revoke
    r = _redis()
    old_fp = r.get(f'session:active:{user_id}')
    if old_fp:
        r.delete(f'session:refresh:{user_id}:{old_fp}')
        r.delete(f'session:active:{user_id}')
