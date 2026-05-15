import json

import httpx

from .base import SYSTEM_PROMPT, BaseAIProvider


class OllamaProvider(BaseAIProvider):
    """
    Ollama — mahalliy server, to'liq bepul modellar.

    O'rnatish:  https://ollama.com/download
    Model olish: ollama pull llama3
                 ollama pull mistral
                 ollama pull qwen2
                 ollama pull phi3        (yengil, tez)

    base_url odatda http://localhost:11434
    """

    def __init__(self, model: str, base_url: str = 'http://localhost:11434', **_):
        self.model = model
        self.base_url = base_url.rstrip('/')

    def parse_text_block(self, text: str) -> dict:
        # Ollama /api/generate — system va prompt birlashtiriladi
        full_prompt = f'{SYSTEM_PROMPT}\n\n{self._prompt(text)}'
        try:
            resp = httpx.post(
                f'{self.base_url}/api/generate',
                json={
                    'model': self.model,
                    'prompt': full_prompt,
                    'stream': False,
                    'format': 'json',
                },
                timeout=120.0,
            )
            resp.raise_for_status()
            return json.loads(resp.json()['response'])
        except json.JSONDecodeError as exc:
            return {'error': f'JSON parse xatosi: {exc}', 'raw_text': text}
        except Exception as exc:
            return {'error': str(exc), 'raw_text': text}
