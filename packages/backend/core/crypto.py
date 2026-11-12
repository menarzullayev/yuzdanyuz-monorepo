"""Field-level secret encryption (Fernet) for values such as third-party API keys.

The key comes from FIELD_ENCRYPTION_KEY (a urlsafe-base64 32-byte Fernet key).
When it is unset a key is derived from SECRET_KEY, so rotating SECRET_KEY without
setting FIELD_ENCRYPTION_KEY first makes stored secrets undecryptable.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = 'enc1:'


class DecryptionError(Exception):
    """A stored secret cannot be decrypted with the current key."""


def _fernet() -> Fernet:
    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or ''
    if key:
        return Fernet(key.encode())
    digest = hashlib.sha256(b'yz-field-encryption:' + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)


def encrypt(plaintext: str) -> str:
    """Return the prefixed ciphertext; empty input stays empty."""
    if not plaintext:
        return ''
    return PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt a value produced by encrypt(); legacy plaintext (no prefix) is returned as is."""
    if not value or not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        raise DecryptionError('stored secret cannot be decrypted with the current key') from None
