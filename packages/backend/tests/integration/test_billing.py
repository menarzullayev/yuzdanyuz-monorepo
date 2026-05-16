"""
Task 7 — Billing/Wallet/Subscription/Affiliate integration tests.

Tekshiriladi:
  - Wallet atomic ops: top_up, spend, refund, InsufficientFunds
  - WalletTransaction audit trail
  - PaymentIntent + Payme/Click stub: initiate + webhook
  - Subscription: subscribe, activate, cancel, expire, lifetime
  - Auto-renew Celery task
  - Affiliate: code claim, threshold, withdraw cash
  - REST API endpoints
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.commerce import (
    affiliate_service,
    payment_providers,
    subscription_service,
    wallet_service,
)
from apps.commerce.models import (
    OrganizationSubscription,
    PaymentIntent,
    Referral,
    SubscriptionPlan,
    WalletTransaction,
)
from apps.commerce.tasks import auto_renew_subscriptions

# ─── Wallet service tests ────────────────────────────────────────────────────


@pytest.mark.integration
class TestWalletService:
    def test_topup_increases_balance(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        tx = wallet_service.top_up(wallet, 100, description='test')
        wallet.refresh_from_db()
        assert wallet.balance_coins == 100
        assert tx.coins_delta == 100
        assert tx.balance_after_coins == 100
        assert tx.kind == WalletTransaction.Kind.TOPUP

    def test_spend_decreases_balance(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100)
        tx = wallet_service.spend(wallet, 30, description='AI diag')
        wallet.refresh_from_db()
        assert wallet.balance_coins == 70
        assert tx.coins_delta == -30
        assert tx.kind == WalletTransaction.Kind.SPEND

    def test_spend_insufficient_funds_raises(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        with pytest.raises(wallet_service.InsufficientFunds):
            wallet_service.spend(wallet, 50)

    def test_refund_returns_coins(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100)
        spend_tx = wallet_service.spend(wallet, 50)
        wallet_service.refund(wallet, 50, original_tx=spend_tx)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 100  # back to original

    def test_audit_trail_records_all_ops(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100)
        wallet_service.spend(wallet, 30)
        wallet_service.top_up(wallet, 50)
        assert WalletTransaction.objects.filter(wallet=wallet).count() == 3

    def test_add_cash_uzs(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.add_cash_uzs(wallet, Decimal('15000.00'))
        wallet.refresh_from_db()
        assert wallet.pending_cash_uzs == Decimal('15000.00')

    def test_withdraw_cash_uzs_insufficient(self, db, user):
        wallet = wallet_service.get_or_create_wallet(user)
        with pytest.raises(wallet_service.InsufficientFunds):
            wallet_service.withdraw_cash_uzs(wallet, Decimal('100.00'))


# ─── Payment provider stub tests ─────────────────────────────────────────────


@pytest.mark.integration
class TestPaymentProviderStub:
    def test_payme_initiate_creates_processing_intent(self, db, user):
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.PAYME,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
        )
        provider = payment_providers.get_provider('payme')
        result = provider.initiate_charge(intent)
        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.PROCESSING
        assert intent.provider_tx_id.startswith('payme-stub-')
        assert 'stub-payme' in result['checkout_url']

    def test_click_initiate_creates_processing_intent(self, db, user):
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.CLICK,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
        )
        provider = payment_providers.get_provider('click')
        provider.initiate_charge(intent)
        intent.refresh_from_db()
        assert intent.provider_tx_id.startswith('click-stub-')

    def test_signature_stub_accepts_non_empty(self, db):
        provider = payment_providers.get_provider('payme')
        assert provider.verify_webhook_signature({}, 'any-signature') is True
        assert provider.verify_webhook_signature({}, '') is False


# ─── Webhook flow ────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestWebhookFlow:
    def test_payme_webhook_credits_wallet(self, db, user, member):
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.PAYME,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
            provider_tx_id='payme-test-123',
            status=PaymentIntent.Status.PROCESSING,
        )

        client = Client()
        # Webhook external — no auth
        resp = client.post(
            reverse('commerce:payme-webhook'),
            data={'id': 'payme-test-123', 'state': 'succeeded', 'amount': 10000},
            content_type='application/json',
            HTTP_X_SIGNATURE='stub-sig',
        )
        assert resp.status_code == 200

        intent.refresh_from_db()
        assert intent.status == PaymentIntent.Status.SUCCEEDED

        wallet = wallet_service.get_or_create_wallet(user)
        assert wallet.balance_coins == 100

    def test_webhook_idempotent(self, db, user):
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.PAYME,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
            provider_tx_id='payme-idem-1',
            status=PaymentIntent.Status.SUCCEEDED,
        )
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100, payment_intent=intent)

        client = Client()
        # Second webhook for same intent — should not double-credit
        resp = client.post(
            reverse('commerce:payme-webhook'),
            data={'id': 'payme-idem-1', 'state': 'succeeded', 'amount': 10000},
            content_type='application/json',
            HTTP_X_SIGNATURE='stub-sig',
        )
        assert resp.status_code == 200
        wallet.refresh_from_db()
        assert wallet.balance_coins == 100  # not 200

    def test_webhook_invalid_signature_401(self, db, user):
        PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.PAYME,
            amount_uzs=Decimal('10000'),
            coins_to_credit=100,
            provider_tx_id='payme-bad',
            status=PaymentIntent.Status.PROCESSING,
        )
        client = Client()
        resp = client.post(
            reverse('commerce:payme-webhook'),
            data={'id': 'payme-bad'},
            content_type='application/json',
            HTTP_X_SIGNATURE='',  # empty
        )
        assert resp.status_code == 401


# ─── Subscription service ────────────────────────────────────────────────────


@pytest.fixture
def monthly_plan(db):
    return SubscriptionPlan.objects.create(
        name='Monthly Bronze',
        slug='monthly-bronze',
        billing_period=SubscriptionPlan.BillingPeriod.MONTHLY,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('500000'),
        max_users=50,
    )


@pytest.fixture
def lifetime_plan(db):
    return SubscriptionPlan.objects.create(
        name='Lifetime Gold',
        slug='lifetime-gold',
        billing_period=SubscriptionPlan.BillingPeriod.LIFETIME,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('50000000'),
    )


@pytest.fixture
def per_seat_plan(db):
    return SubscriptionPlan.objects.create(
        name='Per-seat Enterprise',
        slug='per-seat-ent',
        billing_period=SubscriptionPlan.BillingPeriod.MONTHLY,
        pricing_model=SubscriptionPlan.PricingModel.PER_SEAT,
        price_uzs=Decimal('10000'),
    )


@pytest.mark.integration
class TestSubscriptionService:
    def test_subscribe_creates_trialing(self, db, org, monthly_plan):
        sub = subscription_service.subscribe(org, monthly_plan)
        assert sub.status == OrganizationSubscription.Status.TRIALING
        assert sub.current_period_ends_at is not None

    def test_lifetime_no_period_end(self, db, org, lifetime_plan):
        sub = subscription_service.subscribe(org, lifetime_plan)
        assert sub.current_period_ends_at is None

    def test_activate_changes_status(self, db, org, monthly_plan):
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.activate(sub)
        assert sub.status == OrganizationSubscription.Status.ACTIVE

    def test_cancel_sets_cancelled(self, db, org, monthly_plan):
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.cancel(sub, reason='Testing')
        assert sub.status == OrganizationSubscription.Status.CANCELLED
        assert sub.cancelled_at is not None
        assert sub.auto_renew is False

    def test_per_seat_charge_calculation(self, db, per_seat_plan):
        # 10 active users → 10 * 10000 = 100000 UZS
        amount = per_seat_plan.calculate_charge(active_user_count=10)
        assert amount == Decimal('100000')

    def test_flat_charge_ignores_user_count(self, db, monthly_plan):
        amount = monthly_plan.calculate_charge(active_user_count=999)
        assert amount == Decimal('500000')


# ─── Auto-renew Celery task ──────────────────────────────────────────────────


@pytest.mark.integration
class TestAutoRenew:
    def test_expired_active_renews_when_auto_renew_true(self, db, org, monthly_plan):
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.activate(sub)
        # Manually expire
        past = timezone.now() - timedelta(days=1)
        sub.current_period_ends_at = past
        sub.save(update_fields=['current_period_ends_at'])

        result = auto_renew_subscriptions()
        assert result['renewed'] == 1

        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.ACTIVE
        assert sub.current_period_ends_at > timezone.now()

    def test_expired_active_expires_when_auto_renew_false(self, db, org, monthly_plan):
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.activate(sub)
        sub.auto_renew = False
        past = timezone.now() - timedelta(days=1)
        sub.current_period_ends_at = past
        sub.save(update_fields=['auto_renew', 'current_period_ends_at'])

        result = auto_renew_subscriptions()
        assert result['expired'] == 1

        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.EXPIRED

    def test_lifetime_not_processed(self, db, org, lifetime_plan):
        sub = subscription_service.subscribe(org, lifetime_plan)
        subscription_service.activate(sub)
        result = auto_renew_subscriptions()
        # Lifetime current_period_ends_at=None → query exclude qiladi
        assert result['renewed'] == 0
        assert result['expired'] == 0


# ─── Affiliate ───────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestAffiliate:
    def test_get_or_create_code(self, db, user):
        code = affiliate_service.get_or_create_code(user)
        assert len(code.code) == 8
        assert code.code.isupper()

    def test_claim_referral_credits_inviter(self, db, user, user2, settings):
        settings.REFERRAL_COIN_BONUS = 50
        code = affiliate_service.get_or_create_code(user)
        ref = affiliate_service.claim_referral(code.code, user2)
        assert ref is not None
        assert ref.coin_reward == 50

        wallet = wallet_service.get_or_create_wallet(user)
        wallet.refresh_from_db()
        assert wallet.balance_coins == 50

    def test_self_referral_rejected(self, db, user):
        code = affiliate_service.get_or_create_code(user)
        ref = affiliate_service.claim_referral(code.code, user)
        assert ref is None

    def test_duplicate_referral_rejected(self, db, user, user2):
        code = affiliate_service.get_or_create_code(user)
        affiliate_service.claim_referral(code.code, user2)
        ref2 = affiliate_service.claim_referral(code.code, user2)
        assert ref2 is None

    def test_invalid_code_returns_none(self, db, user):
        ref = affiliate_service.claim_referral('NOSUCH', user)
        assert ref is None


# ─── REST API ────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestWalletAPI:
    def test_get_wallet_creates_on_demand(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('commerce:wallet'))
        assert resp.status_code == 200
        assert resp.json()['balance_coins'] == 0

    def test_topup_endpoint_creates_intent(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:wallet-topup'),
            data={'provider': 'payme', 'amount_uzs': 10000},
            content_type='application/json',
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body['coins_to_credit'] == 100
        assert 'checkout_url' in body

    def test_spend_402_when_insufficient(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:wallet-spend'),
            data={'coins': 100, 'description': 'AI diag'},
            content_type='application/json',
        )
        assert resp.status_code == 402  # Payment Required

    def test_spend_succeeds_with_balance(self, db, user, member):
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100)

        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:wallet-spend'),
            data={'coins': 30},
            content_type='application/json',
        )
        assert resp.status_code == 200
        assert resp.json()['balance_after_coins'] == 70


@pytest.mark.integration
class TestSubscriptionAPI:
    def test_list_active_plans(self, db, user, member, monthly_plan, lifetime_plan):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('commerce:sub-plans'))
        assert resp.status_code == 200
        assert len(resp.json()['plans']) == 2

    def test_subscribe_requires_org_admin(self, db, user, member, monthly_plan):
        # `member` fixture user'ni 'student' role bilan create qiladi (admin emas)
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('commerce:sub-subscribe'),
            data={'plan_slug': 'monthly-bronze', 'payment_provider': 'payme'},
            content_type='application/json',
        )
        assert resp.status_code == 403


@pytest.mark.integration
class TestAffiliateAPI:
    def test_code_endpoint(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('commerce:affiliate-code'))
        assert resp.status_code == 200
        assert len(resp.json()['code']) == 8

    def test_stats_endpoint(self, db, user, user2, member):
        code = affiliate_service.get_or_create_code(user)
        affiliate_service.claim_referral(code.code, user2)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('commerce:affiliate-stats'))
        assert resp.status_code == 200
        body = resp.json()
        assert body['total_referrals'] == 1
        assert body['total_coin_earned'] == int(Decimal('50'))  # default REFERRAL_COIN_BONUS=50


# ─── Concurrent spend (atomicity) ────────────────────────────────────────────


@pytest.mark.integration
class TestWalletAtomicity:
    def test_no_double_spend_on_concurrent_calls(self, db, user):
        """
        SELECT FOR UPDATE atomicity. Bu test'da realistic concurrency yo'q
        (single thread), lekin sequential SPEND'lar to'g'ri ishlashini
        tekshiradi (audit trail toza).
        """
        wallet = wallet_service.get_or_create_wallet(user)
        wallet_service.top_up(wallet, 100)

        wallet_service.spend(wallet, 60)
        with pytest.raises(wallet_service.InsufficientFunds):
            wallet_service.spend(wallet, 60)  # 40 < 60

        wallet.refresh_from_db()
        assert wallet.balance_coins == 40
