"""Operations models: health probes, status incidents, security events, alert state, bot settings.

None of these hold tenant data (platform-level), so they do not inherit TenantTimestampMixin
and need no RLS policy.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProbeResult(models.Model):
    """One 5-minute health probe. A missing row counts as downtime in the uptime maths."""

    checked_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Overall verdict: database and Redis both answered. The AI provider is informational.
    ok = models.BooleanField()
    db_ok = models.BooleanField()
    redis_ok = models.BooleanField()
    ai_ok = models.BooleanField(null=True, blank=True)  # null = not probed
    db_ms = models.PositiveIntegerField(null=True, blank=True)
    redis_ms = models.PositiveIntegerField(null=True, blank=True)
    ai_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-checked_at']

    def __str__(self):
        return f'{self.checked_at:%Y-%m-%d %H:%M} {"ok" if self.ok else "FAIL"}'


class StatusIncident(models.Model):
    class Kind(models.TextChoices):
        INCIDENT = 'incident', 'Incident'
        MAINTENANCE = 'maintenance', 'Maintenance'

    class Impact(models.TextChoices):
        NONE = 'none', 'None'
        PARTIAL = 'partial', 'Partial'
        MAJOR = 'major', 'Major'

    class Status(models.TextChoices):
        INVESTIGATING = 'investigating', 'Investigating'
        IDENTIFIED = 'identified', 'Identified'
        MONITORING = 'monitoring', 'Monitoring'
        RESOLVED = 'resolved', 'Resolved'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.INCIDENT)
    title = models.CharField(max_length=200)
    impact = models.CharField(max_length=10, choices=Impact.choices, default=Impact.NONE)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.INVESTIGATING)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Planned end (maintenance window); the real end is resolved_at.
    ends_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'[{self.kind}/{self.status}] {self.title}'


class StatusIncidentUpdate(models.Model):
    incident = models.ForeignKey(StatusIncident, on_delete=models.CASCADE, related_name='updates')
    status = models.CharField(max_length=14, choices=StatusIncident.Status.choices)
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.incident_id} {self.status}'


class SecurityEvent(models.Model):
    class Type(models.TextChoices):
        LOGIN_FAILED = 'login_failed', 'Failed login'
        ACCOUNT_LOCKED = 'account_locked', 'Account locked'
        RATE_LIMITED = 'rate_limited', 'Rate limited (429)'
        BOT_HIT = 'bot_hit', 'Bot / scraper hit'
        PAYMENT_MISMATCH = 'payment_mismatch', 'Payment amount mismatch'
        RECOVERY_REQUEST = 'recovery_request', 'Recovery request'
        PASSWORD_RESET = 'password_reset', 'Password reset'

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=24, choices=Type.choices)
    # "user:<pk>" for an authenticated actor, otherwise "ip:<address>".
    source = models.CharField(max_length=80)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    path = models.CharField(max_length=300, blank=True)
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['event_type', '-created_at']),
            models.Index(fields=['source', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f'{self.event_type} {self.source}'


class AlertState(models.Model):
    """Small key/value store used to de-duplicate alerts (state changes, per-source cooldowns)."""

    key = models.CharField(max_length=120, unique=True)
    value = models.CharField(max_length=40, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f'{self.key}={self.value}'


class BotSettings(models.Model):
    """Singleton (pk=1). DB values override the environment; the environment is the fallback."""

    token_enc = models.TextField(blank=True)  # core.crypto ciphertext; never returned
    webhook_url = models.URLField(max_length=300, blank=True)
    webhook_secret_enc = models.TextField(blank=True)  # core.crypto ciphertext
    admin_chat_ids = models.JSONField(default=list, blank=True)
    channel_id = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(pk=1), name='botsettings_singleton')
        ]

    def __str__(self):
        return 'Bot settings'

    @classmethod
    def load(cls) -> 'BotSettings':
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
