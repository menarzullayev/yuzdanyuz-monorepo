"""ISSUE-114 — SMS balance check tests."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.accounts import sms_balance

pytestmark = pytest.mark.unit


class _MockResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class TestPlayMobileBalance:
    def test_unconfigured_returns_configured_false(self, db):
        with override_settings(PLAYMOBILE_LOGIN='', PLAYMOBILE_PASSWORD=''):
            result = sms_balance.check_playmobile_balance()
        assert result['configured'] is False
        assert result['balance_uzs'] is None

    def test_ok_response_sets_metric(self, db):
        with override_settings(PLAYMOBILE_LOGIN='u', PLAYMOBILE_PASSWORD='p'):
            with patch.object(
                sms_balance.requests,
                'get',
                return_value=_MockResp(200, {'balance': 123456.78}),
            ):
                result = sms_balance.check_playmobile_balance()
        assert result['balance_uzs'] == 123456
        assert result['configured'] is True
        assert 'error' not in result

    def test_http_error_returns_error(self, db):
        with override_settings(PLAYMOBILE_LOGIN='u', PLAYMOBILE_PASSWORD='p'):
            with patch.object(
                sms_balance.requests,
                'get',
                return_value=_MockResp(401, {'error': 'unauthorized'}),
            ):
                result = sms_balance.check_playmobile_balance()
        assert result['balance_uzs'] is None
        assert result['error'] == 'http_401'


class TestEskizBalance:
    def test_unconfigured(self, db):
        with override_settings(ESKIZ_EMAIL='', ESKIZ_PASSWORD=''):
            result = sms_balance.check_eskiz_balance()
        assert result['configured'] is False

    def test_ok_path(self, db):
        with override_settings(ESKIZ_EMAIL='a@b.c', ESKIZ_PASSWORD='p'):
            with patch.object(
                sms_balance.requests,
                'post',
                return_value=_MockResp(200, {'data': {'token': 'tok'}}),
            ):
                with patch.object(
                    sms_balance.requests,
                    'get',
                    return_value=_MockResp(200, {'data': {'balance': 50000}}),
                ):
                    result = sms_balance.check_eskiz_balance()
        assert result['balance_uzs'] == 50000


class TestPollSMSBalances:
    def test_low_provider_listed(self, db):
        with override_settings(
            PLAYMOBILE_LOGIN='u',
            PLAYMOBILE_PASSWORD='p',
            ESKIZ_EMAIL='',
            ESKIZ_PASSWORD='',
            SMS_BALANCE_THRESHOLD_UZS=100000,
        ):
            with patch.object(
                sms_balance.requests,
                'get',
                return_value=_MockResp(200, {'balance': 5000}),  # past
            ):
                result = sms_balance.poll_sms_balances()
        assert 'playmobile' in result['low']
        assert result['threshold_uzs'] == 100000
