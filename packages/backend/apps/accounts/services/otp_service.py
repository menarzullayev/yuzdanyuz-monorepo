"""
OTP yuborish va tekshirish xizmati.

Rate limiting (Redis):
  - send_otp: 1 raqamga 10 daqiqada max 3 marta SMS
  - verify_otp: noto'g'ri kod 3 marta → OTP bekor, qayta yuborish kerak

OTP saqlash (PostgreSQL):
  - OTPCode modeli — audit trail uchun
  - Foydalanilgan (is_verified=True) yoki muddati o'tgan kodlar boshqa sessiyada ishlatilmaydi
"""

import logging

from django.conf import settings

from apps.accounts.models import CustomUser, OTPCode
from apps.accounts.services.phone_utils import normalize_phone, PhoneValidationError
from apps.accounts.services.sms_backend import get_sms_backend

log = logging.getLogger(__name__)

# Rate limit sozlamalari
SEND_RATE_WINDOW  = 600   # 10 daqiqa (soniyada)
SEND_RATE_LIMIT   = 3     # 10 daqiqada max 3 ta SMS


class OTPError(Exception):
    pass


class OTPRateLimitError(OTPError):
    pass


# ── Redis yordamchi ───────────────────────────────────────────

def _redis():
    import redis
    return redis.Redis.from_url(
        getattr(settings, 'REDIS_URL', 'redis://127.0.0.1:6379/1'),
        decode_responses=True,
    )


def _check_send_rate(phone: str) -> None:
    """10 daqiqada 3 dan ko'p yuborishni bloklaydi."""
    r   = _redis()
    key = f'otp:send_count:{phone}'
    count = r.get(key)
    if count and int(count) >= SEND_RATE_LIMIT:
        ttl = r.ttl(key)
        raise OTPRateLimitError(
            f'Juda ko\'p urinish. {ttl} soniyadan keyin qayta urinib ko\'ring.'
        )


def _increment_send_count(phone: str) -> None:
    r   = _redis()
    key = f'otp:send_count:{phone}'
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, SEND_RATE_WINDOW)
    pipe.execute()


# ── Asosiy funksiyalar ────────────────────────────────────────

def send_otp(raw_phone: str) -> str:
    """
    Telefon raqamga OTP yuboradi.

    Args:
        raw_phone: Har qanday formatdagi telefon raqam

    Returns:
        Normallashtrilgan E.164 telefon raqam

    Raises:
        PhoneValidationError: noto'g'ri format
        OTPRateLimitError:    juda ko'p urinish
        OTPError:             SMS yuborishda xato
    """
    phone = normalize_phone(raw_phone)
    _check_send_rate(phone)

    otp = OTPCode.create_for_phone(phone)
    text = _otp_text(otp.code)

    backend = get_sms_backend()
    sent = backend.send(phone, text)
    if not sent:
        otp.delete()
        raise OTPError('SMS yuborishda xato yuz berdi. Keyinroq urinib ko\'ring.')

    _increment_send_count(phone)
    log.info('OTP yuborildi: phone=%s id=%s', phone, otp.pk)
    return phone


def verify_otp(raw_phone: str, code: str) -> CustomUser:
    """
    Kodni tekshiradi va CustomUser qaytaradi (yangi yoki mavjud).

    Raises:
        PhoneValidationError: noto'g'ri format
        OTPError:             kod noto'g'ri, muddati o'tgan, foydalanilgan yoki urinish tugagan
    """
    phone = normalize_phone(raw_phone)

    otp = (
        OTPCode.objects
        .filter(phone=phone, is_verified=False)
        .order_by('-created_at')
        .first()
    )

    if otp is None:
        raise OTPError('Kod topilmadi. Qayta SMS so\'rang.')

    if otp.is_expired:
        raise OTPError('Kod muddati o\'tgan. Qayta SMS so\'rang.')

    if otp.is_exhausted:
        raise OTPError('Juda ko\'p noto\'g\'ri urinish. Qayta SMS so\'rang.')

    if otp.code != code.strip():
        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        remaining = 3 - otp.attempts
        if remaining > 0:
            raise OTPError(f'Noto\'g\'ri kod. {remaining} ta urinish qoldi.')
        else:
            raise OTPError('Noto\'g\'ri kod. Urinishlar tugadi. Qayta SMS so\'rang.')

    # Kod to'g'ri
    otp.is_verified = True
    otp.save(update_fields=['is_verified'])

    user, created = _find_or_create_by_phone(phone)
    log.info('OTP verify OK: phone=%s user=%s new=%s', phone, user.pk, created)
    return user


# ── Private helpers ───────────────────────────────────────────

def _otp_text(code: str) -> str:
    brand = getattr(settings, 'PROJECT_BRAND_NAME', 'Milliy Sertifikat')
    return f'{brand}: tasdiqlash kodingiz {code}. Hech kimga bermang.'


def _find_or_create_by_phone(phone: str) -> tuple[CustomUser, bool]:
    try:
        user = CustomUser.objects.get(phone_number=phone)
        return user, False
    except CustomUser.DoesNotExist:
        pass

    # Yangi foydalanuvchi
    username = f'ph_{phone.lstrip("+")}'
    if CustomUser.objects.filter(username=username).exists():
        username = f'{username}_{CustomUser.objects.count()}'

    user = CustomUser(username=username, phone_number=phone)
    user.set_unusable_password()
    user.save()
    return user, True
