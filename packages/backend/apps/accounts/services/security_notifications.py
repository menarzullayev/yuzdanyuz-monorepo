"""Security notices sent to the account owner through the Telegram bot.

``notify`` is the hook the auth flows call; it queues a Celery task so the
request never waits on api.telegram.org. Accounts without a linked Telegram
get nothing (there is no other channel by design).
"""

import logging

from django.conf import settings

log = logging.getLogger(__name__)

TEXTS = {
    'account_locked': (
        "<b>{brand}</b>: hisobingizga ko'p marta noto'g'ri parol kiritildi. "
        "Xavfsizlik uchun kirish {minutes} daqiqaga to'xtatildi. Bu siz bo'lmasangiz, "
        'parolingizni almashtiring.'
    ),
    'password_changed': (
        "<b>{brand}</b>: hisobingiz paroli o'zgartirildi. Bu siz bo'lmasangiz, "
        'darhol parolni tiklang va qurilmalarni tekshiring.'
    ),
    'recovery_link': (
        '<b>{brand}</b>: parolni tiklash havolasi (15 daqiqa amal qiladi, bir marta ishlaydi):\n'
        '{link}\n'
        "Siz so'ramagan bo'lsangiz, e'tibor bermang."
    ),
    'recovery_link_admin': (
        "<b>{brand}</b>: tashkilot administratori hisobingiz uchun parolni tiklashni so'radi. "
        'Havola 15 daqiqa amal qiladi, bir marta ishlaydi:\n{link}\n'
        "Buni kutmagan bo'lsangiz, e'tibor bermang."
    ),
    'two_factor_disabled': (
        "<b>{brand}</b>: hisobingizda ikki bosqichli himoya o'chirildi. "
        "Bu siz bo'lmasangiz, parolni almashtiring."
    ),
}


def render(event: str, **ctx) -> str:
    return TEXTS[event].format(brand=settings.PROJECT_BRAND_NAME, **ctx)


def notify(user, event: str, **ctx) -> None:
    """Queue a Telegram notice for ``user`` (no-op without a linked Telegram)."""
    if not getattr(user, 'telegram_id', None) or event not in TEXTS:
        return
    from apps.accounts.tasks import send_security_notice

    send_security_notice.delay(user.telegram_id, event, ctx)


def _first_in_window(key: str, seconds: int) -> bool:
    """True once per ``seconds`` for ``key`` (fails open without Redis)."""
    try:
        from core.utils import rate_limiter as rl

        return bool(rl._redis().set(key, '1', nx=True, ex=seconds))
    except Exception:
        return True


def notify_account_locked(user, retry_after: int) -> None:
    # A stranger can trigger locks at will; never turn that into a message flood.
    if not _first_in_window(f'notice:locked:{user.pk}', 3600):
        return
    notify(user, 'account_locked', minutes=max(1, round(retry_after / 60)))
