"""
Task 9 — Omni-channel notifications (sequential priority fan-out).

Strategy:
  TG → Push → SMS

  - Telegram bot bo'yicha jo'natish urinish (user.telegram_id bor bo'lsa)
  - Muvaffaqiyatsiz/yo'q bo'lsa → Push notification (push_token bor bo'lsa)
  - Muvaffaqiyatsiz va priority='important'+'urgent' bo'lsa → SMS
  - In-app feed har doim yoziladi (status='sent' yoki priority bo'yicha)

Stub mode: providers actual call qilinmaydi, log + status='sent' qo'yiladi.
Production'da: python-telegram-bot (TG), Firebase/APNs (Push), PlayMobile (SMS).
"""

import logging
from datetime import datetime  # noqa: F401  # noqa: F401  for test mocking

from celery import shared_task
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)


def queue(
    user,
    *,
    title: str,
    body: str,
    priority: str = 'normal',
    metadata: dict | None = None,
) -> Notification:
    """
    Notification yaratib, async fan-out task'ni queue qiladi.
    In-app channel default — har doim user feed'ga yoziladi.
    """
    n = Notification.objects.create(
        user=user,
        channel=Notification.Channel.IN_APP,
        priority=priority,
        title=title,
        body=body,
        metadata=metadata or {},
        status=Notification.Status.PENDING,
    )
    fan_out_notification.delay(str(n.id))
    return n


@shared_task(name='engagement.fan_out_notification', bind=True)
def fan_out_notification(self, notification_id: str) -> dict:
    """
    Sequential fan-out:
      1. In-app (har doim, status='sent')
      2. Telegram (user.telegram_id bo'lsa)
      3. Push (TG fail va push_token bo'lsa) — placeholder
      4. SMS (TG/Push fail va priority IMPORTANT/URGENT) — placeholder
    """
    try:
        n = Notification.objects.select_related('user').get(pk=notification_id)
    except Notification.DoesNotExist:
        return {'status': 'not_found'}

    if n.status in (Notification.Status.SENT, Notification.Status.READ):
        return {'status': 'already_sent'}

    # In-app: har doim "sent"
    delivered = ['in_app']

    # Telegram
    if getattr(n.user, 'telegram_id', None):
        try:
            _send_telegram(n)
            delivered.append('telegram')
        except Exception as e:
            logger.warning('Telegram send failed for %s: %s', n.id, e)

    # Push (placeholder — push_token field mavjud emas hozircha)
    push_delivered = False  # Future: real push provider

    # SMS — faqat priority muhim va boshqa kanallar muvaffaqiyatsiz bo'lsa
    if (
        n.priority in (Notification.Priority.IMPORTANT, Notification.Priority.URGENT)
        and 'telegram' not in delivered
        and not push_delivered
        and getattr(n.user, 'phone_number', None)
    ):
        try:
            _send_sms(n)
            delivered.append('sms')
        except Exception as e:
            logger.warning('SMS send failed for %s: %s', n.id, e)

    n.delivery_attempts += 1
    n.status = Notification.Status.SENT
    n.sent_at = timezone.now()
    n.save(update_fields=['delivery_attempts', 'status', 'sent_at'])

    return {'status': 'sent', 'channels': delivered}


# ── Provider stubs ────────────────────────────────────────────────────────────


def _send_telegram(n: Notification) -> None:
    """Stub: log only. Production: python-telegram-bot bilan jo'natish."""
    from django.conf import settings

    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token or 'test' in token.lower():
        logger.info('[STUB Telegram] %s → %s: %s', n.user, n.title, n.body[:80])
        return

    # TODO production:
    # from telegram import Bot
    # bot = Bot(token=token)
    # bot.send_message(chat_id=n.user.telegram_id, text=f'{n.title}\n\n{n.body}')
    raise NotImplementedError('Real Telegram bot integratsiyasi kelajakda')


def _send_sms(n: Notification) -> None:
    """Stub: log only. Production: PlayMobile yoki Eskiz API."""
    from django.conf import settings

    backend = getattr(settings, 'SMS_BACKEND', 'console')
    if backend in ('console', 'dummy'):
        logger.info('[STUB SMS] %s → %s: %s', n.user.phone_number, n.title, n.body[:60])
        return

    raise NotImplementedError('Real SMS integratsiyasi kelajakda')
