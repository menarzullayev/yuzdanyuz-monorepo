"""Partnership applications: public submit endpoint + staff CRM endpoints."""

import logging
import re
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from core.utils import rate_limiter as rl
from core.utils.client_ip import get_client_ip

from .hooks import notify_partnership_application_created
from .partnership_models import (
    ALLOWED_STATUS_TRANSITIONS,
    ApplicantType,
    ApplicationStatus,
    PartnershipApplication,
)

log = logging.getLogger(__name__)

# 5 applications per hour per IP (on top of the global API middleware tier)
APPLY_TIER = rl.RateTier(name='partnership_apply', limit=5, window=3600)
DUPLICATE_WINDOW = timedelta(hours=24)


class ApplyThrottle(BaseThrottle):
    def allow_request(self, request, view):
        try:
            rl.check(f'ip:{get_client_ip(request)}', APPLY_TIER)
        except rl.RateLimitExceeded as exc:
            self._wait = exc.retry_after
            return False
        except Exception as exc:  # Redis down: fail open, like the middleware
            log.error('partnership throttle error: %s', exc)
        return True

    def wait(self):
        return getattr(self, '_wait', None)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 9:
        digits = '998' + digits
    if not re.fullmatch(r'998\d{9}', digits):
        raise serializers.ValidationError("Telefon raqami +998XXXXXXXXX formatida bo'lishi kerak.")
    return '+' + digits


class ApplySerializer(serializers.Serializer):
    applicant_type = serializers.ChoiceField(choices=ApplicantType.choices)
    name = serializers.CharField(max_length=200)
    contact_person = serializers.CharField(max_length=120)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    region = serializers.CharField(max_length=100)
    students_count = serializers.IntegerField(min_value=1, max_value=100_000)
    message = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    # Honeypot: hidden in the form, real users never fill it
    website = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate_phone(self, value):
        return normalize_phone(value)

    def validate_email(self, value):
        return value.strip().lower()


class ApplyView(APIView):
    authentication_classes = []  # public: no session, so no CSRF requirement
    permission_classes = [AllowAny]
    throttle_classes = [ApplyThrottle]

    def post(self, request):
        ser = ApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)

        if data.pop('website', ''):
            log.info('partnership honeypot hit from %s', get_client_ip(request))
            return Response({'detail': 'Ariza qabul qilindi.'}, status=status.HTTP_201_CREATED)

        since = timezone.now() - DUPLICATE_WINDOW
        dup = Q(phone=data['phone'])
        if data.get('email'):
            dup |= Q(email=data['email'])
        if PartnershipApplication.objects.filter(dup, created_at__gte=since).exists():
            return Response(
                {
                    'detail': 'Bu telefon yoki email bilan ariza bugun allaqachon yuborilgan.',
                    'code': 'duplicate_application',
                },
                status=status.HTTP_409_CONFLICT,
            )

        app = PartnershipApplication.objects.create(**data)
        notify_partnership_application_created(app)
        return Response({'detail': 'Ariza qabul qilindi.'}, status=status.HTTP_201_CREATED)


# ── Staff CRM ───────────────────────────────────────────────────────────────


class IsPlatformStaff(BasePermission):
    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and (u.is_staff or u.is_superuser))


class ApplicationSerializer(serializers.ModelSerializer):
    assigned_to_username = serializers.CharField(source='assigned_to.username', read_only=True)

    class Meta:
        model = PartnershipApplication
        fields = [
            'id', 'applicant_type', 'name', 'contact_person', 'phone', 'email', 'region',
            'students_count', 'message', 'status', 'notes', 'next_call_date', 'assigned_to',
            'assigned_to_username', 'contacted_at', 'created_at', 'updated_at',
        ]  # fmt: skip
        read_only_fields = [
            'id', 'applicant_type', 'name', 'contact_person', 'phone', 'email', 'region',
            'students_count', 'message', 'contacted_at', 'created_at', 'updated_at',
        ]  # fmt: skip

    def validate_assigned_to(self, value):
        if value is not None and not (value.is_staff or value.is_superuser):
            raise serializers.ValidationError('Faqat platforma xodimiga tayinlash mumkin.')
        return value

    def validate_status(self, value):
        current = self.instance.status if self.instance else ApplicationStatus.NEW
        if value != current and value not in ALLOWED_STATUS_TRANSITIONS[current]:
            raise serializers.ValidationError(f"'{current}' dan '{value}' ga o'tish mumkin emas.")
        return value

    def update(self, instance, validated):
        if validated.get('status') == ApplicationStatus.CONTACTED and not instance.contacted_at:
            instance.contacted_at = timezone.now()
        return super().update(instance, validated)


def _filtered(request):
    qs = PartnershipApplication.objects.select_related('assigned_to')
    p = request.query_params
    if p.get('status') in ApplicationStatus.values:
        qs = qs.filter(status=p['status'])
    if p.get('type') in ApplicantType.values:
        qs = qs.filter(applicant_type=p['type'])
    if q := p.get('q', '').strip():
        qs = qs.filter(
            Q(name__icontains=q) | Q(contact_person__icontains=q) | Q(phone__icontains=q)
        )
    calls = p.get('calls')
    open_states = [ApplicationStatus.NEW, ApplicationStatus.CONTACTED]
    today = timezone.localdate()
    if calls == 'today':
        qs = qs.filter(next_call_date=today, status__in=open_states)
    elif calls == 'overdue':
        qs = qs.filter(next_call_date__lt=today, status__in=open_states)
    return qs


class ApplicationListView(generics.ListAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = ApplicationSerializer

    def get_queryset(self):
        return _filtered(self.request)


class ApplicationDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsPlatformStaff]
    serializer_class = ApplicationSerializer
    queryset = PartnershipApplication.objects.select_related('assigned_to')
    http_method_names = ['get', 'patch', 'head', 'options']


class ApplicationSummaryView(APIView):
    permission_classes = [IsPlatformStaff]

    def get(self, request):
        today = timezone.localdate()
        open_states = [ApplicationStatus.NEW, ApplicationStatus.CONTACTED]
        by_status = dict(
            PartnershipApplication.objects.values_list('status').annotate(n=Count('id'))
        )
        return Response(
            {
                'by_status': {s: by_status.get(s, 0) for s in ApplicationStatus.values},
                'calls_today': PartnershipApplication.objects.filter(
                    next_call_date=today, status__in=open_states
                ).count(),
                'calls_overdue': PartnershipApplication.objects.filter(
                    next_call_date__lt=today, status__in=open_states
                ).count(),
            }
        )
