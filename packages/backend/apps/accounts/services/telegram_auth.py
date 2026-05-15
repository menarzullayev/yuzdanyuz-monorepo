"""
Telegram Mini App (TMA) initData verifikatsiyasi va foydalanuvchi yaratish.

Rasmiy Telegram hujjati:
  https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Algoritm:
  1. initData URL-decode qilinadi
  2. `hash` parametri ajratiladi
  3. Qolgan parametrlar alfavit tartibida saralanadi va "\n" bilan birlashtiriladi
  4. HMAC-SHA256(secret_key=HMAC-SHA256("WebAppData", bot_token), data=data_check_string)
  5. Hisoblangan hash == kelgan hash → valid
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl, unquote

from django.conf import settings

from apps.accounts.models import CustomUser


class TelegramAuthError(Exception):
    pass


def verify_init_data(init_data: str, bot_token: str | None = None, max_age: int = 86400) -> dict:
    """
    TMA initData ni tekshiradi va user dict qaytaradi.

    Args:
        init_data: Telegram WebApp.initData string (URL-encoded)
        bot_token: Telegram bot token. None bo'lsa settings.TELEGRAM_BOT_TOKEN ishlatiladi
        max_age:   Tokenning maksimal yoshi (soniyada). Default 24 soat.

    Returns:
        {'id': int, 'first_name': str, 'last_name': str, 'username': str,
         'language_code': str, 'photo_url': str}

    Raises:
        TelegramAuthError: Verifikatsiya muvaffaqiyatsiz yoki token eskirgan
    """
    token = bot_token or getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not token:
        raise TelegramAuthError('TELEGRAM_BOT_TOKEN sozlanmagan')

    params = dict(parse_qsl(unquote(init_data), keep_blank_values=True))
    received_hash = params.pop('hash', None)
    if not received_hash:
        raise TelegramAuthError('initData da hash yo\'q')

    # Vaqt tekshiruvi
    auth_date = params.get('auth_date')
    if auth_date and time.time() - int(auth_date) > max_age:
        raise TelegramAuthError('initData muddati o\'tgan (max_age exceeded)')

    # data-check-string
    data_check_string = '\n'.join(
        f'{k}={v}' for k, v in sorted(params.items())
    )

    # secret_key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise TelegramAuthError('initData imzosi noto\'g\'ri')

    user_json = params.get('user')
    if not user_json:
        raise TelegramAuthError('initData da user yo\'q')

    return json.loads(user_json)


def find_or_create_user(tg_user: dict) -> tuple[CustomUser, bool]:
    """
    Telegram user dict dan CustomUser topadi yoki yaratadi.

    Returns:
        (user, created) — created=True yangi user yaratilgan bo'lsa
    """
    tg_id = tg_user['id']

    try:
        user = CustomUser.objects.get(telegram_id=tg_id)
        # Ma'lumotlarni yangilash (ism/username o'zgangan bo'lishi mumkin)
        _update_tg_fields(user, tg_user)
        return user, False
    except CustomUser.DoesNotExist:
        pass

    # Yangi foydalanuvchi yaratish
    user = CustomUser(
        username=_unique_username(tg_user),
        telegram_id=tg_id,
    )
    _update_tg_fields(user, tg_user)
    user.set_unusable_password()
    user.save()
    return user, True


# ── private helpers ──────────────────────────────────────────

def _update_tg_fields(user: CustomUser, tg_user: dict) -> None:
    """Telegram ma'lumotlarini user modeliga yozadi."""
    import django.utils.timezone as tz

    user.telegram_username = tg_user.get('username', '')
    user.telegram_language = tg_user.get('language_code', '')
    user.telegram_photo_url = tg_user.get('photo_url', '')
    user.tg_linked_at = tz.now()

    if not user.first_name:
        user.first_name = tg_user.get('first_name', '')
    if not user.last_name:
        user.last_name = tg_user.get('last_name', '')

    user.save(update_fields=[
        'telegram_username', 'telegram_language', 'telegram_photo_url',
        'tg_linked_at', 'first_name', 'last_name',
    ] if user.pk else None)


def _unique_username(tg_user: dict) -> str:
    """Noyob username generatsiya qiladi."""
    base = tg_user.get('username') or f"tg_{tg_user['id']}"
    if not CustomUser.objects.filter(username=base).exists():
        return base
    return f"{base}_{tg_user['id']}"
