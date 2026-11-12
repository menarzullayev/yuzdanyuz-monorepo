"""
Business analytics API: finance (platform staff), funnel/retention, monthly org report.

  GET    /api/analytics/finance/?period=this_month|last_month|this_year
  GET    /api/analytics/finance/expenses/   ?type= ?date_from= ?date_to=
  POST   /api/analytics/finance/expenses/
  GET/PATCH/DELETE /api/analytics/finance/expenses/<uuid>/      (DELETE is a soft delete)
  GET    /api/analytics/funnel/?days=30&cohort_days=120[&org=<uuid>]
  GET    /api/analytics/org-report/?month=YYYY-MM[&org=<uuid>][&pass_score=60][&export=csv|html]

Finance is platform staff only. Funnel and org-report are open to platform staff (any org via
?org=, funnel defaults to the whole platform) and to org owner/admin/manager for their own org
only; asking for another org is a 403.
"""

import uuid
from datetime import date, timedelta

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.models import Membership, MembershipStatus, Organization

from . import finance, funnel, org_report
from .models import Expense

ADMIN_ROLES = ('owner', 'admin', 'manager')
MAX_FUTURE_DAYS = 31
MIN_EXPENSE_DATE = date(2020, 1, 1)


def _is_staff(user) -> bool:
    return bool(user.is_staff or user.is_superuser)


class IsPlatformStaff(BasePermission):
    message = 'Faqat platforma xodimlari uchun.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and _is_staff(request.user))


def _int_param(request, key, default, low, high):
    try:
        v = int(request.query_params.get(key, default))
    except (TypeError, ValueError):
        raise ValidationError({key: 'integer'}) from None
    if not low <= v <= high:
        raise ValidationError({key: f'{low}..{high}'})
    return v


def _resolve_org(request, *, staff_default_to_request_org=False):
    """Org the caller may analyse, or None (platform scope, staff only)."""
    user, raw = request.user, request.query_params.get('org')
    if _is_staff(user):
        if not raw:
            return getattr(request, 'org', None) if staff_default_to_request_org else None
        try:
            org_id = uuid.UUID(raw)
        except ValueError:
            raise ValidationError({'org': 'uuid'}) from None
        return get_object_or_404(Organization, pk=org_id)
    org = getattr(request, 'org', None)
    if org is None or (raw and raw != str(org.id)):
        raise PermissionDenied('Faqat o‘z tashkilotingiz ma‘lumotlarini ko‘ra olasiz.')
    if not Membership.objects.filter(
        user=user, organization=org, status=MembershipStatus.ACTIVE, role__name__in=ADMIN_ROLES
    ).exists():
        raise PermissionDenied('Faqat tashkilot admini ko‘ra oladi.')
    return org


# ─── Finance ─────────────────────────────────────────────────────────────────


class FinanceSummaryView(APIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        period = request.query_params.get('period', 'this_month')
        if period not in finance.PERIODS:
            raise ValidationError({'period': f'one of {list(finance.PERIODS)}'})
        return Response(finance.finance_summary(period))


class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = ['id', 'date', 'amount_uzs', 'type', 'note', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_by', 'created_at']

    def validate_amount_uzs(self, value):
        if value <= 0:
            raise serializers.ValidationError('Summa 0 dan katta bo‘lishi kerak.')
        return value

    def validate_date(self, value):
        limit = timezone.localdate() + timedelta(days=MAX_FUTURE_DAYS)
        if value > limit:
            raise serializers.ValidationError(
                f'Sana {MAX_FUTURE_DAYS} kundan ortiq kelajakda bo‘lmasligi kerak.'
            )
        if value < MIN_EXPENSE_DATE:
            raise serializers.ValidationError(
                f'Sana {MIN_EXPENSE_DATE} dan oldin bo‘lmasligi kerak.'
            )
        return value


class ExpenseListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        qs = Expense.objects.filter(is_deleted=False)
        p = self.request.query_params
        if p.get('type'):
            if p['type'] not in Expense.Type.values:
                raise ValidationError({'type': f'one of {Expense.Type.values}'})
            qs = qs.filter(type=p['type'])
        for key, lookup in (('date_from', 'date__gte'), ('date_to', 'date__lte')):
            if p.get(key):
                try:
                    qs = qs.filter(**{lookup: date.fromisoformat(p[key])})
                except ValueError:
                    raise ValidationError({key: 'YYYY-MM-DD'}) from None
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class ExpenseDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = ExpenseSerializer
    queryset = Expense.objects.filter(is_deleted=False)
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def perform_destroy(self, instance):
        instance.soft_delete()


# ─── Funnel / retention ──────────────────────────────────────────────────────


class FunnelView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _resolve_org(request)
        days = _int_param(request, 'days', 30, 1, 365)
        cohort_days = _int_param(request, 'cohort_days', 120, 1, 365)
        return Response(funnel.get_funnel(funnel.Scope(org), days=days, cohort_days=cohort_days))


# ─── Monthly organization report ─────────────────────────────────────────────


def _parse_month(raw: str | None) -> date:
    today = timezone.localdate()
    if not raw:
        return today.replace(day=1)
    try:
        y, m = raw.split('-')
        first = date(int(y), int(m), 1)
    except (ValueError, TypeError):
        raise ValidationError({'month': 'YYYY-MM'}) from None
    if first > today.replace(day=1):
        raise ValidationError({'month': 'kelajakdagi oy bo‘lishi mumkin emas'})
    return first


class OrgReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = _resolve_org(request, staff_default_to_request_org=True)
        if org is None:
            raise ValidationError({'org': 'org is required'})
        month = _parse_month(request.query_params.get('month'))
        pass_score = _int_param(request, 'pass_score', org_report.DEFAULT_PASS_SCORE, 0, 100)
        report = org_report.build_report(org, month, pass_score=pass_score)

        export = request.query_params.get('export')
        if export is None:
            return Response(report)
        if export == 'csv':
            resp = HttpResponse(org_report.to_csv(report), content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = (
                f'attachment; filename="report-{org.slug}-{report["month"]}.csv"'
            )
            return resp
        if export == 'html':
            return HttpResponse(org_report.to_html(report), content_type='text/html; charset=utf-8')
        raise ValidationError({'export': 'csv|html'})
