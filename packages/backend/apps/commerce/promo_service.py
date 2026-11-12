"""
Promo codes.

  - validate_promo(code, user, plan, base_amount, now=None) -> PromoQuote  (raises PromoError)
  - record_redemption(intent) -> PromoRedemption | None

Validation never mutates anything. A promo counts as used only when the payment is paid:
`record_redemption` is called by fulfillment, not by checkout.
"""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import PromoCode, PromoRedemption

logger = logging.getLogger(__name__)

MESSAGES = {
    'promo_not_found': 'Promo kod topilmadi.',
    'promo_inactive': 'Promo kod faol emas.',
    'promo_not_started': 'Promo kod hali kuchga kirmagan.',
    'promo_expired': 'Promo kodning muddati tugagan.',
    'promo_exhausted': 'Promo kod limiti tugagan.',
    'promo_already_used': 'Siz bu promo koddan allaqachon foydalangansiz.',
    'promo_plan_not_allowed': 'Promo kod bu reja uchun amal qilmaydi.',
    'promo_exam_type_not_allowed': 'Promo kod bu imtihon turi uchun amal qilmaydi.',
}


class PromoError(Exception):
    def __init__(self, code: str):
        self.code = code
        self.message = MESSAGES[code]
        super().__init__(self.message)


@dataclass
class PromoQuote:
    promo: PromoCode
    base_amount: Decimal
    discount: Decimal
    final_amount: Decimal


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def used_count(promo: PromoCode) -> int:
    return PromoRedemption.objects.filter(promo=promo).count()


def validate_promo(code: str, user, plan, base_amount: Decimal, *, now=None) -> PromoQuote:
    now = now or timezone.now()
    promo = PromoCode.objects.filter(code__iexact=(code or '').strip()).first()
    if promo is None:
        raise PromoError('promo_not_found')
    if not promo.is_active:
        raise PromoError('promo_inactive')
    if promo.starts_at and promo.starts_at > now:
        raise PromoError('promo_not_started')
    if promo.expires_at and promo.expires_at <= now:
        raise PromoError('promo_expired')
    if PromoRedemption.objects.filter(promo=promo, user=user).exists():
        raise PromoError('promo_already_used')
    if promo.max_uses is not None and used_count(promo) >= promo.max_uses:
        raise PromoError('promo_exhausted')
    allowed_plans = set(promo.allowed_plans.values_list('pk', flat=True))
    if allowed_plans and plan.pk not in allowed_plans:
        raise PromoError('promo_plan_not_allowed')
    allowed_types = set(promo.allowed_exam_types.values_list('pk', flat=True))
    if allowed_types and plan.exam_type_id not in allowed_types:
        raise PromoError('promo_exam_type_not_allowed')

    base = Decimal(base_amount)
    if promo.discount_type == PromoCode.DiscountType.PERCENT:
        discount = _money(base * promo.value / Decimal(100))
    else:
        discount = promo.value
    discount = min(discount, base)
    return PromoQuote(promo, base, discount, base - discount)


def record_redemption(intent) -> PromoRedemption | None:
    """Count the promo as used. Called only after the payment is paid. Idempotent."""
    code = (intent.metadata or {}).get('promo_code')
    if not code:
        return None
    promo = PromoCode.objects.select_for_update().filter(code__iexact=code).first()
    if promo is None:
        return None
    discount = Decimal((intent.metadata or {}).get('discount_uzs', '0'))
    try:
        with transaction.atomic():
            return PromoRedemption.objects.get_or_create(
                payment_intent=intent,
                defaults={'promo': promo, 'user': intent.user, 'discount_uzs': discount},
            )[0]
    except IntegrityError:
        # The same user paid two pending checkouts that both carried this code: the
        # money is already taken, so keep the order and just don't double-count the user.
        logger.warning(
            'promo %s: user %s already redeemed, intent %s', code, intent.user_id, intent.id
        )
        return None
