"""
Unit tests for apps/commerce/subscription_service.py

Focus: subscribe/activate/cancel/renew/expire lifecycle + Lifetime edge cases
+ active subscription lookup. DB is real; Redis mocked.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.commerce import subscription_service
from apps.commerce.models import OrganizationSubscription, SubscriptionPlan
from core.tenant import tenant_context

# ── Plan fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def monthly_plan(db):
    return SubscriptionPlan.objects.create(
        name='Monthly Basic',
        slug='monthly-basic',
        billing_period=SubscriptionPlan.BillingPeriod.MONTHLY,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('100000'),
    )


@pytest.fixture
def yearly_plan(db):
    return SubscriptionPlan.objects.create(
        name='Yearly Pro',
        slug='yearly-pro',
        billing_period=SubscriptionPlan.BillingPeriod.YEARLY,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('1000000'),
    )


@pytest.fixture
def lifetime_plan(db):
    return SubscriptionPlan.objects.create(
        name='Lifetime',
        slug='lifetime',
        billing_period=SubscriptionPlan.BillingPeriod.LIFETIME,
        pricing_model=SubscriptionPlan.PricingModel.FLAT,
        price_uzs=Decimal('5000000'),
    )


@pytest.mark.unit
class TestSubscribe:
    """subscribe — yangi sub TRIALING status bilan yaratadi."""

    def test_subscribe_creates_trialing_subscription(self, db, org, monthly_plan):
        """Yangi sub TRIALING bo'lib yaratiladi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        assert sub.organization_id == org.id
        assert sub.plan_id == monthly_plan.id
        assert sub.status == OrganizationSubscription.Status.TRIALING

    def test_subscribe_monthly_sets_30_day_period(self, db, org, monthly_plan):
        """Monthly plan → period 30 kun."""
        sub = subscription_service.subscribe(org, monthly_plan)
        delta = sub.current_period_ends_at - sub.current_period_started_at
        assert 29 <= delta.days <= 31

    def test_subscribe_yearly_sets_365_day_period(self, db, org, yearly_plan):
        """Yearly plan → period 365 kun."""
        sub = subscription_service.subscribe(org, yearly_plan)
        delta = sub.current_period_ends_at - sub.current_period_started_at
        assert 364 <= delta.days <= 366

    def test_subscribe_lifetime_no_end_date(self, db, org, lifetime_plan):
        """Lifetime plan → current_period_ends_at = None."""
        sub = subscription_service.subscribe(org, lifetime_plan)
        assert sub.current_period_ends_at is None

    def test_subscribe_with_payment_intent(self, db, org, user, monthly_plan):
        """payment_intent FK saqlanadi."""
        from apps.commerce.models import PaymentIntent

        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.STUB,
            amount_uzs=Decimal('100000'),
            coins_to_credit=0,
        )
        sub = subscription_service.subscribe(org, monthly_plan, payment_intent=intent)
        assert sub.last_payment_intent_id == intent.id


