"""ISSUE-405 — WebhookEndpoint + WebhookDelivery models.

Architecture:
  WebhookEndpoint  — tenant-scoped config row (URL + secret + event subscriptions).
  WebhookDelivery  — one row per dispatched event (status, attempts, response).

Delivery rows are NOT tenant-scoped via TenantTimestampMixin — they hang off
WebhookEndpoint (which IS tenant-scoped), keeping the FK chain authoritative
without duplicating organization_id columns.
"""

from __future__ import annotations

import secrets
import uuid

from django.db import models

from core.mixins import TenantTimestampMixin


def _generate_secret() -> str:
    """64-char URL-safe HMAC secret (default for new endpoints)."""
    return secrets.token_urlsafe(48)[:64]


class WebhookEndpoint(TenantTimestampMixin):
    """Enterprise-registered outgoing webhook target.

    `secret` is the HMAC-SHA256 key — displayed ONCE on create, never exposed
    via API afterwards. Plaintext storage is intentional (admin needs to
    re-display on rotation request); future hardening: encrypt-at-rest.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=64, default=_generate_secret)
    # List of event names this endpoint subscribes to, e.g.
    #   ["exam.submitted", "payment.completed"]
    events = models.JSONField(default=list)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        verbose_name = 'Webhook Endpoint'
        verbose_name_plural = 'Webhook Endpoints'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self) -> str:
        return f'{self.name} → {self.url}'

    def subscribes_to(self, event: str) -> bool:
        """True if this endpoint receives `event` (wildcards not supported yet)."""
        return event in (self.events or [])


class WebhookDelivery(models.Model):
    """One attempt-history row per outbound webhook fire.

    State machine:
        PENDING ──deliver_webhook 2xx──► SENT       (terminal)
                ├─deliver_webhook 4xx/5xx─► PENDING (retry; attempts++)
                └─max_retries reached────► EXHAUSTED (terminal; DLQ entry)
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed (will retry)'
        EXHAUSTED = 'exhausted', 'Exhausted (DLQ)'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name='deliveries',
    )
    event = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    # Truncated to 2000 chars on save (large HTML error pages mustn't bloat the row).
    response_body = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Webhook Delivery'
        verbose_name_plural = 'Webhook Deliveries'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['endpoint', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'{self.event} → {self.endpoint_id} [{self.status}]'

    def save(self, *args, **kwargs):
        # Defensive truncation — upstream services may pass arbitrary length.
        if self.response_body and len(self.response_body) > 2000:
            self.response_body = self.response_body[:2000]
        super().save(*args, **kwargs)
