"""
Audit D06/D07 — payment webhook hardening.

- Stub signature acceptance must only work in DEBUG/test settings; with real
  payments off in production-like settings the webhook and top-up endpoints
  are unavailable (503) and nothing is credited.
- `provider_tx_id` is never returned to the client.
- A duplicate or concurrent succeeded callback credits the wallet once.
"""

import hashlib
import hmac
import threading
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client, override_settings
from django.urls import reverse

from apps.commerce import views as commerce_views
from apps.commerce import wallet_service
from apps.commerce.models import PaymentIntent

PROD_LIKE = {'DEBUG': False, 'PAYMENTS_STUB_ALLOWED': False, 'ENABLE_REAL_PAYMENTS': False}


def _intent(user, tx_id, provider=PaymentIntent.Provider.PAYME):
    return PaymentIntent.objects.create(
        user=user,
        provider=provider,
        amount_uzs=Decimal('10000'),
        coins_to_credit=100,
        provider_tx_id=tx_id,
        status=PaymentIntent.Status.PROCESSING,
    )


def _post_payme(client, tx_id, signature='stub-sig', payload=None):
    return client.post(
        reverse('commerce:payme-webhook'),
        data=payload or {'id': tx_id, 'state': 'succeeded', 'amount': 1000000},
        content_type='application/json',
        HTTP_X_SIGNATURE=signature,
    )


def _check():
    from apps.commerce.checks import check_payment_settings

    return check_payment_settings(None)


def _balance(user):
    return wallet_service.get_or_create_wallet(user).balance_coins


@pytest.mark.security
class TestProdLikeSettingsRejectStub:
    @override_settings(**PROD_LIKE)
    def test_forged_payme_signature_rejected_and_no_credit(self, db, user):
        intent = _intent(user, 'payme-forged-1')
        resp = _post_payme(Client(), 'payme-forged-1', signature='anything')
        assert resp.status_code == 503
        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.PROCESSING
        assert _balance(user) == 0

    @override_settings(**PROD_LIKE)
    def test_forged_click_signature_rejected_and_no_credit(self, db, user):
        _intent(user, 'click-forged-1', provider=PaymentIntent.Provider.CLICK)
        resp = Client().post(
            reverse('commerce:click-webhook'),
            data={'payment_id': 'click-forged-1', 'status': 1},
            content_type='application/json',
            HTTP_X_SIGNATURE='anything',
        )
        assert resp.status_code == 503
        assert _balance(user) == 0

    @override_settings(**PROD_LIKE)
    def test_provider_rejects_stub_signature_directly(self):
        from apps.commerce.payment_providers import get_provider

        assert not get_provider('payme').verify_webhook_signature({'id': 'x'}, 'any')
        assert not get_provider('click').verify_webhook_signature({'payment_id': 'x'}, 'any')

    @override_settings(**PROD_LIKE)
    def test_topup_unavailable_without_real_mode(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:wallet-topup'),
            data={'provider': 'payme', 'amount_uzs': 10000},
            content_type='application/json',
        )
        assert resp.status_code == 503
        assert not PaymentIntent.objects.filter(user=user).exists()

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='real-secret', DEBUG=False)
    def test_real_mode_forged_signature_rejected(self, db, user):
        _intent(user, 'payme-real-1')
        resp = _post_payme(Client(), 'payme-real-1', signature='forged')
        assert resp.status_code == 401
        assert _balance(user) == 0

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='real-secret', DEBUG=False)
    def test_real_mode_valid_hmac_credits(self, db, user):
        _intent(user, 'payme-real-2')
        payload = {'id': 'payme-real-2', 'state': 'succeeded', 'amount': 1000000}
        sig = hmac.new(b'real-secret', str(payload).encode(), hashlib.sha256).hexdigest()
        resp = _post_payme(Client(), 'payme-real-2', signature=sig, payload=payload)
        assert resp.status_code == 200
        assert _balance(user) == 100


@pytest.mark.security
class TestStubStillWorksInTestSettings:
    def test_stub_webhook_credits(self, db, user):
        _intent(user, 'payme-stub-ok')
        resp = _post_payme(Client(), 'payme-stub-ok')
        assert resp.status_code == 200
        assert _balance(user) == 100

    @override_settings(DEBUG=True, PAYMENTS_STUB_ALLOWED=False, ENABLE_REAL_PAYMENTS=False)
    def test_stub_allowed_under_debug(self, db, user):
        _intent(user, 'payme-stub-debug')
        resp = _post_payme(Client(), 'payme-stub-debug')
        assert resp.status_code == 200
        assert _balance(user) == 100

    def test_stub_empty_signature_still_rejected(self, db, user):
        _intent(user, 'payme-stub-empty')
        resp = _post_payme(Client(), 'payme-stub-empty', signature='')
        assert resp.status_code == 401
        assert _balance(user) == 0


@pytest.mark.security
class TestTopUpResponse:
    def test_provider_tx_id_not_returned(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:wallet-topup'),
            data={'provider': 'payme', 'amount_uzs': 10000},
            content_type='application/json',
        )
        assert resp.status_code == 201
        body = resp.json()
        assert 'provider_tx_id' not in body
        intent = PaymentIntent.objects.get(id=body['intent_id'])
        assert intent.provider_tx_id not in resp.content.decode()


@pytest.mark.security
class TestWebhookIdempotency:
    def test_same_callback_twice_credits_once(self, db, user):
        _intent(user, 'payme-dup-1')
        client = Client()
        assert _post_payme(client, 'payme-dup-1').status_code == 200
        assert _post_payme(client, 'payme-dup-1').status_code == 200
        assert _balance(user) == 100

    def test_concurrent_callback_credits_once(self, transactional_db, user, monkeypatch):
        """
        A second callback arrives while the first is between crediting the
        wallet and marking the intent succeeded. Without a row lock on the
        intent it sees PROCESSING and credits again.
        """
        _intent(user, 'payme-race-1')
        original_top_up = wallet_service.top_up
        second = {}

        def run_second():
            try:
                second['resp'] = _post_payme(Client(), 'payme-race-1')
            finally:
                connection.close()

        def top_up_then_race(*args, **kwargs):
            result = original_top_up(*args, **kwargs)
            if 'thread' not in second:
                t = threading.Thread(target=run_second)
                second['thread'] = t
                t.start()
                # Give the second request time to reach the intent lookup.
                t.join(timeout=1.5)
            return result

        monkeypatch.setattr(commerce_views.wallet_service, 'top_up', top_up_then_race)

        first = _post_payme(Client(), 'payme-race-1')
        second['thread'].join(timeout=10)
        assert not second['thread'].is_alive()
        assert first.status_code == 200
        assert second['resp'].status_code == 200
        assert _balance(user) == 100
        assert wallet_service.get_or_create_wallet(user).transactions.count() == 1


@pytest.mark.security
class TestPaymentSystemCheck:
    @override_settings(**PROD_LIKE)
    def test_warns_when_payments_disabled_in_prod(self):
        ids = {m.id for m in _check()}
        assert 'commerce.W001' in ids

    @override_settings(ENABLE_REAL_PAYMENTS=True, PAYME_SECRET_KEY='', CLICK_SECRET_KEY='')
    def test_errors_when_real_mode_has_no_secrets(self):
        ids = {m.id for m in _check()}
        assert 'commerce.E001' in ids

    def test_clean_in_test_settings(self):
        assert _check() == []
