"""ISSUE-113 + ISSUE-114 — accounts background tasks.

- cleanup_expired_sessions          — django_session jadvalini tozalash (>14 kun)
- cleanup_used_otp_codes            — OTPCode is_verified=True yoki 24h expired
- cleanup_old_webhook_deliveries    — webhooks_webhookdelivery sent + 90+ kun
- check_sms_balance                 — PlayMobile + Eskiz balance poller + Prometheus
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.contrib.sessions.models import Session
from django.utils import timezone

from core.locks import single_runner_lock
from core.tenant import unscoped_context

logger = logging.getLogger(__name__)


@shared_task(name='accounts.cleanup_expired_sessions')
def cleanup_expired_sessions(days: int = 14) -> dict:
    """Django session jadvalidan eskirgan yozuvlarni o'chiradi.

    Django o'z-o'zidan `clearsessions` management command beradi, lekin Celery beat
    orqali periodic ishlatish uchun task'ga o'raymiz. `days` parametri: shu vaqtdan
    oldin yaratilgan + expired sessionlarni o'chiradi.
    """
    with single_runner_lock('accounts.cleanup_expired_sessions', expire=1800) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = Session.objects.filter(expire_date__lt=cutoff).delete()
        logger.info('cleanup_expired_sessions: deleted %d expired sessions', deleted)
        return {'deleted': deleted}


@shared_task(name='accounts.cleanup_used_otp_codes')
def cleanup_used_otp_codes(grace_hours: int = 24) -> dict:
    """OTPCode'ni tozalaydi:
      * `is_verified=True` (foydalanilgan)
      * yoki `expires_at < now - grace_hours` (eskirgan, lekin verify qilinmagan).
    Grace window dispute uchun (foydalanuvchi 1 soatda shikoyat qilishi mumkin).
    """
    from .models import OTPCode

    with single_runner_lock('accounts.cleanup_used_otp_codes', expire=1800) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        cutoff = timezone.now() - timedelta(hours=grace_hours)
        qs = OTPCode.objects.filter(is_verified=True) | OTPCode.objects.filter(
            expires_at__lt=cutoff
        )
        deleted, _ = qs.delete()
        logger.info('cleanup_used_otp_codes: deleted %d OTP codes', deleted)
        return {'deleted': deleted}


@shared_task(name='accounts.cleanup_old_webhook_deliveries')
def cleanup_old_webhook_deliveries(days: int = 90) -> dict:
    """Eski webhook delivery yozuvlarini o'chiradi (terminal: sent/exhausted).

    `pending`/`failed` saqlanadi (hali retry'ga loyiq). Terminal status + 90+ kun → o'chir.
    """
    from apps.webhooks.models import WebhookDelivery

    with single_runner_lock('accounts.cleanup_old_webhook_deliveries', expire=3600) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        cutoff = timezone.now() - timedelta(days=days)
        with unscoped_context():
            deleted, _ = WebhookDelivery.objects.filter(
                status__in=[
                    WebhookDelivery.Status.SENT,
                    WebhookDelivery.Status.EXHAUSTED,
                ],
                completed_at__lt=cutoff,
            ).delete()
        logger.info(
            'cleanup_old_webhook_deliveries: deleted %d terminal webhook deliveries',
            deleted,
        )
        return {'deleted': deleted}


@shared_task(name='accounts.check_sms_balance')
def check_sms_balance() -> dict:
    """SMS provider balansini tekshiradi va Prometheus gauge'ga yozadi.

    Threshold pastida bo'lsa logger.error — Grafana alert orqali on-call ogohlantiriladi.
    """
    from .sms_balance import poll_sms_balances

    with single_runner_lock('accounts.check_sms_balance', expire=300) as acquired:
        if not acquired:
            return {'status': 'skipped_lock_busy'}
        return poll_sms_balances()
