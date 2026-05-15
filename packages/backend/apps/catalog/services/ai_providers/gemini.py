import json

from .base import SYSTEM_PROMPT, BaseAIProvider


class GeminiProvider(BaseAIProvider):
    """
    Google Gemini — gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash, ...
    O'rnatish: pip install google-generativeai
    """

    def __init__(self, api_key: str, model: str, **_):
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError(
                "google-generativeai paketi o'rnatilmagan. pip install google-generativeai"
            )

        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            system_instruction=SYSTEM_PROMPT,
            generation_config=genai.GenerationConfig(response_mime_type='application/json'),
        )

    def parse_text_block(self, text: str) -> dict:
        try:
            response = self._model.generate_content(self._prompt(text))
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            return {'error': f'JSON parse xatosi: {exc}', 'raw_text': text}
        except Exception as exc:
            return {'error': str(exc), 'raw_text': text}
