"""
Subscription service — org subscription lifecycle.

API:
  - subscribe(org, plan, *, payment_intent=None) → OrganizationSubscription (TRIALING)
  - activate(sub) — payment muvaffaqiyatli → ACTIVE + period dates set
  - cancel(sub, reason='') → CANCELLED + cancelled_at
  - renew(sub) → period extended (Celery auto-renew tomonidan chaqiriladi)
  - expire(sub) → EXPIRED (period_ends_at < now va auto_renew=False)
  - get_active_subscription(org) → joriy ACTIVE sub yoki None

Lifetime plan: current_period_ends_at = None, hech qachon expire bo'lmaydi.
"""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from core.tenant import unscoped_context

from .models import OrganizationSubscription, SubscriptionPlan


def _next_period_end(plan: SubscriptionPlan, *, from_dt: datetime) -> datetime | None:
    """Plan billing_period bo'yicha keyingi period oxiri."""
    if plan.billing_period == SubscriptionPlan.BillingPeriod.LIFETIME:
        return None
    if plan.billing_period == SubscriptionPlan.BillingPeriod.MONTHLY:
        return from_dt + timedelta(days=30)
    if plan.billing_period == SubscriptionPlan.BillingPeriod.YEARLY:
        return from_dt + timedelta(days=365)
    raise ValueError(f'Unknown billing_period: {plan.billing_period}')


@transaction.atomic
def subscribe(org, plan: SubscriptionPlan, *, payment_intent=None) -> OrganizationSubscription:
    """
    Org'ga yangi subscription yaratish (TRIALING). Payment intent berilgan bo'lsa,
    activate() shu zahoti chaqirilishi mumkin (webhook orqali ham mumkin).
    """
    now = timezone.now()
    sub = OrganizationSubscription.objects.create(
        organization=org,
        plan=plan,
        status=OrganizationSubscription.Status.TRIALING,
        current_period_started_at=now,
        current_period_ends_at=_next_period_end(plan, from_dt=now),
        last_payment_intent=payment_intent,
    )
    return sub


def activate(sub: OrganizationSubscription) -> OrganizationSubscription:
    """Payment muvaffaqiyatli bo'lgandan keyin chaqiriladi."""
    sub.status = OrganizationSubscription.Status.ACTIVE
    sub.save(update_fields=['status', 'updated_at'])
    return sub


def cancel(sub: OrganizationSubscription, *, reason: str = '') -> OrganizationSubscription:
    sub.status = OrganizationSubscription.Status.CANCELLED
    sub.cancelled_at = timezone.now()
    sub.cancellation_reason = reason
    sub.auto_renew = False
    sub.save(
        update_fields=['status', 'cancelled_at', 'cancellation_reason', 'auto_renew', 'updated_at']
    )
    return sub


@transaction.atomic
def renew(sub: OrganizationSubscription) -> OrganizationSubscription:
    """
    Auto-renew Celery task tomonidan chaqiriladi. Lifetime plan uchun no-op.
    """
    if sub.plan.billing_period == SubscriptionPlan.BillingPeriod.LIFETIME:
        return sub
    now = timezone.now()
    sub.current_period_started_at = now
    sub.current_period_ends_at = _next_period_end(sub.plan, from_dt=now)
    sub.status = OrganizationSubscription.Status.ACTIVE
    sub.save(
        update_fields=[
            'current_period_started_at',
            'current_period_ends_at',
            'status',
            'updated_at',
        ]
    )
    return sub


def expire(sub: OrganizationSubscription) -> OrganizationSubscription:
    sub.status = OrganizationSubscription.Status.EXPIRED
    sub.save(update_fields=['status', 'updated_at'])
    return sub


def get_active_subscription(org) -> OrganizationSubscription | None:
    # ISSUE-110 W1: TenantManager qo'shilgandan keyin tenant context'siz callers
    # (Celery beat, webhook view) explicit `unscoped_context()` ichida o'qiydi.
    # Explicit `organization=org` filter cross-tenant leak yo'qligini ta'minlaydi.
    with unscoped_context():
        return (
            OrganizationSubscription.objects.filter(
                organization=org,
                status__in=[
                    OrganizationSubscription.Status.ACTIVE,
                    OrganizationSubscription.Status.TRIALING,
                ],
            )
            .order_by('-created_at')
            .first()
        )


def has_active_subscription(org) -> bool:
    sub = get_active_subscription(org)
    if sub is None:
        return False
    if sub.current_period_ends_at is None:
        # Lifetime
        return True
    return sub.current_period_ends_at > timezone.now()
