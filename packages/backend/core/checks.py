"""Deployment system checks for core settings (run by `manage.py check --deploy`)."""

from cryptography.fernet import Fernet
from django.core.checks import Error, Tags, Warning, register

from core import crypto


@register(Tags.security, deploy=True)
def check_field_encryption_key(app_configs, **kwargs):
    keys = crypto.configured_keys()
    if not keys:
        return [
            Warning(
                'FIELD_ENCRYPTION_KEYS is empty: database secrets are encrypted with a key '
                'derived from SECRET_KEY, so rotating SECRET_KEY makes them unreadable.',
                hint='Generate one with `python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"` and set FIELD_ENCRYPTION_KEYS.',
                id='core.W010',
            )
        ]
    for key in keys:
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            return [
                Error(
                    'FIELD_ENCRYPTION_KEYS contains a value that is not a Fernet key '
                    '(32 url-safe base64-encoded bytes).',
                    id='core.E010',
                )
            ]
    return []
