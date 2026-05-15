from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.catalog.models import AIProviderConfig

from .base import BaseAIProvider


class ProviderFactory:
    """AIProviderConfig DB yozuvidan to'g'ri provayder obyektini yaratadi."""

    _MAP = {
        'claude': ('apps.catalog.services.ai_providers.claude',   'AnthropicProvider'),
        'openai': ('apps.catalog.services.ai_providers.openai_p', 'OpenAIProvider'),
        'gemini': ('apps.catalog.services.ai_providers.gemini',   'GeminiProvider'),
        'ollama': ('apps.catalog.services.ai_providers.ollama',   'OllamaProvider'),
    }

    @classmethod
    def create(cls, config: AIProviderConfig) -> BaseAIProvider:
        entry = cls._MAP.get(config.provider)
        if not entry:
            raise ValueError(f"Noma'lum provider: '{config.provider}'")

        module_path, class_name = entry
        import importlib
        module = importlib.import_module(module_path)
        klass  = getattr(module, class_name)

        return klass(
            api_key  = config.api_key,
            model    = config.model,
            base_url = config.base_url,
        )
