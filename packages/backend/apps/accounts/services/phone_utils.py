"""
Telefon raqami normalizatsiya va validatsiya.

Default mamlakat: O'zbekiston (+998).
Natija har doim E.164 formatida: +998XXXXXXXXX
"""

import re

UZ_PREFIX = '998'
UZ_OPERATORS = {
    '90',
    '91',
    '93',
    '94',
    '95',
    '97',
    '98',
    '99',
    '33',
    '71',
    '77',
    '88',
    '55',
    '20',
}


class PhoneValidationError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """
    Har qanday formatdagi telefon raqamni E.164 ga keltiradi.

    Qabul qilinadi:
        +998901234567   → +998901234567
        998901234567    → +998901234567
        0901234567      → +998901234567
        901234567       → +998901234567  (9 xonali, UZ operator)

    Raises:
        PhoneValidationError: format noto'g'ri
    """
    digits = re.sub(r'\D', '', raw)

    if not digits:
        raise PhoneValidationError('Telefon raqam kiritilmagan')

    # 13 xonali: 998901234567 (country code without +)
    if len(digits) == 12 and digits.startswith(UZ_PREFIX):
        return '+' + digits

    # 10 xonali: 0901234567
    if len(digits) == 10 and digits.startswith('0'):
        return '+' + UZ_PREFIX + digits[1:]

    # 9 xonali: 901234567
    if len(digits) == 9:
        return '+' + UZ_PREFIX + digits

    # Boshqa mamlakat: + bilan to'liq E.164
    if len(digits) >= 10 and raw.strip().startswith('+'):
        return '+' + digits

    raise PhoneValidationError(
        f'Noto\'g\'ri telefon raqam formati: "{raw}". '
        f'+998XXXXXXXXX yoki 0XXXXXXXXX formatida kiriting.'
    )


def is_uzbek_number(phone_e164: str) -> bool:
    """E.164 formatdagi raqam O'zbekistonnikimi?"""
    return phone_e164.startswith('+998') and len(phone_e164) == 13
