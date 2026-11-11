"""
Optional two-factor authentication: TOTP (RFC 6238) + one-time backup codes.

TOTP is implemented with ``hmac`` (no extra dependency): HMAC-SHA1, 6 digits,
30 s step, accepting the previous/next step (+-1 window) for clock drift.
A step is accepted once (``last_used_step``), so a sniffed code cannot be
replayed. The shared secret is stored encrypted (Fernet, key derived from
``settings.TOTP_ENCRYPTION_KEY`` or SECRET_KEY); backup codes are stored only
as keyed hashes and are single use.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import BackupCode, CustomUser, TOTPDevice

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
BACKUP_CODE_COUNT = 10
_BACKUP_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'  # no look-alikes (i, l, o, 0, 1)


class TwoFactorError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ── TOTP primitives ─────────────────────────────────────────


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _key_bytes(secret: str) -> bytes:
    padded = secret.upper() + '=' * (-len(secret) % 8)
    return base64.b32decode(padded)


def totp_code(secret: str, step: int) -> str:
    digest = hmac.new(_key_bytes(secret), struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack('>I', digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // STEP_SECONDS)


def matching_step(secret: str, code: str, now: float | None = None) -> int | None:
    """Step whose code equals ``code`` within the +-1 window, else None (constant time)."""
    if not (len(code) == DIGITS and code.isdigit()):
        return None
    base = current_step(now)
    found = None
    for step in range(base - WINDOW, base + WINDOW + 1):
        if hmac.compare_digest(totp_code(secret, step), code):
            found = step
    return found


def provisioning_uri(secret: str, username: str) -> str:
    issuer = settings.PROJECT_BRAND_NAME
    label = quote(f'{issuer}:{username}')
    return (
        f'otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}'
        f'&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}'
    )


# ── Secret encryption ───────────────────────────────────────


def _fernet() -> Fernet:
    material = getattr(settings, 'TOTP_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(f'totp:{material}'.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


# ── Backup codes ────────────────────────────────────────────


def _normalize_backup(code: str) -> str:
    return ''.join(ch for ch in code.lower() if ch.isalnum())


def hash_backup_code(code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f'backup:{_normalize_backup(code)}'.encode(), hashlib.sha256).hexdigest()


def _new_backup_code() -> str:
    raw = ''.join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
    return f'{raw[:5]}-{raw[5:]}'


def generate_backup_codes(user: CustomUser) -> list[str]:
    """Replace the user's backup codes with ``BACKUP_CODE_COUNT`` new ones (plaintext returned once)."""
    codes = [_new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
    with transaction.atomic():
        BackupCode.objects.filter(user=user).delete()
        BackupCode.objects.bulk_create(
            [BackupCode(user=user, code_hash=hash_backup_code(c)) for c in codes]
        )
    return codes


def backup_codes_remaining(user: CustomUser) -> int:
    return BackupCode.objects.filter(user=user, used_at__isnull=True).count()


def _consume_backup_code(user: CustomUser, code: str) -> bool:
    digest = hash_backup_code(code)
    for row in BackupCode.objects.filter(user=user, used_at__isnull=True):
        if hmac.compare_digest(row.code_hash, digest):
            # Conditional update: two parallel requests cannot both spend the code.
            return (
                BackupCode.objects.filter(pk=row.pk, used_at__isnull=True).update(
                    used_at=timezone.now()
                )
                == 1
            )
    return False


# ── Device lifecycle ────────────────────────────────────────


def get_device(user: CustomUser) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user).first()


def is_enabled(user: CustomUser) -> bool:
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def begin_setup(user: CustomUser) -> dict:
    """Create (or restart) an unconfirmed device; returns the secret and otpauth URI."""
    if is_enabled(user):
        raise TwoFactorError('already_enabled', 'Two-factor authentication is already enabled.')
    secret = generate_secret()
    TOTPDevice.objects.update_or_create(
        user=user,
        defaults={'secret_enc': encrypt_secret(secret), 'confirmed_at': None, 'last_used_step': 0},
    )
    return {'secret': secret, 'otpauth_uri': provisioning_uri(secret, user.username)}


def _verify_totp(device: TOTPDevice, code: str) -> bool:
    step = matching_step(decrypt_secret(device.secret_enc), code)
    if step is None:
        return False
    # Accept each step once: the conditional update fails for a replayed code.
    return (
        TOTPDevice.objects.filter(pk=device.pk, last_used_step__lt=step).update(last_used_step=step)
        == 1
    )


def confirm_setup(user: CustomUser, code: str) -> list[str]:
    """Activate 2FA after the user proves the app works; returns the backup codes."""
    device = get_device(user)
    if device is None:
        raise TwoFactorError('setup_not_started', 'Start the setup first.')
    if device.confirmed_at is not None:
        raise TwoFactorError('already_enabled', 'Two-factor authentication is already enabled.')
    if not _verify_totp(device, code.strip()):
        raise TwoFactorError('invalid_code', 'The code is not valid.')
    TOTPDevice.objects.filter(pk=device.pk).update(confirmed_at=timezone.now())
    return generate_backup_codes(user)


def verify_second_factor(user: CustomUser, code: str) -> bool:
    """A TOTP code (6 digits) or a backup code. False when 2FA is off or the code is wrong."""
    device = TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None or not code:
        return False
    code = code.strip()
    if len(code) == DIGITS and code.isdigit():
        return _verify_totp(device, code)
    return _consume_backup_code(user, code)


def disable(user: CustomUser) -> None:
    with transaction.atomic():
        TOTPDevice.objects.filter(user=user).delete()
        BackupCode.objects.filter(user=user).delete()
