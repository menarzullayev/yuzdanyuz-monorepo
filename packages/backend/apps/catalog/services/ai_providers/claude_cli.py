from core import claude_cli

from .base import SYSTEM_PROMPT, BaseAIProvider

PARSER_SCHEMA = {
    'type': 'object',
    'properties': {
        'text': {'type': 'string'},
        'options': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'label': {'type': 'string'},
                    'text': {'type': 'string'},
                    'is_correct': {'type': 'boolean'},
                },
                'required': ['label', 'text', 'is_correct'],
                'additionalProperties': False,
            },
        },
        'explanation': {'type': 'string'},
    },
    'required': ['text', 'options'],
    'additionalProperties': False,
}


class ClaudeCLIProvider(BaseAIProvider):
    """Claude on the local subscription via `claude -p` (no API key; core/claude_cli.py)."""

    def __init__(self, model: str, **_):
        self.model = model

    def parse_text_block(self, text: str) -> dict:
        from django.conf import settings

        try:
            result = claude_cli.run(
                self._prompt(text),
                system=SYSTEM_PROMPT,
                model=self.model,
                json_schema=PARSER_SCHEMA,
                timeout=settings.CLAUDE_CLI_TIMEOUT,
            )
        except claude_cli.ClaudeCLIError as exc:
            return {'error': str(exc), 'raw_text': text}
        return result.structured

    def complete(
        self,
        prompt: str,
        *,
        system: str = '',
        json_schema: dict | None = None,
        max_tokens: int = 2048,
    ) -> str:
        import json

        from django.conf import settings

        result = claude_cli.run(
            prompt,
            system=system,
            model=self.model,
            json_schema=json_schema,
            timeout=settings.CLAUDE_CLI_TIMEOUT,
        )
        if json_schema is not None:
            return json.dumps(result.structured, ensure_ascii=False)
        return result.text
