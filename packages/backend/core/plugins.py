"""ISSUE-408 — Plugin / modular architecture.

Settings-driven plugin registry. Har plugin "namespace"'iga settings'da
dotted-import path'lar ro'yxati orqali registratsiya qilinadi. Enterprise
client'lar `apps/<their_app>/providers.py`'da o'z plugin'ini yozadi va
`settings.PROVIDERS_PAYMENTS` ro'yxatiga dotted path qo'shadi — kod
o'zgartirish (`if provider == 'payme': ... elif provider == 'click': ...`)
kerakmas.

Settings example:
    PROVIDERS_PAYMENTS = [
        'apps.commerce.payment_providers.PaymeProvider',
        'apps.commerce.payment_providers.ClickProvider',
        # B2B custom integration:
        # 'theirorg.gateways.UzPayProvider',
    ]
    PROVIDERS_SMS = [
        'apps.accounts.services.sms_backend.PlayMobileBackend',
        # 'apps.accounts.services.sms_backend.EskizBackend',
    ]
    PROVIDERS_AI = [
        'apps.intelligence.providers.AnthropicProvider',
    ]
    PROVIDERS_ANTICHEAT = [
        'apps.exams.anti_cheat.StrikeDetector',
    ]

Note: stevedore entry_points'ga keyinchalik o'tish mumkin — bu uchun backend'ni
proper pip-installable package'ga aylantirish kerak (pyproject.toml [project]
section + setup.cfg [options.entry_points]). Hozir embedded Django app sifatida
settings-based registratsiya yetadi.
"""

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string


class PluginRegistry:
    """Per-namespace plugin loader.

    Usage:
        from core.plugins import payments
        for cls in payments.classes():
            ... instantiate ...
        provider = payments.by_name('payme')  # match cls.name == 'payme'
    """

    def __init__(self, namespace: str, settings_key: str):
        self.namespace = namespace
        self.settings_key = settings_key
        self._cache: list[type] | None = None

    def classes(self) -> list[type]:
        """Lazily import + cache configured plugin classes."""
        if self._cache is None:
            paths = getattr(settings, self.settings_key, [])
            self._cache = [import_string(p) for p in paths]
        return list(self._cache)

    def instances(self) -> list[Any]:
        """Instantiate all classes (no args). Override if plugins need ctor args."""
        return [cls() for cls in self.classes()]

    def by_name(self, name: str) -> Any | None:
        """Find an instance whose `cls.name == name` (case-insensitive)."""
        for cls in self.classes():
            if getattr(cls, 'name', '').lower() == name.lower():
                return cls()
        return None

    def reload(self) -> None:
        """Drop cache — test/dev hot-reload uchun."""
        self._cache = None


# ── Namespaces ──────────────────────────────────────────────────────────────

payments = PluginRegistry('payments', 'PROVIDERS_PAYMENTS')
sms = PluginRegistry('sms', 'PROVIDERS_SMS')
ai = PluginRegistry('ai', 'PROVIDERS_AI')
anticheat = PluginRegistry('anticheat', 'PROVIDERS_ANTICHEAT')
