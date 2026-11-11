"""Pluggable speech-to-text for the speaking pipeline.

An STT provider is an AIProviderConfig row with extra_options {"purpose": "stt"} in the
speaking profile (or the default profile). Implemented: OpenAI-compatible
`/audio/transcriptions` (provider='openai'; base_url may point at Groq, a local server, ...).
Optional extra_options: stt_model (default: the row's model), language (ISO code).
"""

from apps.catalog.models import AIProviderConfig
from apps.catalog.services.ai_providers._http import get_json, post_multipart

from .llm import DEFAULT_PROFILE, resolve_configs

OPENAI_BASE_URL = 'https://api.openai.com/v1'


class SttError(RuntimeError):
    pass


def resolve_stt(profile: str = 'speaking') -> AIProviderConfig | None:
    """First usable STT config for the profile, falling back to the default profile."""
    for name in (profile, DEFAULT_PROFILE):
        for config in resolve_configs(name, purpose='stt'):
            if config.provider == AIProviderConfig.Provider.OPENAI:
                return config
    return None


def _base(config: AIProviderConfig) -> str:
    return (config.base_url or OPENAI_BASE_URL).rstrip('/')


def transcribe(config: AIProviderConfig, audio: bytes, filename: str) -> str:
    key = config.get_api_key()
    options = config.extra_options or {}
    data = {'model': options.get('stt_model') or config.model, 'response_format': 'json'}
    if options.get('language'):
        data['language'] = options['language']
    body = post_multipart(
        f'{_base(config)}/audio/transcriptions',
        data=data,
        files={'file': (filename, audio)},
        headers={'Authorization': f'Bearer {key}'},
        secret=key,
    )
    text = (body.get('text') or '').strip()
    if not text:
        raise SttError('Speech-to-text returned an empty transcript')
    return text


def check_stt(config: AIProviderConfig) -> None:
    """Cheap reachability/auth probe for 'test connection' (lists models)."""
    key = config.get_api_key()
    get_json(f'{_base(config)}/models', headers={'Authorization': f'Bearer {key}'}, secret=key)
