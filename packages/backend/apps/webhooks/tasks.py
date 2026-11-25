"""ISSUE-405 — Celery delivery task with exponential retry + DLQ integration."""

from __future__ import annotations

import logging
import random

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.utils import timezone

from core.tenant import unscoped_context

from .egress import BlockedDestination, EgressError, safe_post
from .models import WebhookDelivery
from .services import canonical_json, sign_payload

logger = logging.getLogger(__name__)

# Exponential backoff schedule: 1m, 5m, 30m, 2h, 12h (seconds).
# Aligned with @shared_task(max_retries=5) — index = self.request.retries.
RETRY_SCHEDULE_SECONDS = (60, 300, 1800, 7200, 43200)

# ±20% spread so endpoints that failed together (receiver outage) are not all retried
# in the same second when it comes back (audit F07).
RETRY_JITTER = 0.2

# 4xx answers that can change on their own; any other 4xx (bad signature, gone,
# not found) is the receiver rejecting this payload, so retrying only adds load.
RETRYABLE_4XX = frozenset({408, 409, 425, 429})

REQUEST_TIMEOUT_SECONDS = 10
# Only a short prefix of the receiver's answer is read and kept: enough to debug,
# too little to turn the delivery log into a reader for internal pages.
RESPONSE_BODY_MAX_BYTES = 1024
RESPONSE_BODY_MAX_CHARS = 512


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
        # one delivery by id, from a worker without a tenant context (B15: RLS applies)
        with unscoped_context():
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
        response = safe_post(
            endpoint.url,
            body=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_body_bytes=RESPONSE_BODY_MAX_BYTES,
        )
    except BlockedDestination as exc:
        # Permanent: retrying cannot make an internal address acceptable.
        logger.warning('deliver_webhook: delivery %s blocked: %s', delivery_id, exc)
        _record_attempt(
            delivery,
            status=WebhookDelivery.Status.EXHAUSTED,
            response_status=None,
            response_body=f'blocked: {exc}',
            completed=True,
        )
        return
    except EgressError as exc:
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

    if 400 <= response.status_code < 500 and response.status_code not in RETRYABLE_4XX:
        logger.warning(
            'deliver_webhook: delivery %s rejected with HTTP %s, not retried',
            delivery_id,
            response.status_code,
        )
        _record_attempt(
            delivery,
            status=WebhookDelivery.Status.EXHAUSTED,
            response_status=response.status_code,
            response_body=response_text,
            completed=True,
        )
        return

    # 5xx and retryable 4xx → retry path
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
        RuntimeError(f'HTTP {response.status_code} from webhook endpoint {endpoint.pk}'),
    )


def jittered(seconds: int) -> int:
    """``seconds`` spread by ±RETRY_JITTER, never below one second."""
    spread = seconds * RETRY_JITTER
    return max(1, round(seconds + random.uniform(-spread, spread)))  # noqa: S311 - not security


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
    base = RETRY_SCHEDULE_SECONDS[min(retries_so_far, len(RETRY_SCHEDULE_SECONDS) - 1)]
    countdown = jittered(base)
    try:
        raise task.retry(exc=exc, countdown=countdown)
    except MaxRetriesExceededError:
        # Race: max retries hit between check above and retry() — finalize.
        delivery.status = WebhookDelivery.Status.EXHAUSTED
        delivery.completed_at = timezone.now()
        delivery.save(update_fields=['status', 'completed_at'])
        raise exc
