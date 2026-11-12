"""Field-level secret encryption (Fernet / MultiFernet) for values such as third-party API keys.

Keys come from FIELD_ENCRYPTION_KEYS (comma-separated urlsafe-base64 Fernet keys). The first
key encrypts; every key decrypts, so rotation is: put the new key first, run
`manage.py rotate_field_encryption`, then drop the old key. FIELD_ENCRYPTION_KEY (single key)
is still read when the list is empty. A key derived from SECRET_KEY is always tried last for
decryption only, so rows written before an explicit key was configured stay readable until
they are rotated; with no explicit key it is also the encryption key (system check core.W010).
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings

PREFIX = 'enc1:'


class DecryptionError(Exception):
    """A stored secret cannot be decrypted with any configured key."""


def configured_keys() -> list[str]:
    keys = getattr(settings, 'FIELD_ENCRYPTION_KEYS', None) or []
    if isinstance(keys, str):
        keys = keys.split(',')
    keys = [k.strip() for k in keys if k and k.strip()]
    if not keys:
        single = (getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or '').strip()
        if single:
            keys = [single]
    return keys


def _derived_key() -> bytes:
    digest = hashlib.sha256(b'yz-field-encryption:' + settings.SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> MultiFernet:
    fernets = [Fernet(k.encode()) for k in configured_keys()]
    fernets.append(Fernet(_derived_key()))
    return MultiFernet(fernets)


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(PREFIX)


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
        raise DecryptionError(
            'stored secret cannot be decrypted with the configured keys'
        ) from None


def rotate(value: str) -> str:
    """Re-encrypt with the primary key; plaintext is encrypted, empty stays empty."""
    if not value:
        return value
    if not is_encrypted(value):
        return encrypt(value)
    try:
        return PREFIX + _fernet().rotate(value[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        raise DecryptionError(
            'stored secret cannot be decrypted with the configured keys'
        ) from None
