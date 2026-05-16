"""
ISSUE-308 — Dead-letter queue for failed Celery tasks.

Pattern: Celery task max_retries dan keyin fail bo'lsa, `FailedTask` model'iga
yoziladi. Admin UI orqali manual reprocess yoki delete qilish mumkin.

Setup:
    from celery.signals import task_failure
    task_failure.connect(record_failure)

Worker'lar `app.conf.task_acks_late=True` bilan ishlashi kerak — task fail
bo'lsa, Redis broker ack qilmaydi va retry imkoniyati saqlanadi.
"""

from __future__ import annotations

import logging
import uuid

from celery.signals import task_failure
from django.db import models

logger = logging.getLogger(__name__)


class FailedTask(models.Model):
    """Celery task max_retries dan keyin fail bo'lsa shu yerda yoziladi."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_name = models.CharField(max_length=255, db_index=True)
    task_id = models.CharField(max_length=64, unique=True)
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    exception = models.CharField(max_length=255)
    traceback = models.TextField()
    failed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reprocessed_at = models.DateTimeField(null=True, blank=True)
    reprocessed_by = models.ForeignKey(
        'accounts.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        verbose_name = 'Failed Task (DLQ)'
        verbose_name_plural = 'Failed Tasks (DLQ)'
        ordering = ['-failed_at']
        indexes = [
            models.Index(fields=['task_name', '-failed_at']),
        ]

    def __str__(self):
        return f'{self.task_name} [{str(self.id)[:8]}] {self.exception[:60]}'

    def reprocess(self, by_user=None) -> str:
        """Retry the failed task with original args/kwargs.

        Returns new Celery task ID.
        """
        from celery import current_app
        from django.utils import timezone

        result = current_app.send_task(self.task_name, args=self.args, kwargs=self.kwargs)
        self.reprocessed_at = timezone.now()
        self.reprocessed_by = by_user
        self.save(update_fields=['reprocessed_at', 'reprocessed_by'])
        return result.id


@task_failure.connect
def record_failure(
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    sender=None,
    **extra,
):
    """Celery signal handler — har task failure'da DLQ'ga yozadi.

    Sentry'ga ham yetadi (Sentry CeleryIntegration), lekin DLQ retry imkonini beradi.
    Sentry observability, DLQ — recovery.
    """
    # Prometheus metric (ISSUE-104'ga qo'shimcha)
    try:
        from prometheus_client import Counter

        Counter(
            'yz_task_dlq_total',
            'Failed Celery tasks routed to DLQ',
            ['task_name'],
        ).labels(task_name=sender.name if sender else 'unknown').inc()
    except (ImportError, ValueError):
        # Counter already registered (duplicate) yoki prometheus_client yo'q — skip
        pass

    try:
        FailedTask.objects.create(
            task_name=sender.name if sender else 'unknown',
            task_id=task_id or str(uuid.uuid4()),
            args=list(args) if args else [],
            kwargs=dict(kwargs) if kwargs else {},
            exception=str(exception)[:255],
            traceback=str(traceback)[:5000],
        )
    except Exception as e:
        logger.error('Failed to record DLQ entry for %s: %s', task_id, e)
