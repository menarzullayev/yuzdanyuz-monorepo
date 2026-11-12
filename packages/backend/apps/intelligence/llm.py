"""One entry point for every LLM and speech-to-text call made by tasks.

Provider selection (AIProviderConfig rows, platform-staff managed):
  1. active, non-STT rows whose profile (`use_for`) equals the task's profile, by priority;
  2. none -> the `default` profile rows (use_for='all');
  3. none -> the settings backend (LLM_BACKEND: claude_cli | anthropic_api).
A failing provider falls through to the next row; the first error is re-raised when all
fail so the retry policy in tasks.py still sees e.g. a Claude rate limit.
"""

import logging
import time

from django.conf import settings

from apps.catalog.models import AIProviderConfig
from apps.catalog.services.ai_providers import ProviderFactory
from apps.catalog.services.ai_providers.base import BaseAIProvider, TransientProviderError
from apps.catalog.services.ai_providers.claude import AnthropicProvider
from apps.catalog.services.ai_providers.claude_cli import ClaudeCLIProvider

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = 'default'
# API-facing profile name -> AIProviderConfig.use_for
PROFILE_USE_FOR = {
    'default': AIProviderConfig.UseFor.ALL,
    'writing': AIProviderConfig.UseFor.WRITING,
    'speaking': AIProviderConfig.UseFor.SPEAKING,
    'parser': AIProviderConfig.UseFor.PARSER,
    'tutor': AIProviderConfig.UseFor.TUTOR,
}
USE_FOR_PROFILE = {v.value: k for k, v in PROFILE_USE_FOR.items()}


def resolve_configs(profile: str, purpose: str = 'llm') -> list[AIProviderConfig]:
    """Active configs for a profile; a profile without rows of its own uses `default`."""
    use_for = PROFILE_USE_FOR[profile].value
    active = AIProviderConfig.objects.filter(is_active=True).order_by('priority', 'id')

    def rows(value: str) -> list[AIProviderConfig]:
        return [c for c in active.filter(use_for=value) if c.purpose == purpose]

    own = rows(use_for)
    if own or profile == DEFAULT_PROFILE:
        return own
    return rows(PROFILE_USE_FOR[DEFAULT_PROFILE].value)


def settings_provider(model: str | None = None) -> BaseAIProvider:
    """The provider selected by settings.LLM_BACKEND (used when no config row applies)."""
    model = model or settings.ANTHROPIC_MODEL
    if settings.LLM_BACKEND == 'claude_cli':
        return ClaudeCLIProvider(model=model)
    api_key = getattr(settings, 'ANTHROPIC_API_KEY', '')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY settings/env'da yo'q")
    return AnthropicProvider(api_key=api_key, model=model)


def generate(
    prompt: str,
    *,
    profile: str = DEFAULT_PROFILE,
    system: str = '',
    json_schema: dict | None = None,
    max_tokens: int = 2048,
) -> str:
    configs = resolve_configs(profile)
    if not configs:
        return settings_provider().complete(
            prompt, system=system, json_schema=json_schema, max_tokens=max_tokens
        )
    first_error: Exception | None = None
    for config in configs:
        try:
            provider = ProviderFactory.create(config)
            return provider.complete(
                prompt, system=system, json_schema=json_schema, max_tokens=max_tokens
            )
        except Exception as exc:
            logger.warning('AI provider %r (%s profile) failed: %s', config.name, profile, exc)
            first_error = first_error or exc
    assert first_error is not None
    raise first_error


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, '***') if secret else text


def test_connection(config: AIProviderConfig) -> dict:
    """Send a tiny request through the config; never raises, never echoes the key."""
    from core import crypto

    started = time.monotonic()
    secret = ''
    try:
        secret = config.get_api_key()
        if config.purpose == 'stt':
            from .stt import check_stt

            check_stt(config)
        else:
            reply = ProviderFactory.create(config).complete(
                'Reply with the single word: ok', max_tokens=64
            )
            if not reply.strip():
                raise RuntimeError('Empty reply')
        ok, error = True, ''
    except crypto.DecryptionError as exc:
        ok, error = False, str(exc)
    except TransientProviderError as exc:
        ok, error = False, f'Temporary failure: {_redact(str(exc), secret)}'
    except Exception as exc:
        ok, error = False, _redact(str(exc), secret)[:300]
    return {
        'ok': ok,
        'latency_ms': int((time.monotonic() - started) * 1000),
        'error': error,
    }


# pytest would otherwise collect this helper as a test
test_connection.__test__ = False
