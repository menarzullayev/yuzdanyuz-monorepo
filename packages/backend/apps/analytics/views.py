"""
Task 8 — B2B Analytics REST API.

Endpoints:
  GET  /api/analytics/dashboard/?days=30        all widgets data
  GET  /api/analytics/subjects/?days=30          subject averages (bar chart)
  GET  /api/analytics/weekly/?weeks=12            weekly growth (line chart)
  GET  /api/analytics/distribution/?days=30      score distribution (pie chart)
  GET  /api/analytics/weak-students/?limit=10    weak students list

  POST /api/analytics/export/                     create report export
  GET  /api/analytics/export/<id>/                 status + download URL

Permission: Org admin/owner/teacher (not students).
Tenant isolation: request.org via TenantMiddleware.
"""

import logging

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import ReportExport
from .tasks import generate_export

logger = logging.getLogger(__name__)


def _is_dashboard_user(user, org) -> bool:
    """Org admin/owner/manager/teacher (student emas)."""
    if user.is_superuser or user.is_staff:
        return True
    return user.memberships.filter(
        organization=org,
        status='active',
        role__name__in=['owner', 'admin', 'manager', 'teacher'],
    ).exists()


def _require_org(request):
    org = getattr(request, 'org', None)
    if org is None:
        raise ValidationError({'detail': "Tenant context yo'q"})
    if not _is_dashboard_user(request.user, org):
        raise PermissionDenied("Faqat org admin/teacher dashboard ko'ra oladi.")
    return org


def _parse_int(request, key: str, default: int, *, low: int = 1, high: int = 365) -> int:
    try:
        v = int(request.query_params.get(key, default))
    except (TypeError, ValueError):
        raise ValidationError({key: 'integer'}) from None
    if v < low or v > high:
        raise ValidationError({key: f"{low}..{high} oralig'ida"})
    return v


# ─── Dashboard widgets ───────────────────────────────────────────────────────


class DashboardSummaryView(APIView):
    def get(self, request):
        org = _require_org(request)
        days = _parse_int(request, 'days', 30, low=1, high=365)
        return Response(services.get_dashboard_summary(org, days=days))


class SubjectAveragesView(APIView):
    def get(self, request):
        org = _require_org(request)
        days = _parse_int(request, 'days', 30)
        return Response({'subjects': services.get_subject_averages(org, days=days)})


class WeeklyGrowthView(APIView):
    def get(self, request):
        org = _require_org(request)
        weeks = _parse_int(request, 'weeks', 12, low=1, high=52)
        return Response({'weekly': services.get_weekly_growth(org, weeks=weeks)})


class ScoreDistributionView(APIView):
    def get(self, request):
        org = _require_org(request)
        days = _parse_int(request, 'days', 30)
        return Response({'distribution': services.get_score_distribution(org, days=days)})


class WeakStudentsView(APIView):
    def get(self, request):
        org = _require_org(request)
        limit = _parse_int(request, 'limit', 10, low=1, high=100)
        days = _parse_int(request, 'days', 30)
        return Response({'students': services.get_weak_students(org, limit=limit, days=days)})


# ─── Reports export ──────────────────────────────────────────────────────────


class ReportExportCreateView(APIView):
    """
    POST /api/analytics/export/
    body: {report_type: 'students_full', fmt: 'csv'|'excel', filters: {...}}
    """

    def post(self, request):
        org = _require_org(request)
        report_type = request.data.get('report_type', 'students_full')
        fmt = request.data.get('fmt', 'csv')
        filters = request.data.get('filters', {})

        if fmt not in ('csv', 'excel'):
            raise ValidationError({'fmt': "'csv' yoki 'excel'"})

        report = ReportExport.objects.create(
            organization=org,
            user=request.user,
            report_type=report_type,
            fmt=fmt,
            filters=filters,
            status=ReportExport.Status.PENDING,
        )
        generate_export.delay(str(report.id))
        report.refresh_from_db()
        return Response(_serialize_report(report), status=status.HTTP_202_ACCEPTED)


class ReportExportDetailView(APIView):
    """GET /api/analytics/export/<id>/  (?download=1 → file download)"""

    def get(self, request, report_id):
        org = _require_org(request)
        report = get_object_or_404(ReportExport, pk=report_id, organization=org)

        if request.query_params.get('download') == '1' and report.file:
            return FileResponse(report.file.open('rb'), as_attachment=True)
        return Response(_serialize_report(report))


def _serialize_report(report: ReportExport) -> dict:
    return {
        'id': str(report.id),
        'report_type': report.report_type,
        'fmt': report.fmt,
        'status': report.status,
        'filters': report.filters,
        'file_url': report.file.url if report.file else None,
        'error': report.error,
        'requested_at': report.requested_at,
        'completed_at': report.completed_at,
    }
