"""
Telegram Deep Link Sign In — telefon raqam ulash orqali.

Flow:
  1. POST /api/auth/tg/init/       → {token, deep_link}
  2. Foydalanuvchi deep link ochadi, START bosadi
  3. Bot "📱 Telefon raqamni ulash" klaviaturasi yuboradi
  4. Foydalanuvchi tap qiladi → Telegram contact yuboradi
  5. Bot webhook: contact.user_id == from.id → tasdiqlash
  6. GET  /api/auth/tg/status/?token=TOKEN → {status: 'verified'}
  7. POST /api/auth/tg/complete/   {token}  → JWT cookie

Redis sessiya holatlari:
  'pending'   — deep link yuborildi, hali START bosilmagan
  'waiting'   — START bosildi, contact kutilmoqda
  'verified'  — telefon ulashildi, login tayyor
  (TTL = 300s)
"""

import json
import logging
import uuid

from django.conf import settings
from django.db import transaction

log = logging.getLogger(__name__)

SESSION_TTL = 300  # 5 daqiqa


def _redis():
    import redis as _r

    return _r.Redis.from_url(
        getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
        decode_responses=True,
    )


def _key(token: str) -> str:
    return f'tg_deeplink:{token}'


def _save(token: str, data: dict) -> None:
    r = _redis()
    ttl = r.ttl(_key(token))
    r.set(_key(token), json.dumps(data), ex=ttl if ttl > 0 else SESSION_TTL)


# ── Session boshqaruv ─────────────────────────────────────────


def create_session(
    link_user_pk: str | None = None,
    acquisition: dict | None = None,
    nonce_hash: str | None = None,
    requester: dict | None = None,
) -> dict:
    """
    Yangi deep link sessiya yaratadi.

    ``link_user_pk`` makes it a *link* session: instead of signing in, the Telegram
    account that opens the link is attached to that (already signed-in) user after
    the Telegram owner confirms in the bot.

    Returns: {token, deep_link}
    """
    token = uuid.uuid4().hex
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '')
    deep_link = f'https://t.me/{bot_username}?start=tgauth_{token}'

    _redis().set(
        _key(token),
        json.dumps(
            {
                'status': 'pending',
                'telegram_id': None,
                'phone': None,
                'user_pk': None,
                'mode': 'link' if link_user_pk else 'login',
                'link_user_pk': link_user_pk,
                'acquisition': acquisition if isinstance(acquisition, dict) else None,
                # Login sessions are bound to the browser that started them (nonce cookie).
                'nonce_hash': nonce_hash,
                'requester': requester,
            }
        ),
        ex=SESSION_TTL,
    )
    return {'token': token, 'deep_link': deep_link, 'expires_in': SESSION_TTL}


def get_session(token: str) -> dict | None:
    raw = _redis().get(_key(token))
    return json.loads(raw) if raw else None


def expire_session(token: str) -> None:
    _redis().delete(_key(token))


# ── Bot tomonidan chaqiriladi ─────────────────────────────────


def on_bot_start(token: str, telegram_id: int) -> bool:
    """
    /start tgauth_TOKEN qabul qilindi.
    Sessiyani 'waiting' ga o'tkazadi (contact kutmoqda).
    Returns: True — muvaffaqiyatli, False — sessiya topilmadi/eskirgan
    """
    session = get_session(token)
    if session is None or session['status'] != 'pending':
        return False

    if session.get('mode') == 'link':
        # Link sessions need no phone: the Telegram owner confirms in the bot instead.
        session.update({'status': 'confirming', 'telegram_id': telegram_id})
        _save(token, session)
        return True

    session.update({'status': 'waiting', 'telegram_id': telegram_id})
    _save(token, session)
    _register_waiting(token, telegram_id)  # reverse lookup uchun
    return True


def link_target(token: str):
    """The user a link session would attach Telegram to (None for login sessions)."""
    from apps.accounts.models import CustomUser

    session = get_session(token)
    if session is None or session.get('mode') != 'link' or not session.get('link_user_pk'):
        return None
    return CustomUser.objects.filter(pk=session['link_user_pk'], is_active=True).first()


def on_link_confirm(token: str, from_id: int, profile: dict | None = None) -> str:
    """The Telegram owner pressed "confirm" in the bot.

    Returns 'linked', 'conflict' (Telegram already belongs to another account, or this
    account already has a Telegram) or 'invalid' (expired / not this chat's session).
    """
    session = get_session(token)
    if (
        session is None
        or session.get('mode') != 'link'
        or session.get('status') != 'confirming'
        or session.get('telegram_id') != from_id
    ):
        return 'invalid'
    user = link_target(token)
    if user is None:
        return 'invalid'
    from apps.accounts.services.linking import TelegramLinkError, link_telegram

    try:
        link_telegram(user, from_id, profile or {})
    except TelegramLinkError:
        session['status'] = 'conflict'
        _save(token, session)
        return 'conflict'
    session['status'] = 'linked'
    _save(token, session)
    return 'linked'


def on_link_cancel(token: str, from_id: int) -> None:
    session = get_session(token)
    if session and session.get('telegram_id') == from_id:
        expire_session(token)


