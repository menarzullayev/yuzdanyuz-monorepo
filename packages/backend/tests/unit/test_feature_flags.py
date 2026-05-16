"""ISSUE-505 — Feature flag wrapper tests."""

import pytest

from core.feature_flags import is_enabled


@pytest.mark.unit
class TestFeatureFlags:
    def test_unknown_flag_returns_false(self):
        # Fail-closed: noma'lum flag → False (xavfsiz default)
        assert is_enabled('TOTALLY_UNKNOWN_FLAG_12345') is False

    def test_existing_settings_flags_return_bool(self):
        # Smoke test: settings.FLAGS dict'idagi flag'lar bool qaytaradi
        assert isinstance(is_enabled('ENABLE_AI_PARSER'), bool)
        assert isinstance(is_enabled('ENABLE_REAL_PAYMENTS'), bool)
        assert isinstance(is_enabled('EXPERIMENTAL_NEW_DASHBOARD'), bool)

    def test_experimental_new_dashboard_default_false(self):
        # settings'da `value: False` deb belgilangan
        assert is_enabled('EXPERIMENTAL_NEW_DASHBOARD') is False

    def test_no_exception_for_misconfigured(self):
        # is_enabled HEC qachon raise qilmaydi — har holatda bool
        result = is_enabled('___ANY_WEIRD_NAME___', extra_kwarg=12345)
        assert isinstance(result, bool)
