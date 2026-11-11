"""
Sitting REST API.

Staff / org-admin (permission exam:edit, or platform staff/superuser):
  GET/POST /sittings/                    list / create
  GET      /sittings/{id}/               detail
  POST     /sittings/{id}/open|close|calculate|publish/
  GET      /sittings/{id}/results/       all results + item difficulties
  POST     /certificates/{id}/revoke/

Student (own data only):
  GET /sittings/mine/                    own results of PUBLISHED sittings
  GET /sittings/{id}/my-result/          404 until the sitting is PUBLISHED
  GET /certificates/mine/

Public:
  GET /certificates/verify/{number}/     initials + grade + validity only
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tenant import unscoped_context

from . import sitting_service
from .models import MockExam
from .sitting_models import Certificate, ExamSitting, GradeScale, SittingResult
from .sitting_serializers import (
    CertificateSerializer,
    SittingCreateSerializer,
    SittingItemSerializer,
    SittingSerializer,
    StaffResultSerializer,
    StudentResultSerializer,
)


class SittingStateConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'Sitting is in the wrong state for this action.'
    default_code = 'sitting_state_conflict'


class CanManageSittings(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        org = getattr(request, 'org', None)
        return org is not None and user.has_org_permission(org, 'exam:edit')


def _get_sitting(pk) -> ExamSitting:
    # TenantManager scopes to the request org; other tenants' ids 404.
    return get_object_or_404(ExamSitting.objects.select_related('grade_scale'), pk=pk)


class SittingListCreateView(APIView):
    permission_classes = [CanManageSittings]

    def get(self, request):
        qs = ExamSitting.objects.select_related('grade_scale').order_by('-opens_at')
        return Response(SittingSerializer(qs, many=True).data)

    def post(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            raise ValidationError('Tenant context topilmadi.')
        ser = SittingCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        mock = MockExam.objects.filter(pk=data['mock_exam']).first()
        # Public platform mocks are visible to every tenant, but a sitting is tenant data:
        # only the mock's own organization may schedule it.
        if mock is None or mock.organization_id != org.id:
            raise ValidationError({'mock_exam': 'Mock exam not found in this organization.'})
        if ExamSitting.objects.filter(mock_exam=mock).exists():
            raise ValidationError({'mock_exam': 'This mock exam already has a sitting.'})

        if 'grade_scale' in data:
            scale = GradeScale.objects.filter(pk=data['grade_scale']).first()
            if scale is None:
                raise ValidationError({'grade_scale': 'Grade scale not found.'})
        else:
            scale = GradeScale.national_default(org)

        sitting = ExamSitting.objects.create(
            organization=org,
            mock_exam=mock,
            grade_scale=scale,
            title=data['title'],
            opens_at=data['opens_at'],
            closes_at=data['closes_at'],
            created_by=request.user,
        )
        return Response(SittingSerializer(sitting).data, status=status.HTTP_201_CREATED)


class SittingDetailView(APIView):
    permission_classes = [CanManageSittings]

    def get(self, request, pk):
        return Response(SittingSerializer(_get_sitting(pk)).data)


class SittingActionView(APIView):
    """POST /sittings/{id}/<action>/ — action is bound in urls.py."""

    permission_classes = [CanManageSittings]
    action = None

    def post(self, request, pk):
        sitting = _get_sitting(pk)
        try:
            if self.action == 'open':
                sitting.open()
            elif self.action == 'close':
                sitting.close()
            elif self.action == 'calculate':
                sitting_service.calculate(sitting)
            elif self.action == 'publish':
                sitting_service.publish(sitting)
            else:  # pragma: no cover - wiring error
                raise ValueError('unknown action')
        except ValueError as exc:
            raise SittingStateConflict(str(exc)) from exc
        sitting.refresh_from_db()
        return Response(SittingSerializer(sitting).data)


class SittingResultsView(APIView):
    permission_classes = [CanManageSittings]

    def get(self, request, pk):
        sitting = _get_sitting(pk)
        return Response(
            {
                'sitting': SittingSerializer(sitting).data,
                'results': StaffResultSerializer(sitting.results.all(), many=True).data,
                'items': SittingItemSerializer(sitting.items.all(), many=True).data,
            }
        )


class CertificateRevokeView(APIView):
    permission_classes = [CanManageSittings]

    def post(self, request, pk):
        cert = get_object_or_404(Certificate, pk=pk)
        cert.revoke(str(request.data.get('reason', '')))
        return Response(CertificateSerializer(cert).data)


# ─── Student ──────────────────────────────────────────────────────────────────


def _own_published_results(user):
    return SittingResult.objects.filter(
        user=user, sitting__status=ExamSitting.Status.PUBLISHED
    ).select_related('sitting', 'certificate')


class MyResultsView(APIView):
    def get(self, request):
        return Response(
            StudentResultSerializer(_own_published_results(request.user), many=True).data
        )


class MyResultView(APIView):
    def get(self, request, pk):
        # Not published -> 404 (do not even confirm that a result exists).
        result = get_object_or_404(_own_published_results(request.user), sitting_id=pk)
        return Response(StudentResultSerializer(result).data)


class MyCertificatesView(APIView):
    def get(self, request):
        if request.user.is_anonymous:  # pragma: no cover - IsAuthenticated default
            raise PermissionDenied()
        qs = Certificate.objects.filter(user=request.user)
        return Response(CertificateSerializer(qs, many=True).data)


# ─── Public verification ──────────────────────────────────────────────────────


class CertificateVerifyView(APIView):
    """Anonymous. Exposes initials, grade and validity — nothing else."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request, number):
        number = sitting_service.normalize_number(number)
        # Anonymous callers have no tenant; the lookup key is the unguessable number.
        with unscoped_context():
            cert = Certificate.objects.filter(number=number).first()
        if cert is None:
            return Response(
                {'valid': False, 'status': 'not_found'}, status=status.HTTP_404_NOT_FOUND
            )
        cert_status = cert.status
        if cert_status == 'revoked':
            return Response({'valid': False, 'status': 'revoked', 'number': cert.number})
        return Response(
            {
                'valid': cert_status == 'valid',
                'status': cert_status,
                'number': cert.number,
                'holder_initials': cert.holder_initials,
                'grade': cert.grade,
                'issued_at': cert.issued_at,
                'valid_until': cert.valid_until,
            }
        )
