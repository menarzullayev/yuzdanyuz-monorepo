"""ISSUE-409 — AuditUserMixin tests.

`created_by` insert paytida, `updated_by` har save'da set qilinadi.
ContextVar'da user yo'q bo'lsa — FK NULL qoldiriladi.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.commerce.models import (
    OrganizationSubscription,
    SubscriptionPlan,
    Wallet,
    WalletTransaction,
)
from core.audit_user import audit_user_context, set_current_user

User = get_user_model()


@pytest.fixture
def plan(db):
    return SubscriptionPlan.objects.create(
        name='Basic',
        slug='basic',
        billing_period=SubscriptionPlan.BillingPeriod.MONTHLY,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('100000'),
    )


@pytest.fixture
def wallet(db, user):
    return Wallet.objects.create(user=user)


@pytest.mark.unit
class TestAuditUserMixinInsert:
    def test_no_context_leaves_created_by_null(self, db, wallet):
        tx = WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransaction.Kind.TOPUP,
            coins_delta=100,
            balance_after_coins=100,
        )
        assert tx.created_by_id is None
        assert tx.updated_by_id is None

    def test_explicit_context_sets_both(self, db, wallet, user):
        with audit_user_context(user):
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.TOPUP,
                coins_delta=100,
                balance_after_coins=100,
            )
        assert tx.created_by_id == user.id
        assert tx.updated_by_id == user.id

    def test_middleware_set_user_picked_up(self, db, wallet, user):
        # Simulate middleware behavior
        set_current_user(user)
        try:
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.TOPUP,
                coins_delta=100,
                balance_after_coins=100,
            )
        finally:
            set_current_user(None)
        assert tx.created_by_id == user.id


@pytest.mark.unit
class TestAuditUserMixinUpdate:
    def test_update_changes_updated_by_only(self, db, wallet, user, user2):
        with audit_user_context(user):
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.TOPUP,
                coins_delta=100,
                balance_after_coins=100,
            )
        assert tx.created_by_id == user.id

        with audit_user_context(user2):
            tx.coins_delta = 200
            tx.save()
        tx.refresh_from_db()
        assert tx.created_by_id == user.id  # immutable
        assert tx.updated_by_id == user2.id  # changed

    def test_update_fields_save_includes_audit(self, db, wallet, user, user2):
        with audit_user_context(user):
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.TOPUP,
                coins_delta=100,
                balance_after_coins=100,
            )
        # save(update_fields=...) chaqirilsa ham updated_by qo'shiladi
        with audit_user_context(user2):
            tx.coins_delta = 999
            tx.save(update_fields=['coins_delta'])
        tx.refresh_from_db()
        assert tx.coins_delta == 999
        assert tx.updated_by_id == user2.id

    def test_anonymous_user_does_not_overwrite_created_by(self, db, wallet, user):
        with audit_user_context(user):
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                kind=WalletTransaction.Kind.TOPUP,
                coins_delta=100,
                balance_after_coins=100,
            )
        # Anonymous re-save shouldn't touch created_by/updated_by
        tx.coins_delta = 50
        tx.save()
        tx.refresh_from_db()
        assert tx.created_by_id == user.id
        # updated_by also unchanged (no user in context)
        assert tx.updated_by_id == user.id


@pytest.mark.unit
class TestAuditUserMixinOrganizationSubscription:
    def test_org_subscription_audit(self, db, org, plan, user):
        from django.utils import timezone

        with audit_user_context(user):
            sub = OrganizationSubscription.objects.create(
                organization=org,
                plan=plan,
                current_period_started_at=timezone.now(),
            )
        assert sub.created_by_id == user.id
        assert sub.updated_by_id == user.id

    def test_transition_method_preserves_audit_chain(self, db, org, plan, user, user2):
        from django.utils import timezone

        with audit_user_context(user):
            sub = OrganizationSubscription.objects.create(
                organization=org,
                plan=plan,
                current_period_started_at=timezone.now(),
            )
        with audit_user_context(user2):
            sub.activate()
        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.ACTIVE
        assert sub.created_by_id == user.id
        assert sub.updated_by_id == user2.id
