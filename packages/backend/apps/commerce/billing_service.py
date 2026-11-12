"""
Checkout and fulfillment for the monetisation products.

Checkout creates a PaymentIntent whose metadata carries `billing_kind`:
  - user_plan : B2C tariff or one-time pass for `target_subscription_plan`
  - org_keys  : an organization buys `quantity` keys (unit price = the exam_key plan
                of that exam type)
Nothing is granted at checkout. `fulfil_paid_intent` is called by the payment webhook
after the intent is marked SUCCEEDED (or immediately for a fully discounted order).
"""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import key_service, payment_providers, promo_service
from .models import ExamKeyBatch, PaymentIntent, SubscriptionPlan, UserSubscription

PERIOD_DAYS = 30  # same convention as subscription_service._next_period_end (monthly)

KIND_USER_PLAN = 'user_plan'
KIND_ORG_KEYS = 'org_keys'


class CheckoutError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class PaymentsUnavailable(Exception):
    pass


def purchasable_plan_or_error(plan: SubscriptionPlan) -> None:
    if plan.audience != SubscriptionPlan.Audience.STUDENT or not plan.is_active:
        raise CheckoutError('plan_not_available', 'Reja mavjud emas.', 404)
    if plan.exam_type_id and not plan.exam_type.is_enabled:
        raise CheckoutError('exam_type_disabled', 'Bu imtihon turi hozircha mavjud emas.', 404)


def _start_payment(*, user, provider_name, amount, plan, org, metadata):
    """Create the intent and start the provider charge. Zero amount skips the provider."""
    if amount <= 0:
        intent = PaymentIntent.objects.create(
            user=user,
            provider=PaymentIntent.Provider.STUB,
            status=PaymentIntent.Status.SUCCEEDED,
            amount_uzs=Decimal('0.00'),
            coins_to_credit=0,
            target_subscription_plan=plan,
            target_organization=org,
            metadata=metadata,
        )
        fulfil_paid_intent(intent)
        return intent, None
    if provider_name not in ('payme', 'click'):
        raise CheckoutError('invalid_provider', "'payme' yoki 'click'")
    if not payment_providers.payments_available():
        raise PaymentsUnavailable()
    provider = payment_providers.get_provider(provider_name)
    try:
        with transaction.atomic():
            intent = PaymentIntent.objects.create(
                user=user,
                provider=provider_name,
                amount_uzs=amount,
                coins_to_credit=0,
                target_subscription_plan=plan,
                target_organization=org,
                metadata=metadata,
            )
            result = provider.initiate_charge(intent)
    except NotImplementedError:
        raise PaymentsUnavailable() from None
    return intent, result['checkout_url']


def _apply_promo(promo_code, user, plan, base_amount):
    metadata, amount = {}, base_amount
    if promo_code:
        try:
            quote = promo_service.validate_promo(promo_code, user, plan, base_amount)
        except promo_service.PromoError as e:
            raise CheckoutError(e.code, e.message) from None
        metadata = {'promo_code': quote.promo.code, 'discount_uzs': str(quote.discount)}
        amount = quote.final_amount
    return metadata, amount


def checkout_user_plan(user, plan, provider_name, promo_code=''):
    purchasable_plan_or_error(plan)
    promo_meta, amount = _apply_promo(promo_code, user, plan, plan.price_uzs)
    metadata = {'billing_kind': KIND_USER_PLAN, 'plan_id': str(plan.id), **promo_meta}
    intent, url = _start_payment(
        user=user,
        provider_name=provider_name,
        amount=amount,
        plan=plan,
        org=None,
        metadata=metadata,
    )
    return intent, url


def checkout_org_keys(user, org, exam_type, quantity, provider_name, promo_code=''):
    if not (1 <= quantity <= key_service.MAX_KEYS_PER_GRANT):
        raise CheckoutError('invalid_quantity', "Kalit soni noto'g'ri.")
    if not exam_type.is_enabled:
        raise CheckoutError('exam_type_disabled', 'Bu imtihon turi hozircha mavjud emas.', 404)
    plan = SubscriptionPlan.objects.filter(
        audience='student',
        access_mode=SubscriptionPlan.AccessMode.EXAM_KEY,
        exam_type=exam_type,
        is_active=True,
    ).first()
    if plan is None:
        raise CheckoutError('plan_not_available', 'Bu tur uchun kalit narxi belgilanmagan.', 404)
    base = plan.price_uzs * quantity
    promo_meta, amount = _apply_promo(promo_code, user, plan, base)
    metadata = {
        'billing_kind': KIND_ORG_KEYS,
        'plan_id': str(plan.id),
        'exam_type': exam_type.code,
        'quantity': quantity,
        **promo_meta,
    }
    return _start_payment(
        user=user, provider_name=provider_name, amount=amount, plan=plan, org=org, metadata=metadata
    )


@transaction.atomic
def fulfil_paid_intent(intent: PaymentIntent) -> None:
    """Activate what the paid intent bought. Idempotent per intent."""
    meta = intent.metadata or {}
    kind = meta.get('billing_kind')
    if not kind or meta.get('fulfilled'):
        return
    plan = intent.target_subscription_plan
    if kind == KIND_USER_PLAN:
        _grant_user_plan(intent.user, plan, intent)
    elif kind == KIND_ORG_KEYS:
        if ExamKeyBatch.global_objects.filter(payment_intent=intent).exists():
            return
        key_service.grant_keys(
            intent.target_organization,
            plan.exam_type,
            int(meta['quantity']),
            source=ExamKeyBatch.Source.PURCHASE,
            payment_intent=intent,
            granted_by=intent.user,
        )
    else:
        return
    promo_service.record_redemption(intent)
    intent.metadata = {**meta, 'fulfilled': True}
    intent.save(update_fields=['metadata', 'updated_at'])


def _grant_user_plan(user, plan: SubscriptionPlan, intent) -> UserSubscription:
    now = timezone.now()
    if plan.access_mode == SubscriptionPlan.AccessMode.EXAM_KEY:
        return UserSubscription.objects.create(
            user=user,
            plan=plan,
            started_at=now,
            attempts_remaining=plan.attempts_included,
            last_payment_intent=intent,
        )
    existing = (
        UserSubscription.objects.select_for_update()
        .filter(
            user=user,
            plan=plan,
            status=UserSubscription.Status.ACTIVE,
            current_period_ends_at__gt=now,
        )
        .first()
    )
    if existing is not None:  # renewing early stacks on top of the remaining time
        existing.current_period_ends_at += timedelta(days=PERIOD_DAYS)
        existing.last_payment_intent = intent
        existing.save(update_fields=['current_period_ends_at', 'last_payment_intent', 'updated_at'])
        return existing
    return UserSubscription.objects.create(
        user=user,
        plan=plan,
        started_at=now,
        current_period_ends_at=now + timedelta(days=PERIOD_DAYS),
        last_payment_intent=intent,
    )
