"""Audit H05: staff can switch features off at runtime; flags never switch on what env left off."""

import json

import pytest
from django.test import Client

from core.feature_flags import feature_on


def _kill(name):
    """What an admin does at /admin/flags/: a required boolean False condition."""
    from flags.models import FlagState

    FlagState.objects.create(name=name, condition='boolean', value='False', required=True)


@pytest.mark.integration
class TestFeatureOn:
    @pytest.mark.parametrize('name', ['ENABLE_AI_PARSER', 'ENABLE_REAL_PAYMENTS', 'ENABLE_SMS_OTP'])
    def test_env_on_and_flag_default_is_on(self, db, settings, name):
        setattr(settings, name, True)
        assert feature_on(name) is True

    @pytest.mark.parametrize('name', ['ENABLE_AI_PARSER', 'ENABLE_REAL_PAYMENTS', 'ENABLE_SMS_OTP'])
    def test_kill_switch_turns_it_off_without_restart(self, db, settings, name):
        setattr(settings, name, True)
        _kill(name)
        assert feature_on(name) is False

    @pytest.mark.parametrize('name', ['ENABLE_AI_PARSER', 'ENABLE_REAL_PAYMENTS', 'ENABLE_SMS_OTP'])
    def test_flag_cannot_enable_what_env_left_off(self, db, settings, name):
        from flags.models import FlagState

        setattr(settings, name, False)
        FlagState.objects.create(name=name, condition='boolean', value='True', required=False)
        assert feature_on(name) is False


@pytest.mark.integration
class TestWiring:
    def test_real_payments_kill_switch_stops_new_checkouts(self, db, settings):
        from apps.commerce import payment_providers

        settings.ENABLE_REAL_PAYMENTS = True
        assert payment_providers.payments_available() is True
        _kill('ENABLE_REAL_PAYMENTS')
        assert payment_providers.payments_available() is False
        # Webhooks of charges already in flight keep the real-mode verification path.
        assert payment_providers._is_real_mode() is True

    def test_stub_mode_is_not_affected(self, db, settings):
        from apps.commerce import payment_providers

        settings.ENABLE_REAL_PAYMENTS = False
        settings.DEBUG = True
        _kill('ENABLE_REAL_PAYMENTS')
        assert payment_providers.payments_available() is True

    def test_ai_parser_kill_switch(self, db, settings):
        from apps.catalog.services import parsers

        settings.ENABLE_AI_PARSER = True
        assert parsers._ai_enabled() is True
        _kill('ENABLE_AI_PARSER')
        assert parsers._ai_enabled() is False

    def test_sms_otp_kill_switch_closes_the_endpoint(self, db, settings, phone_number):
        settings.ENABLE_SMS_OTP = True
        _kill('ENABLE_SMS_OTP')
        resp = Client().post(
            '/api/auth/otp/send/',
            data=json.dumps({'phone': phone_number}),
            content_type='application/json',
            REMOTE_ADDR='198.51.100.7',
        )
        assert resp.status_code == 404
