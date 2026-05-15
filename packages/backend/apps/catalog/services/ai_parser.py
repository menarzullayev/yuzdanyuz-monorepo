import logging

logger = logging.getLogger(__name__)


class AIParserService:
    """
    DB dagi AIProviderConfig dan aktiv provyderni oladi va matnni parse qiladi.

    Ishlash tartibi:
      1. use_for='parser' yoki 'all', is_active=True bo'lgan provyderlar priority bo'yicha olinadi.
      2. Birinchi provyder urinadi. Xato bo'lsa keyingisi (fallback).
      3. Hech qaysi ishlamasa xato dict qaytaradi.

    Admin panelda yangi provyder qo'shish yoki o'chirish — qayta ishga tushirish kerak emas.
    """

    def parse_text_block(self, text: str) -> dict:
        from apps.catalog.models import AIProviderConfig

        from .ai_providers import ProviderFactory

        configs = AIProviderConfig.objects.filter(
            is_active=True,
            use_for__in=[AIProviderConfig.UseFor.PARSER, AIProviderConfig.UseFor.ALL],
        ).order_by('priority')

        if not configs.exists():
            return {
                'error': 'Hech qanday AI provider sozlanmagan. '
                'Admin panel → AI Provider konfiguratsiyalari ga kiring.',
                'raw_text': text,
            }

        last_error = None
        for config in configs:
            try:
                provider = ProviderFactory.create(config)
                result = provider.parse_text_block(text)
                if 'error' not in result:
                    return result
                last_error = result
                logger.warning("Provider '%s' xato qaytardi: %s", config.name, result.get('error'))
            except Exception as exc:
                last_error = {'error': str(exc), 'raw_text': text}
                logger.error("Provider '%s' ishlamadi: %s", config.name, exc)

        return last_error or {
            'error': "Barcha provyderlar muvaffaqiyatsiz bo'ldi.",
            'raw_text': text,
        }
