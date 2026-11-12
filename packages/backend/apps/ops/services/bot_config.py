"""Bot settings resolver: DB value first, environment (Django settings) as the fallback.

Reads go through a short cache so per-message senders do not hit the database; saving the
settings row clears it. A database failure never breaks a send: it falls back to the env.
"""

import logging
import secrets

from django.conf import settings
from django.core.cache import cache

from core import crypto

log = logging.getLogger(__name__)

CACHE_KEY = 'ops:bot-settings:v1'
CACHE_TTL = 60

_EMPTY = {'token': '', 'secret': '', 'webhook_url': '', 'admin_chat_ids': [], 'channel_id': ''}


def _dec(value: str) -> str:
    try:
        return crypto.decrypt(value) if value else ''
    except crypto.DecryptionError:
        log.error('Stored bot secret cannot be decrypted; falling back to the environment')
        return ''


def _load() -> dict:
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    from apps.ops.models import BotSettings

    try:
        row = BotSettings.objects.filter(pk=1).first()
        data = (
            {
                'token': _dec(row.token_enc),
                'secret': _dec(row.webhook_secret_enc),
                'webhook_url': row.webhook_url,
                'admin_chat_ids': list(row.admin_chat_ids or []),
                'channel_id': row.channel_id,
            }
            if row
            else dict(_EMPTY)
        )
    except Exception as e:  # DB unavailable...
        log.error('Bot settings unavailable, using environment: %s', e)
        return dict(_EMPTY)
    cache.set(CACHE_KEY, data, CACHE_TTL)
    return data


def invalidate() -> None:
    cache.delete(CACHE_KEY)


def get_token() -> str:
    return _load()['token'] or getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''


def get_webhook_secret() -> str:
    return _load()['secret'] or getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or ''


def get_webhook_url() -> str:
    return _load()['webhook_url']


def get_admin_chat_ids() -> list[int]:
    ids = _load()['admin_chat_ids'] or list(getattr(settings, 'OPS_ADMIN_CHAT_IDS', []) or [])
    return [int(i) for i in ids]


def get_channel_id() -> str:
    return _load()['channel_id'] or getattr(settings, 'OPS_TELEGRAM_CHANNEL_ID', '') or ''


def source_of(field: str) -> str:
    """'db' when the database overrides the environment for ``field``, else 'env'."""
    return 'db' if _load().get(field) else 'env'


def generate_secret() -> str:
    # Telegram allows A-Z a-z 0-9 _ - (1..256 chars) in secret_token.
    return secrets.token_urlsafe(32)


def last4(value: str) -> str:
    return value[-4:] if value else ''
