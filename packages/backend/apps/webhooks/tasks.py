"""ISSUE-405 — Celery delivery task with exponential retry + DLQ integration."""

from __future__ import annotations

import logging

import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from .models import WebhookDelivery
from .services import canonical_json, sign_payload

logger = logging.getLogger(__name__)

# Exponential backoff schedule: 1m, 5m, 30m, 2h, 12h (seconds).
# Aligned with @shared_task(max_retries=5) — index = self.request.retries.
RETRY_SCHEDULE_SECONDS = (60, 300, 1800, 7200, 43200)

REQUEST_TIMEOUT_SECONDS = 10
RESPONSE_BODY_MAX_CHARS = 2000


def _record_attempt(
    delivery: WebhookDelivery,
    *,
    status: str,
    response_status: int | None,
    response_body: str,
    completed: bool,
) -> None:
    """Persist attempt result on the delivery row."""
    delivery.attempts = (delivery.attempts or 0) + 1
    delivery.status = status
    delivery.response_status = response_status
    delivery.response_body = (response_body or '')[:RESPONSE_BODY_MAX_CHARS]
    delivery.last_attempted_at = timezone.now()
    if completed:
        delivery.completed_at = timezone.now()
    delivery.save(
        update_fields=[
            'attempts',
            'status',
            'response_status',
            'response_body',
            'last_attempted_at',
            'completed_at',
        ]
    )


@shared_task(bind=True, max_retries=5, default_retry_delay=60, acks_late=True)
def deliver_webhook(self, delivery_id: str):
    """POST signed payload to endpoint.url.

    Retry on 4xx/5xx/network with exponential schedule. After max_retries:
    mark EXHAUSTED + raise → Celery `task_failure` signal → DLQ (ISSUE-308).
    """
    try:
        delivery = WebhookDelivery.objects.select_related('endpoint').get(pk=delivery_id)
    except WebhookDelivery.DoesNotExist:
        logger.warning('deliver_webhook: delivery %s not found', delivery_id)
        return

    endpoint = delivery.endpoint
    body = canonical_json(delivery.payload)
    signature = sign_payload(body, endpoint.secret)

    headers = {
        'Content-Type': 'application/json',
        'X-Webhook-Signature': f'sha256={signature}',
        'X-Webhook-Event': delivery.event,
        'X-Webhook-Delivery': str(delivery.id),
    }

    try:
        response = requests.post(
            endpoint.url,
            data=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning('deliver_webhook: network error for delivery %s: %s', delivery_id, exc)
        _record_attempt(
            delivery,
            status=WebhookDelivery.Status.FAILED,
            response_status=None,
            response_body=str(exc),
            completed=False,
        )
        _retry_or_exhaust(self, delivery, exc)
        return

    response_text = response.text or ''
    if 200 <= response.status_code < 300:
        _record_attempt(
            delivery,
            status=WebhookDelivery.Status.SENT,
            response_status=response.status_code,
            response_body=response_text,
            completed=True,
        )
        return

    # 4xx / 5xx → retry path
    _record_attempt(
        delivery,
        status=WebhookDelivery.Status.FAILED,
        response_status=response.status_code,
        response_body=response_text,
        completed=False,
    )
    _retry_or_exhaust(
        self,
        delivery,
        RuntimeError(f'HTTP {response.status_code} from {endpoint.url}'),
    )


def _retry_or_exhaust(task, delivery: WebhookDelivery, exc: Exception) -> None:
    """Schedule next retry with exponential backoff or mark EXHAUSTED + raise.

    `task_failure` Celery signal (ISSUE-308 DLQ) fires automatically when the
    task raises after final retry — we re-raise here to trigger it.
    """
    retries_so_far = task.request.retries  # 0-indexed: 0 on first call
    if retries_so_far >= task.max_retries:
        # Final attempt failed. Mark terminal + raise so task_failure → DLQ.
        delivery.status = WebhookDelivery.Status.EXHAUSTED
        delivery.completed_at = timezone.now()
        delivery.save(update_fields=['status', 'completed_at'])
        logger.error(
            'deliver_webhook: delivery %s exhausted after %s attempts',
            delivery.id,
            delivery.attempts,
        )
        raise exc

    # retries_so_far is the count BEFORE this retry — next attempt index.
    countdown = RETRY_SCHEDULE_SECONDS[min(retries_so_far, len(RETRY_SCHEDULE_SECONDS) - 1)]
    try:
        raise task.retry(exc=exc, countdown=countdown)
    except MaxRetriesExceededError:
        # Race: max retries hit between check above and retry() — finalize.
        delivery.status = WebhookDelivery.Status.EXHAUSTED
        delivery.completed_at = timezone.now()
        delivery.save(update_fields=['status', 'completed_at'])
        raise exc