@pytest.mark.unit
class TestActivate:
    """activate — TRIALING → ACTIVE."""

    def test_activate_changes_status(self, db, org, monthly_plan):
        """activate() status'ni ACTIVE qiladi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.activate(sub)
        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.ACTIVE


@pytest.mark.unit
class TestCancel:
    """cancel — har holatdan CANCELLED + cancelled_at."""

    def test_cancel_sets_status_and_timestamp(self, db, org, monthly_plan):
        """cancel() status + cancelled_at + auto_renew=False."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.cancel(sub, reason='User requested')
        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.CANCELLED
        assert sub.cancelled_at is not None
        assert sub.cancellation_reason == 'User requested'
        assert sub.auto_renew is False

    def test_cancel_without_reason(self, db, org, monthly_plan):
        """reason ixtiyoriy — default bo'sh string."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.cancel(sub)
        sub.refresh_from_db()
        assert sub.cancellation_reason == ''


@pytest.mark.unit
class TestRenew:
    """renew — period extend + status=ACTIVE."""

    def test_renew_extends_period(self, db, org, monthly_plan):
        """Renew → yangi 30 kun period."""
        sub = subscription_service.subscribe(org, monthly_plan)
        old_start = sub.current_period_started_at
        subscription_service.renew(sub)
        sub.refresh_from_db()
        assert sub.current_period_started_at >= old_start
        assert sub.status == OrganizationSubscription.Status.ACTIVE

    def test_renew_lifetime_is_noop(self, db, org, lifetime_plan):
        """Lifetime → renew no-op (period_ends_at None qoladi)."""
        sub = subscription_service.subscribe(org, lifetime_plan)
        result = subscription_service.renew(sub)
        assert result.current_period_ends_at is None
        # Status TRIALING qoladi (saqlanmaydi)
        sub.refresh_from_db()
        assert sub.current_period_ends_at is None

    def test_renew_yearly_extends_365_days(self, db, org, yearly_plan):
        """Yearly renew → 365 kun period."""
        sub = subscription_service.subscribe(org, yearly_plan)
        subscription_service.renew(sub)
        sub.refresh_from_db()
        delta = sub.current_period_ends_at - sub.current_period_started_at
        assert 364 <= delta.days <= 366


@pytest.mark.unit
class TestExpire:
    """expire — period tugaganda EXPIRED."""

    def test_expire_changes_status(self, db, org, monthly_plan):
        """expire() → EXPIRED."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.expire(sub)
        sub.refresh_from_db()
        assert sub.status == OrganizationSubscription.Status.EXPIRED


@pytest.mark.unit
class TestGetActiveSubscription:
    """get_active_subscription + has_active_subscription."""

    def test_get_active_returns_trialing(self, db, org, monthly_plan):
        """TRIALING sub ham 'active' deb hisoblanadi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        found = subscription_service.get_active_subscription(org)
        assert found is not None
        assert found.id == sub.id

    def test_get_active_returns_active(self, db, org, monthly_plan):
        """ACTIVE sub topiladi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.activate(sub)
        found = subscription_service.get_active_subscription(org)
        assert found is not None
        assert found.status == OrganizationSubscription.Status.ACTIVE

    def test_get_active_ignores_cancelled(self, db, org, monthly_plan):
        """CANCELLED sub topilmaydi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.cancel(sub)
        found = subscription_service.get_active_subscription(org)
        assert found is None

    def test_get_active_ignores_expired(self, db, org, monthly_plan):
        """EXPIRED sub topilmaydi."""
        sub = subscription_service.subscribe(org, monthly_plan)
        subscription_service.expire(sub)
        found = subscription_service.get_active_subscription(org)
        assert found is None

    def test_get_active_returns_none_for_no_sub(self, db, org):
        """Hech qachon subscribe qilinmagan org → None."""
        found = subscription_service.get_active_subscription(org)
        assert found is None

    def test_has_active_true_for_trialing(self, db, org, monthly_plan):
        """TRIALING + future end date → has_active=True."""
        subscription_service.subscribe(org, monthly_plan)
        assert subscription_service.has_active_subscription(org) is True

    def test_has_active_true_for_lifetime(self, db, org, lifetime_plan):
        """Lifetime sub → har doim has_active=True."""
        subscription_service.subscribe(org, lifetime_plan)
        assert subscription_service.has_active_subscription(org) is True

    def test_has_active_false_for_no_sub(self, db, org):
        """Hech qanday sub yo'q → False."""
        assert subscription_service.has_active_subscription(org) is False

    def test_has_active_false_for_expired_period(self, db, org, monthly_plan):
        """Period tugagan, lekin status hali ACTIVE → False."""
        sub = subscription_service.subscribe(org, monthly_plan)
        # Manually set period to past
        sub.current_period_ends_at = timezone.now() - timedelta(days=1)
        sub.save(update_fields=['current_period_ends_at'])
        assert subscription_service.has_active_subscription(org) is False

    def test_get_active_isolated_per_org(self, db, org, org2, monthly_plan):
        """Subs per-org alohida — org2 sub'i org'da ko'rinmaydi."""
        subscription_service.subscribe(org2, monthly_plan)
        found = subscription_service.get_active_subscription(org)
        assert found is None
