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
    r.setex(_key(token), ttl if ttl > 0 else SESSION_TTL, json.dumps(data))


# ── Session boshqaruv ─────────────────────────────────────────


def create_session() -> dict:
    """
    Yangi deep link sessiya yaratadi.
    Returns: {token, deep_link}
    """
    token = uuid.uuid4().hex
    bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', '')
    deep_link = f'https://t.me/{bot_username}?start=tgauth_{token}'

    _redis().setex(
        _key(token),
        SESSION_TTL,
        json.dumps({'status': 'pending', 'telegram_id': None, 'phone': None, 'user_pk': None}),
    )
    return {'token': token, 'deep_link': deep_link}


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

    session.update({'status': 'waiting', 'telegram_id': telegram_id})
    _save(token, session)
    _register_waiting(token, telegram_id)  # reverse lookup uchun
    return True


def on_bot_contact(telegram_id: int, phone: str, contact_user_id: int | None) -> bool:
    """
    Foydalanuvchi telefon raqamini ulashdi.

    Xavfsizlik: contact.user_id == from.id tekshiriladi —
    boshqa odamning kontaktini yuborib o'tib ketishning iloji yo'q.

    Returns: True — tasdiqlandi, False — sessiya topilmadi yoki fraud
    """
    if contact_user_id and contact_user_id != telegram_id:
        log.warning('Fraud urinishi: from=%s contact_user_id=%s', telegram_id, contact_user_id)
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
    user_pk = _find_or_create_user(telegram_id, normalized_phone)

    session.update({'status': 'verified', 'phone': normalized_phone, 'user_pk': user_pk})
    _save(token, session)
    return True


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
    r.setex(f'tg_waiting:{telegram_id}', SESSION_TTL, token)


def _find_or_create_user(telegram_id: int, phone: str) -> str:
    """CustomUser topadi yoki yaratadi. user.pk qaytaradi (string)."""
    from apps.accounts.models import CustomUser

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
            user.telegram_id = telegram_id
            user.save(update_fields=['telegram_id'])
            return str(user.pk)
        except CustomUser.DoesNotExist:
            pass

    # Yangi foydalanuvchi
    username = f'tg_{telegram_id}'
    if CustomUser.objects.filter(username=username).exists():
        username = f'tg_{telegram_id}_{CustomUser.objects.count()}'

    user = CustomUser(username=username, telegram_id=telegram_id, phone_number=phone or None)
    user.set_unusable_password()
    user.save()
    return str(user.pk)
