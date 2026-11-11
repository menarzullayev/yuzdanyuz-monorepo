"""System checks for the Claude subscription token (LLM_BACKEND=claude_cli).

Warnings only: an expired AI token must not stop migrations or the web app,
it only breaks AI features. `manage.py check` and every runserver/migrate show them.
"""

import os

from django.conf import settings
from django.core.checks import Warning, register

RENEW_HINT = (
    'Run `claude setup-token`, then update CLAUDE_CODE_OAUTH_TOKEN and set '
    'CLAUDE_CODE_OAUTH_TOKEN_EXPIRES to the new date (YYYY-MM-DD, one year later).'
)


@register()
def ai_token_expiry_check(app_configs, **kwargs):
    if settings.LLM_BACKEND != 'claude_cli':
        return []

    from .tasks import TOKEN_WARN_DAYS, ai_token_days_left

    if not (settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES or '').strip():
        if os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'):
            return [
                Warning(
                    'CLAUDE_CODE_OAUTH_TOKEN is set but CLAUDE_CODE_OAUTH_TOKEN_EXPIRES is not, '
                    'so nobody is warned before the token expires.',
                    hint=RENEW_HINT,
                    id='intelligence.W002',
                )
            ]
        return []
    try:
        days = ai_token_days_left()
    except ValueError:
        return [
            Warning(
                f'CLAUDE_CODE_OAUTH_TOKEN_EXPIRES={settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES!r} '
                'is not a YYYY-MM-DD date.',
                hint=RENEW_HINT,
                id='intelligence.W003',
            )
        ]
    if days < 0:
        return [
            Warning(
                f'The Claude subscription token expired {-days} day(s) ago; AI features fail.',
                hint=RENEW_HINT,
                id='intelligence.W001',
            )
        ]
    if days <= TOKEN_WARN_DAYS:
        return [
            Warning(
                f'The Claude subscription token expires in {days} day(s).',
                hint=RENEW_HINT,
                id='intelligence.W004',
            )
        ]
    return []
