"""Payment webhooks must verify the reported amount against the intent.

Payme reports tiyin (x100), Click reports so'm. A mismatch is rejected with the
provider's "incorrect amount" code, activates/credits nothing, and logs a warning.
"""

import logging
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.commerce import wallet_service
from apps.commerce.models import PaymentIntent, PromoCode, UserSubscription
from tests.integration._monetisation import student_plan

pytestmark = pytest.mark.security


def _intent(user, provider, amount='10000', tx='tx-1', coins=100, **kw):
    return PaymentIntent.objects.create(
        user=user,
        provider=provider,
        amount_uzs=Decimal(amount),
        coins_to_credit=coins,
        provider_tx_id=tx,
        status=PaymentIntent.Status.PROCESSING,
        **kw,
    )


def _post(provider, tx, amount, **extra):
    if provider == 'payme':
        payload = {'id': tx, 'state': 'succeeded', **extra}
        url = 'commerce:payme-webhook'
    else:
        payload = {'payment_id': tx, 'status': 1, **extra}
        url = 'commerce:click-webhook'
    if amount is not None:
        payload['amount'] = amount
    return Client().post(
        reverse(url), data=payload, content_type='application/json', HTTP_X_SIGNATURE='sig'
    )


def _assert_rejected(resp, provider, intent, user):
    expected_code = {'payme': -31001, 'click': -2}[provider]
    assert resp.status_code == 400
    assert resp.json()['error']['code'] == expected_code
    intent.refresh_from_db()
    assert intent.status == PaymentIntent.Status.PROCESSING
    assert wallet_service.get_or_create_wallet(user).balance_coins == 0


# (provider, exact, under, over, wrong_unit) for a 10 000 so'm intent
CASES = [
    ('payme', 1_000_000, 999_999, 1_000_001, 10_000),  # exact tiyin; so'm sent as tiyin
    ('click', 10000, 9999, 10001, 1_000_000),  # exact so'm; tiyin sent as so'm
]


@pytest.mark.parametrize('provider,exact,under,over,wrong_unit', CASES)
class TestAmountVerification:
    def test_exact_amount_succeeds(self, db, user, provider, exact, under, over, wrong_unit):
        intent = _intent(user, provider)
        assert _post(provider, 'tx-1', exact).status_code == 200
        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.SUCCEEDED
        assert wallet_service.get_or_create_wallet(user).balance_coins == 100

    def test_underpayment_rejected(self, db, user, provider, exact, under, over, wrong_unit):
        intent = _intent(user, provider)
        _assert_rejected(_post(provider, 'tx-1', under), provider, intent, user)

    def test_overpayment_rejected(self, db, user, provider, exact, under, over, wrong_unit):
        intent = _intent(user, provider)
        _assert_rejected(_post(provider, 'tx-1', over), provider, intent, user)

    def test_wrong_unit_rejected(self, db, user, provider, exact, under, over, wrong_unit):
        intent = _intent(user, provider)
        _assert_rejected(_post(provider, 'tx-1', wrong_unit), provider, intent, user)

    def test_missing_or_garbage_amount_rejected(
        self, db, user, provider, exact, under, over, wrong_unit
    ):
        intent = _intent(user, provider)
        for bad in (None, 'abc', -5, True, 'NaN'):
            _assert_rejected(_post(provider, 'tx-1', bad), provider, intent, user)

    def test_mismatch_logged_as_security_warning(
        self, db, user, caplog, provider, exact, under, over, wrong_unit
    ):
        _intent(user, provider)
        with caplog.at_level(logging.WARNING, logger='apps.commerce.views'):
            _post(provider, 'tx-1', under)
        rec = [r for r in caplog.records if 'SECURITY payment amount mismatch' in r.getMessage()]
        assert rec and rec[0].levelno == logging.WARNING

    def test_correct_payment_still_works_after_rejection(
        self, db, user, provider, exact, under, over, wrong_unit
    ):
        intent = _intent(user, provider)
        _post(provider, 'tx-1', under)
        assert _post(provider, 'tx-1', exact).status_code == 200
        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.SUCCEEDED

    def test_failed_state_needs_no_amount(self, db, user, provider, exact, under, over, wrong_unit):
        intent = _intent(user, provider)
        if provider == 'payme':
            resp = _post(provider, 'tx-1', None, state='failed')
        else:
            resp = Client().post(
                reverse('commerce:click-webhook'),
                data={'payment_id': 'tx-1', 'status': -1},
                content_type='application/json',
                HTTP_X_SIGNATURE='sig',
            )
        assert resp.status_code == 200
        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.FAILED


class TestPromoDiscountedOrders:
    """Order created at the discounted price: only the discounted amount is accepted."""

    @pytest.fixture
    def discounted(self, db, user, org, member):
        plan = student_plan('all-month', 'all_types', price='100000')
        PromoCode.objects.create(code='HALF', discount_type='percent', value=Decimal('50'))
        c = Client()
        c.force_login(user)
        r = c.post(
            reverse('commerce:billing-checkout'),
            {'plan_slug': plan.slug, 'provider': 'payme', 'promo_code': 'half'},
            content_type='application/json',
        )
        assert r.status_code == 201 and r.json()['amount_uzs'] == '50000.00'
        return PaymentIntent.objects.get(user=user)

    @pytest.mark.parametrize(
        'tiyin',
        [10_000_000, 5_000_001, 4_999_999, 100],  # full price, over, under, far lower
    )
    def test_wrong_amounts_rejected_and_nothing_activated(self, discounted, user, tiyin):
        resp = _post('payme', discounted.provider_tx_id, tiyin)
        assert resp.status_code == 400 and resp.json()['error']['code'] == -31001
        discounted.refresh_from_db()
        assert discounted.status == PaymentIntent.Status.PROCESSING
        assert not UserSubscription.objects.filter(user=user).exists()
        from apps.commerce.models import PromoRedemption

        assert not PromoRedemption.objects.exists()

    def test_exact_discounted_amount_activates(self, discounted, user):
        assert _post('payme', discounted.provider_tx_id, 5_000_000).status_code == 200
        assert UserSubscription.objects.filter(user=user).count() == 1

    def test_click_exact_discounted_amount_activates(self, db, user, org, member):
        plan = student_plan('all-month', 'all_types', price='100000')
        PromoCode.objects.create(code='HALF', discount_type='percent', value=Decimal('50'))
        c = Client()
        c.force_login(user)
        c.post(
            reverse('commerce:billing-checkout'),
            {'plan_slug': plan.slug, 'provider': 'click', 'promo_code': 'half'},
            content_type='application/json',
        )
        intent = PaymentIntent.objects.get(user=user)
        assert _post('click', intent.provider_tx_id, 100000).status_code == 400  # full price
        assert not UserSubscription.objects.filter(user=user).exists()
        assert _post('click', intent.provider_tx_id, 50000).status_code == 200
        assert UserSubscription.objects.filter(user=user).count() == 1
