"""ISSUE-408 — PluginRegistry tests."""

import pytest
from django.test import override_settings

from core.plugins import PluginRegistry, payments


@pytest.mark.unit
class TestPluginRegistry:
    def setup_method(self):
        # Reset cache between tests (settings o'zgaradi)
        payments.reload()

    def test_empty_settings_returns_empty(self):
        with override_settings(PROVIDERS_FAKE=[]):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            assert reg.classes() == []
            assert reg.instances() == []

    def test_missing_setting_returns_empty(self):
        reg = PluginRegistry('nonexistent', 'PROVIDERS_NONEXISTENT_XYZ')
        assert reg.classes() == []

    def test_loads_class_from_dotted_path(self):
        with override_settings(PROVIDERS_FAKE=['apps.commerce.payment_providers.BaseProvider']):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            classes = reg.classes()
            assert len(classes) == 1
            assert classes[0].__name__ == 'BaseProvider'

    def test_cache_avoids_reimport(self):
        with override_settings(PROVIDERS_FAKE=['apps.commerce.payment_providers.BaseProvider']):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            first = reg.classes()
            second = reg.classes()
            assert first == second  # same instances

    def test_reload_drops_cache(self):
        with override_settings(PROVIDERS_FAKE=['apps.commerce.payment_providers.BaseProvider']):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            reg.classes()
            assert reg._cache is not None
            reg.reload()
            assert reg._cache is None

    def test_by_name_matches_class_attribute(self):
        with override_settings(
            PROVIDERS_FAKE=[
                'apps.commerce.payment_providers.PaymeProvider',
                'apps.commerce.payment_providers.ClickProvider',
            ]
        ):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            payme = reg.by_name('payme')
            assert payme is not None
            assert payme.name == 'payme'
            assert reg.by_name('nonexistent') is None

    def test_by_name_case_insensitive(self):
        with override_settings(PROVIDERS_FAKE=['apps.commerce.payment_providers.PaymeProvider']):
            reg = PluginRegistry('fake', 'PROVIDERS_FAKE')
            assert reg.by_name('PAYME') is not None
            assert reg.by_name('Payme') is not None
