import json

from .base import BaseAIProvider, SYSTEM_PROMPT


class OpenAIProvider(BaseAIProvider):
    """
    OpenAI GPT — gpt-4o, gpt-4o-mini, gpt-3.5-turbo, ...
    base_url ni o'zgartirib Groq (bepul), Together AI va boshqalarni ham ishlatsa bo'ladi.
    """

    def __init__(self, api_key: str, model: str, base_url: str = '', **_):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai paketi o'rnatilmagan. pip install openai")

        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model  = model

    def parse_text_block(self, text: str) -> dict:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": self._prompt(text)},
                ],
            )
            return json.loads(resp.choices[0].message.content)
        except json.JSONDecodeError as exc:
            return {"error": f"JSON parse xatosi: {exc}", "raw_text": text}
        except Exception as exc:
            return {"error": str(exc), "raw_text": text}
