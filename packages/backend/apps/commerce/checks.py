"""System checks for payment configuration (audit D07)."""

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from .payment_providers import payments_available


@register(Tags.security)
def check_payment_settings(app_configs, **kwargs):
    messages = []
    if getattr(settings, 'ENABLE_REAL_PAYMENTS', False):
        missing = [
            name
            for name in ('PAYME_SECRET_KEY', 'CLICK_SECRET_KEY')
            if not getattr(settings, name, '')
        ]
        if len(missing) == 2:
            messages.append(
                Error(
                    'ENABLE_REAL_PAYMENTS is on but no provider secret is set; '
                    'every payment webhook will be rejected.',
                    hint='Set PAYME_SECRET_KEY (or PAYME_MERCHANT_KEY) and CLICK_SECRET_KEY.',
                    id='commerce.E001',
                )
            )
        elif missing:
            messages.append(
                Warning(
                    f'ENABLE_REAL_PAYMENTS is on but {missing[0]} is empty; '
                    "that provider's webhooks will be rejected.",
                    id='commerce.W002',
                )
            )
    elif not payments_available():
        messages.append(
            Warning(
                'Real payments are off and the stub provider is disabled outside DEBUG; '
                'top-up and payment webhooks return 503.',
                hint='Set ENABLE_REAL_PAYMENTS=true with provider secrets to accept payments.',
                id='commerce.W001',
            )
        )
    return messages
