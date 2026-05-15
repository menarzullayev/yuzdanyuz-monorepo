import json

import anthropic

from .base import SYSTEM_PROMPT, BaseAIProvider


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude — claude-sonnet-4-6, claude-opus-4-7, ..."""

    def __init__(self, api_key: str, model: str, **_):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def parse_text_block(self, text: str) -> dict:
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{'role': 'user', 'content': self._prompt(text)}],
            )
            return json.loads(msg.content[0].text)
        except json.JSONDecodeError as exc:
            return {'error': f'JSON parse xatosi: {exc}', 'raw_text': text}
        except Exception as exc:
            return {'error': str(exc), 'raw_text': text}
