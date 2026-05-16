"""
ISSUE-107 — Payment webhook signature security tests.

Maqsad: PROD'da `ENABLE_REAL_PAYMENTS=true` bo'lganda webhook signature
verification real ishlashini tasdiqlash. Stub mode'da test bilan adashtirish
mumkin emas — production'da har webhook signed bo'lishi shart.

CI tasdiqlash: agar provider `_verify_webhook_signature` stub-mode'ga
to'g'ridan-to'g'ri qaytib qolsa (regression), bu test fail beradi.
"""

import hashlib
import hmac

import pytest
from django.test import override_settings
from django.urls import reverse


@pytest.mark.security
class TestStubModeAcceptsAnySignature:
    """Stub mode (ENABLE_REAL_PAYMENTS=False) — har signed string qabul qilinadi."""

    def test_stub_mode_accepts_any_non_empty_signature(self, settings):
        from apps.commerce.payment_providers import get_provider

        settings.ENABLE_REAL_PAYMENTS = False
        provider = get_provider('payme')
        assert provider.verify_webhook_signature({'id': 'tx-1'}, 'any-signature')

    def test_stub_mode_rejects_empty_signature(self, settings):
        """Stub mode'da ham empty signature rad etiladi (basic sanity)."""
        from apps.commerce.payment_providers import get_provider

        settings.ENABLE_REAL_PAYMENTS = False
        provider = get_provider('payme')
        assert not provider.verify_webhook_signature({'id': 'tx-1'}, '')


@pytest.mark.security
class TestRealModeStrictHMAC:
    """ENABLE_REAL_PAYMENTS=True bo'lganda — HMAC-SHA256 majburiy va to'g'ri bo'lishi shart."""

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='test-secret-payme')
    def test_payme_real_mode_invalid_signature_rejected(self):
        from apps.commerce.payment_providers import get_provider

        provider = get_provider('payme')
        assert not provider.verify_webhook_signature({'id': 'tx-1'}, 'wrong-signature')

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='test-secret-payme')
    def test_payme_real_mode_valid_signature_accepted(self):
        from apps.commerce.payment_providers import get_provider

        provider = get_provider('payme')
        payload = {'id': 'tx-1', 'state': 'succeeded'}
        body = str(payload).encode()
        valid_sig = hmac.new(b'test-secret-payme', body, hashlib.sha256).hexdigest()
        assert provider.verify_webhook_signature(payload, valid_sig)

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='')
    def test_payme_real_mode_missing_secret_rejects_all(self):
        """Secret env-var bo'sh — production'da hech bir webhook qabul qilinmasligi shart."""
        from apps.commerce.payment_providers import get_provider

        provider = get_provider('payme')
        assert not provider.verify_webhook_signature({'id': 'tx-1'}, 'any-sig')

    @override_settings(ENABLE_REAL_PAYMENTS=True, CLICK_SECRET_KEY='test-secret-click')
    def test_click_real_mode_invalid_signature_rejected(self):
        from apps.commerce.payment_providers import get_provider

        provider = get_provider('click')
        assert not provider.verify_webhook_signature({'payment_id': '123'}, 'forged-sig')

    @override_settings(ENABLE_REAL_PAYMENTS=True, CLICK_SECRET_KEY='test-secret-click')
    def test_click_real_mode_valid_signature_accepted(self):
        from apps.commerce.payment_providers import get_provider

        provider = get_provider('click')
        payload = {'payment_id': '123', 'status': 1}
        body = str(payload).encode()
        valid_sig = hmac.new(b'test-secret-click', body, hashlib.sha256).hexdigest()
        assert provider.verify_webhook_signature(payload, valid_sig)


@pytest.mark.security
class TestWebhookEndpointRejectsInvalidSignature:
    """End-to-end: webhook endpoint invalid signature uchun 401 qaytarishi."""

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='test-secret')
    def test_payme_webhook_invalid_signature_returns_401(self, db):
        from django.test import Client

        client = Client()
        resp = client.post(
            reverse('commerce:payme-webhook'),
            data={'id': 'tx-fake', 'state': 'succeeded'},
            content_type='application/json',
            HTTP_X_SIGNATURE='invalid-signature',
        )
        assert resp.status_code == 401
        assert b'Invalid signature' in resp.content

    @override_settings(ENABLE_REAL_PAYMENTS=True, CLICK_SECRET_KEY='test-secret')
    def test_click_webhook_invalid_signature_returns_401(self, db):
        from django.test import Client

        client = Client()
        resp = client.post(
            reverse('commerce:click-webhook'),
            data={'payment_id': 'tx-fake', 'status': 1},
            content_type='application/json',
            HTTP_X_SIGNATURE='forged-sig',
        )
        assert resp.status_code == 401
