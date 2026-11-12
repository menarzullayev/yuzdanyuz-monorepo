"""Password policy validators (single source of truth: settings.AUTH_PASSWORD_VALIDATORS).

Used by register, change/set, reset and the Django admin, so every path
enforces the same rules. Django's own validators cover minimum length,
common-password list, all-numeric and similarity to user attributes.
"""

import re

from django.core.exceptions import ValidationError

# Local words people pick for a password on this platform.
LOCAL_WORDS_LONG = ('milliy', 'sertifikat', 'mashqgoh', 'yuzdanyuz', 'qwerty', 'parol')
LOCAL_WORDS_EXACT = ('mock', 'dtm', 'uzbekistan', 'toshkent', 'password')
_SEQUENCE = '1234567890'


class MaximumLengthValidator:
    def __init__(self, max_length=128):
        self.max_length = max_length

    def validate(self, password, user=None):
        if len(password) > self.max_length:
            raise ValidationError(
                f'Password must be at most {self.max_length} characters.',
                code='password_too_long',
            )

    def get_help_text(self):
        return f'At most {self.max_length} characters.'


class Utf8ByteLengthValidator:
    """Reject passwords over 72 UTF-8 bytes (many hashers silently truncate there)."""

    def __init__(self, max_bytes=72):
        self.max_bytes = max_bytes

    def validate(self, password, user=None):
        if len(password.encode('utf-8')) > self.max_bytes:
            raise ValidationError(
                f'Password must be at most {self.max_bytes} bytes in UTF-8.',
                code='password_too_many_bytes',
            )

    def get_help_text(self):
        return f'At most {self.max_bytes} bytes in UTF-8.'


class RepeatedCharacterValidator:
    def validate(self, password, user=None):
        if password and len(set(password)) == 1:
            raise ValidationError(
                'Password must not be a single repeated character.',
                code='password_repeated_char',
            )

    def get_help_text(self):
        return 'Not a single repeated character.'


class LocalWordsValidator:
    """Reject platform words and 123456 variants, with or without digits/symbols around them."""

    def validate(self, password, user=None):
        low = password.lower()
        alnum = re.sub(r'[^a-z0-9]', '', low)
        letters_only = re.sub(r'[^a-z]', '', low)
        bad = (
            any(w in low for w in LOCAL_WORDS_LONG)
            or letters_only in LOCAL_WORDS_EXACT
            or low in LOCAL_WORDS_EXACT
            or (alnum != '' and alnum.startswith('123456'))
            or (alnum != '' and alnum in (_SEQUENCE, _SEQUENCE[::-1]))
            or (len(alnum) >= 5 and alnum in (_SEQUENCE * 2, _SEQUENCE[::-1] * 2))
        )
        if bad:
            raise ValidationError(
                'Password is too predictable (platform name or a number sequence).',
                code='password_local_word',
            )

    def get_help_text(self):
        return 'Must not contain the platform name or a 123456-style sequence.'


class NotEqualToUsernameValidator:
    def validate(self, password, user=None):
        username = getattr(user, 'username', None)
        if username and password.lower() == username.lower():
            raise ValidationError(
                'Password must not equal the username.', code='password_equals_username'
            )

    def get_help_text(self):
        return 'Must not equal the username.'
