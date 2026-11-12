import json

from ._http import post_json
from .base import SYSTEM_PROMPT, BaseAIProvider

DEFAULT_BASE_URL = 'https://api.openai.com/v1'


class OpenAIProvider(BaseAIProvider):
    """
    OpenAI-compatible chat API (OpenAI, Groq, Together, vLLM, ...) over plain HTTP.
    base_url points at the API root, e.g. https://api.groq.com/openai/v1.
    """

    def __init__(self, api_key: str, model: str, base_url: str = '', **_):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip('/')

    def complete(
        self,
        prompt: str,
        *,
        system: str = '',
        json_schema: dict | None = None,
        max_tokens: int = 2048,
    ) -> str:
        messages = []
        if json_schema is not None:
            # json_object mode requires the word JSON somewhere in the conversation
            messages.append({'role': 'system', 'content': 'Reply with a single JSON object.'})
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': prompt})
        body: dict = {'model': self.model, 'messages': messages, 'max_tokens': max_tokens}
        if json_schema is not None:
            body['response_format'] = {'type': 'json_object'}
        data = post_json(
            f'{self.base_url}/chat/completions',
            json=body,
            headers={'Authorization': f'Bearer {self.api_key}'},
            secret=self.api_key,
        )
        try:
            return data['choices'][0]['message']['content'] or ''
        except (KeyError, IndexError, TypeError):
            raise RuntimeError('Unexpected OpenAI response shape') from None

    def parse_text_block(self, text: str) -> dict:
        try:
            raw = self.complete(
                self._prompt(text), system=SYSTEM_PROMPT, json_schema={'type': 'object'}
            )
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            return {'error': f'JSON parse xatosi: {exc}', 'raw_text': text}
        except Exception as exc:
            return {'error': str(exc), 'raw_text': text}
