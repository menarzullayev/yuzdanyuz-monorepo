"""ISSUE-405 — Webhook dispatch + signing services."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from core.tenant import get_current_org, unscoped_context

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)


def sign_payload(payload: bytes, secret: str) -> str:
    """Return HMAC-SHA256 hex digest of `payload` keyed by `secret`.

    Caller is responsible for serializing `payload` to bytes (canonical JSON
    encoding is the convention — use `json.dumps(..., sort_keys=True, separators=(',', ':'))`).
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError('sign_payload requires bytes; serialize JSON before calling')
    return hmac.new(secret.encode('utf-8'), payload, hashlib.sha256).hexdigest()


def canonical_json(payload: dict[str, Any]) -> bytes:
    """Stable JSON serialization for HMAC signing — sorted keys, compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')


def dispatch_webhook(
    event: str,
    payload: dict[str, Any],
    org=None,
) -> list[WebhookDelivery]:
    """Find subscribed active endpoints for `org` and queue delivery tasks.

    Returns the list of newly-created WebhookDelivery rows (mostly for tests).
    No-op (empty list) if no matching endpoints exist.

    Tenant resolution: `org` arg wins; falls back to `get_current_org()`.
    Look-up bypasses TenantManager (we may be called from a Celery signal where
    no tenant context is set — endpoint look-up filters explicitly by org).
    """
    organization = org if org is not None else get_current_org()
    if organization is None:
        logger.debug('dispatch_webhook(%s): no organization in context — skipped', event)
        return []

    # Bypass TenantManager since dispatch can run from signal/Celery context
    # without an active tenant. Explicit filter on organization keeps isolation.
    with unscoped_context():
        endpoints = list(
            WebhookEndpoint.global_objects.filter(
                organization=organization,
                is_active=True,
            )
        )

    matching = [ep for ep in endpoints if ep.subscribes_to(event)]
    if not matching:
        return []

    # Lazy import — services.py is imported by signals at app-ready time, but
    # tasks.py imports models which import services would cause a cycle.
    from .tasks import deliver_webhook

    deliveries: list[WebhookDelivery] = []
    for endpoint in matching:
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint,
            event=event,
            payload=payload,
            status=WebhookDelivery.Status.PENDING,
        )
        deliveries.append(delivery)
        # Eager mode (tests) executes immediately; prod queues via Redis.
        deliver_webhook.delay(str(delivery.id))

    return deliveries
