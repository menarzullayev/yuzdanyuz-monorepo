import json

import anthropic

from .base import SYSTEM_PROMPT, BaseAIProvider


class AnthropicProvider(BaseAIProvider):
    """Anthropic Claude — any Messages API model (claude-sonnet-5-5, claude-opus-5-5, ...)."""

    def __init__(self, api_key: str, model: str, **_):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def parse_text_block(self, text: str) -> dict:
        try:
            # ISSUE-X04: ephemeral prompt cache on system block — reduces token
            # cost on repeated parser calls (system prompt is identical every call).
            # The model comes from AIProviderConfig; Claude 5.x models think by
            # default and thinking counts toward max_tokens, hence the headroom.
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=[
                    {
                        'type': 'text',
                        'text': SYSTEM_PROMPT,
                        'cache_control': {'type': 'ephemeral'},
                    },
                ],
                messages=[{'role': 'user', 'content': self._prompt(text)}],
            )
            if msg.stop_reason == 'refusal':
                category = getattr(msg.stop_details, 'category', None)
                return {
                    'error': f'Claude declined the request (category={category})',
                    'raw_text': text,
                }
            # A reply can start with a thinking block, so read text blocks by type.
            return json.loads(''.join(b.text for b in msg.content if b.type == 'text'))
        except json.JSONDecodeError as exc:
            return {'error': f'JSON parse xatosi: {exc}', 'raw_text': text}
        except Exception as exc:
            return {'error': str(exc), 'raw_text': text}
