"""System checks for the accounts app."""

from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security)
def telegram_webhook_secret_check(app_configs, **kwargs):
    """The bot webhook rejects every update without a secret; say so loudly outside DEBUG."""
    if settings.DEBUG or (getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or '').strip():
        return []
    return [
        Error(
            'TELEGRAM_WEBHOOK_SECRET is empty, so the Telegram bot webhook rejects every update '
            'and Telegram sign-in cannot work.',
            hint='Set TELEGRAM_WEBHOOK_SECRET and pass the same value as secret_token to setWebhook.',
            id='accounts.E001',
        )
    ]
