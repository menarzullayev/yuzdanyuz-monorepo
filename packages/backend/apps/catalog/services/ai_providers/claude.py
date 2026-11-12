import json

import anthropic

from .base import SYSTEM_PROMPT, BaseAIProvider

# Models that accept server-side refusal fallback (`fallbacks: "default"`).
# Exact IDs: a prefix match on 'claude-opus-5' would also catch other models.
SERVER_FALLBACK_MODELS = frozenset(
    {
        'claude-sonnet-5-5',
        'claude-opus-5-5',
        'claude-opus-5',
        'claude-fable-5-1',
        'claude-fable-5-2',
    }
)


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

    def complete(
        self,
        prompt: str,
        *,
        system: str = '',
        json_schema: dict | None = None,
        max_tokens: int = 2048,
    ) -> str:
        """Messages API turn. Claude 5.x thinks adaptively and thinking counts toward
        max_tokens; `effort: low` keeps it short on these scoring/generation requests.
        json_schema is not enforced here; the prompt must ask for JSON."""
        kwargs: dict = {
            'model': self.model,
            'max_tokens': max_tokens,
            'output_config': {'effort': 'low'},
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if system:
            # ISSUE-X04: ephemeral cache on the stable system block
            kwargs['system'] = [
                {'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}},
            ]
        if self.model in SERVER_FALLBACK_MODELS:
            # On a policy decline the API re-runs the request on a fallback model.
            message = self.client.beta.messages.create(
                betas=['server-side-fallback-2026-07-01'], fallbacks='default', **kwargs
            )
        else:
            message = self.client.messages.create(**kwargs)

        if message.stop_reason == 'refusal':
            category = getattr(message.stop_details, 'category', None)
            raise RuntimeError(f'Claude declined the request (refusal, category={category})')
        # A reply can start with a thinking block, so read text blocks by type.
        text = ''.join(block.text for block in message.content if block.type == 'text')
        if message.stop_reason == 'max_tokens':
            raise RuntimeError(f'Claude reply truncated at max_tokens={max_tokens}')
        return text
