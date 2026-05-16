"""
Task 8 — Analytics Celery tasks.

  - generate_export(report_id) — async CSV/Excel generation.
    Heavy reports uchun (10K+ rows). User REST POST orqali request qiladi,
    Celery generate qiladi, status='ready' bo'lganda user file'ni yuklab oladi.
"""

import csv
import io
import logging

from celery import shared_task
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import ExamEvent, ReportExport

logger = logging.getLogger(__name__)


def _query_for_report(report_type: str, org, filters: dict):
    """Report turi bo'yicha ExamEvent queryset."""
    qs = ExamEvent.global_objects.filter(organization=org)
    if 'date_from' in filters:
        qs = qs.filter(completed_at__gte=filters['date_from'])
    if 'date_to' in filters:
        qs = qs.filter(completed_at__lte=filters['date_to'])
    return qs


def _build_csv(qs) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ['user_email', 'subject', 'score', 'correct', 'total', 'duration_sec', 'completed_at']
    )
    for ev in qs.select_related('user').iterator(chunk_size=500):
        writer.writerow(
            [
                ev.user.email or ev.user.username,
                ev.subject_name,
                str(ev.score),
                ev.correct_count,
                ev.total_count,
                ev.duration_seconds,
                ev.completed_at.isoformat(),
            ]
        )
    return buf.getvalue().encode('utf-8')


def _build_excel(qs) -> bytes:
    """openpyxl bilan .xlsx generate (catalog'da allaqachon ishlatilgan)."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = 'Exam Events'
    ws.append(
        ['user_email', 'subject', 'score', 'correct', 'total', 'duration_sec', 'completed_at']
    )
    for ev in qs.select_related('user').iterator(chunk_size=500):
        ws.append(
            [
                ev.user.email or ev.user.username,
                ev.subject_name,
                float(ev.score),
                ev.correct_count,
                ev.total_count,
                ev.duration_seconds,
                ev.completed_at.replace(tzinfo=None),
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@shared_task(name='analytics.generate_export', bind=True)
def generate_export(self, report_id: str) -> dict:
    try:
        report = ReportExport.objects.get(pk=report_id)
    except ReportExport.DoesNotExist:
        return {'status': 'not_found'}

    report.status = ReportExport.Status.PROCESSING
    report.save(update_fields=['status'])

    try:
        qs = _query_for_report(report.report_type, report.organization, report.filters)
        if report.fmt == ReportExport.Format.CSV:
            data = _build_csv(qs)
            ext = 'csv'
        else:
            data = _build_excel(qs)
            ext = 'xlsx'

        filename = f'{report.report_type}_{report.id}.{ext}'
        report.file.save(filename, ContentFile(data), save=False)
        report.status = ReportExport.Status.READY
        report.completed_at = timezone.now()
        report.save(update_fields=['file', 'status', 'completed_at'])
        return {'status': 'ready', 'report_id': str(report.id)}
    except Exception as e:
        logger.exception('generate_export failed for %s', report_id)
        report.status = ReportExport.Status.FAILED
        report.error = str(e)[:500]
        report.completed_at = timezone.now()
        report.save(update_fields=['status', 'error', 'completed_at'])
        return {'status': 'failed', 'error': str(e)[:200]}
