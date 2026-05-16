"""
Task 7 — Commerce Celery tasks.

  - auto_renew_subscriptions: har kun 03:00 da expired subscription'larni
    avtomat yangilash (auto_renew=True bo'lsa) yoki EXPIRED qilish.
"""

import logging

from celery import shared_task
from django.utils import timezone

from . import subscription_service
from .models import OrganizationSubscription

logger = logging.getLogger(__name__)


@shared_task(name='commerce.auto_renew_subscriptions')
def auto_renew_subscriptions() -> dict:
    """
    Beat schedule: har kun 03:00. current_period_ends_at o'tgan ACTIVE sub'larni:
      - auto_renew=True → renew(sub) (yangi period boshlanadi)
      - auto_renew=False → expire(sub) (EXPIRED status)

    Lifetime sub'lar (current_period_ends_at=NULL) ko'rilmaydi.
    """
    now = timezone.now()
    expired_qs = OrganizationSubscription.objects.filter(
        status__in=[
            OrganizationSubscription.Status.ACTIVE,
            OrganizationSubscription.Status.TRIALING,
        ],
        current_period_ends_at__isnull=False,
        current_period_ends_at__lt=now,
    )

    renewed = 0
    expired = 0
    for sub in expired_qs:
        try:
            if sub.auto_renew:
                # Production'da: PaymentIntent yaratish, charge initiate
                # Hozir: stub mode'da to'g'ridan-to'g'ri renew (charge fake)
                subscription_service.renew(sub)
                renewed += 1
            else:
                subscription_service.expire(sub)
                expired += 1
        except Exception as e:
            logger.warning('auto_renew_subscriptions: sub %s failed: %s', sub.id, e)

    logger.info(
        'auto_renew_subscriptions: %d renewed, %d expired (run %s)',
        renewed,
        expired,
        now,
    )
    return {'renewed': renewed, 'expired': expired}