def on_bot_contact(
    telegram_id: int,
    phone: str,
    contact_user_id: int | None,
    first_name: str = '',
    last_name: str = '',
) -> bool:
    """
    Foydalanuvchi telefon raqamini ulashdi.

    Xavfsizlik: contact.user_id == from.id tekshiriladi —
    boshqa odamning kontaktini yuborib o'tib ketishning iloji yo'q.

    Returns: True — tasdiqlandi, False — sessiya topilmadi yoki fraud
    """
    # A contact card typed in by hand has no user_id, so only a contact whose
    # user_id is the sender proves the sender owns this phone number.
    if contact_user_id is None or contact_user_id != telegram_id:
        log.warning('Contact rejected: from=%s contact_user_id=%s', telegram_id, contact_user_id)
        return False

    # Ushbu telegram_id uchun 'waiting' sessiyani topamiz
    token = _find_token_by_telegram_id(telegram_id)
    if token is None:
        log.warning('Sessiya topilmadi: telegram_id=%s', telegram_id)
        return False

    session = get_session(token)
    if session is None or session['status'] != 'waiting':
        return False

    # E.164 formatga keltirish
    from apps.accounts.services.phone_utils import normalize_phone

    try:
        normalized_phone = normalize_phone(phone)
    except Exception:
        normalized_phone = f'+{phone.lstrip("+")}'

    # User topish yoki yaratish
    user_pk = _find_or_create_user(telegram_id, normalized_phone, first_name, last_name)
    if user_pk is None:
        return False

    if session.get('acquisition'):
        from apps.accounts.models import CustomUser
        from apps.accounts.services.onboarding import capture_acquisition

        capture_acquisition(CustomUser.objects.get(pk=user_pk), session['acquisition'])

    session.update({'status': 'verified', 'phone': normalized_phone, 'user_pk': user_pk})
    _save(token, session)
    return True


def hash_nonce(nonce: str) -> str:
    import hashlib

    return hashlib.sha256((nonce or '').encode()).hexdigest()


def nonce_matches(token: str, nonce: str) -> bool:
    """True when ``nonce`` is the secret handed to the browser that created this session."""
    import hmac

    session = get_session(token)
    expected = (session or {}).get('nonce_hash')
    if not expected or not nonce:
        return False
    return hmac.compare_digest(expected, hash_nonce(nonce))


def requester_text(token: str) -> str:
    """Who asked for this sign-in (shown in the bot message so the owner can refuse)."""
    info = (get_session(token) or {}).get('requester') or {}
    if not info:
        return ''
    return f'{info.get("label", "?")}, IP {info.get("ip", "?")}'


def get_verified_user_pk(token: str) -> int | None:
    """
    Verified sessiyadan user_pk ni qaytaradi.
    Returns: user_pk yoki None
    """
    session = get_session(token)
    if session is None or session['status'] != 'verified':
        return None
    return session.get('user_pk')


# ── Private helpers ───────────────────────────────────────────


def _find_token_by_telegram_id(telegram_id: int) -> str | None:
    """Redis da ushbu telegram_id uchun 'waiting' sessiyani qidiradi."""
    r = _redis()
    # telegram_id → token xaritasi ham saqlaymiz
    mapping_key = f'tg_waiting:{telegram_id}'
    return r.get(mapping_key)


def _register_waiting(token: str, telegram_id: int) -> None:
    """telegram_id → token xaritasini Redis ga yozadi."""
    r = _redis()
    r.set(f'tg_waiting:{telegram_id}', token, ex=SESSION_TTL)


def _find_or_create_user(
    telegram_id: int, phone: str, first_name: str = '', last_name: str = ''
) -> str | None:
    """CustomUser topadi yoki yaratadi. user.pk qaytaradi (string).

    None — the phone belongs to an account already bound to a different
    Telegram id (e.g. a recycled number); that account is never re-bound here.
    """
    from apps.accounts.models import CustomUser
    from apps.organizations.services import ensure_platform_membership

    # Avval telegram_id bo'yicha
    try:
        user = CustomUser.objects.get(telegram_id=telegram_id)
        if phone and not user.phone_number:
            user.phone_number = phone
            user.save(update_fields=['phone_number'])
        return str(user.pk)
    except CustomUser.DoesNotExist:
        pass

    # Keyin telefon bo'yicha
    if phone:
        try:
            user = CustomUser.objects.get(phone_number=phone)
            if user.telegram_id is not None:
                log.warning('Phone already bound to another Telegram account: from=%s', telegram_id)
                return None
            user.telegram_id = telegram_id
            user.save(update_fields=['telegram_id'])
            return str(user.pk)
        except CustomUser.DoesNotExist:
            pass

    # Yangi foydalanuvchi: username is generated from the Telegram name (one free change).
    from django.utils import timezone

    from apps.accounts.services.usernames import generate_username

    user = CustomUser(
        username=generate_username(first_name, last_name),
        username_is_auto=True,
        first_name=first_name[:150],
        last_name=last_name[:150],
        telegram_id=telegram_id,
        phone_number=phone or None,
        terms_version=settings.TERMS_VERSION,
        terms_accepted_at=timezone.now(),
    )
    user.set_unusable_password()
    with transaction.atomic():
        user.save()
        ensure_platform_membership(user)
    return str(user.pk)
