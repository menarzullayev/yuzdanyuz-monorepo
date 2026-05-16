"""
Task 8 — B2B Analytics modellari.

Architecture (Bosqich 17):
  ClickHouse OLAP — kelajakda. Hozir stub mode'da PostgreSQL'da denormalized
  ExamEvent jadvali — analytics queries uchun aggregation friendly.

Models:
  - ExamEvent     — har submitted attempt'dan denormalized snapshot
  - ReportExport  — async CSV/Excel export tracking
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin


class ExamEvent(TenantTimestampMixin):
    """
    Denormalized event — har ExamAttempt SUBMITTED bo'lganda yoziladi.
    Analytics queries (avg/weekly/distribution) shu jadvaldan ishlaydi.

    Production: ClickHouse'ga ham parallel yoziladi (kelajakda toggle).
    Hozir: faqat PostgreSQL.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exam_events'
    )

    exam_attempt_id = models.UUIDField(db_index=True)
    mock_exam_id = models.UUIDField(db_index=True)
    subject_id = models.UUIDField(null=True, blank=True, db_index=True)
    subject_name = models.CharField(max_length=255, blank=True)

    score = models.DecimalField(max_digits=6, decimal_places=2)
    correct_count = models.PositiveIntegerField()
    total_count = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=0)

    region_id = models.UUIDField(null=True, blank=True, db_index=True)

    completed_at = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = _('Exam Event')
        verbose_name_plural = _('Exam Events')
        ordering = ['-completed_at']
        indexes = [
            models.Index(fields=['organization', 'subject_id', '-completed_at']),
            models.Index(fields=['organization', '-completed_at']),
            models.Index(fields=['organization', 'user', '-completed_at']),
        ]

    def __str__(self):
        return f'{self.user} {self.score} ({self.completed_at:%Y-%m-%d})'


class ReportExport(TenantTimestampMixin):
    """
    Heavy CSV/Excel export tracking. Celery task async ishlaydi.
    """

    class Format(models.TextChoices):
        CSV = 'csv', 'CSV'
        EXCEL = 'excel', 'Excel (.xlsx)'

    class Status(models.TextChoices):
        PENDING = 'pending', _('Kutilmoqda')
        PROCESSING = 'processing', _('Ishlanmoqda')
        READY = 'ready', _('Tayyor')
        FAILED = 'failed', _('Xato')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='report_exports'
    )
    report_type = models.CharField(
        max_length=32,
        help_text=_("Misol: 'students_full', 'subject_breakdown', 'weekly_trend'"),
    )
    fmt = models.CharField(max_length=6, choices=Format.choices, default=Format.CSV)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    filters = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to='reports/', null=True, blank=True)
    error = models.TextField(blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Report Export')
        verbose_name_plural = _('Report Exports')
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['organization', '-requested_at']),
        ]

    def __str__(self):
        return f'{self.user} {self.report_type} ({self.status})'
