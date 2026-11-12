"""Thin wrapper over Django's validators returning API-friendly error pairs."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


def password_errors(password: str, user=None) -> list[tuple[str, str]]:
    """``[(code, message), ...]``; empty list means the password is acceptable."""
    try:
        validate_password(password, user=user)
    except ValidationError as e:
        return [(err.code or 'password_invalid', ' '.join(err.messages)) for err in e.error_list]
    return []
