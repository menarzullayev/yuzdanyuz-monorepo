"""School / learning-centre rating API (mounted under /api/v1/engagement/ratings/).

GET   organizations/?period=&kind=   public ranking (no auth)
GET   mine/?period=                  own org position, incl. hidden orgs (exam:view)
GET/PATCH settings/                  kind + visibility (org admin)
"""

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import rating_service as svc
from .permissions import CanViewExams, is_org_admin
from .rating_models import OrgRatingProfile


def _period(request):
    try:
        return svc.parse_period(request.query_params.get('period'))
    except ValueError as e:
        raise ValidationError({'period': str(e)}) from e


class PublicRatingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        period = _period(request)
        kind = request.query_params.get('kind') or None
        if kind and kind not in OrgRatingProfile.Kind.values:
            raise ValidationError({'kind': f'kind must be one of {OrgRatingProfile.Kind.values}'})
        return Response(
            {
                'period': period,
                'kind': kind,
                'min_students': svc.RATING_MIN_STUDENTS,
                'min_attempts': svc.RATING_MIN_ATTEMPTS,
                'results': svc.public_ranking(period, kind),
            }
        )


class MyOrgRatingView(APIView):
    permission_classes = [CanViewExams]

    def get(self, request):
        org = getattr(request, 'org', None)
        if org is None:
            raise ValidationError('No organization context.')
        return Response(svc.own_position(org, _period(request)))


class SettingsSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=OrgRatingProfile.Kind.choices, required=False)
    visibility = serializers.ChoiceField(
        choices=OrgRatingProfile.Visibility.choices, required=False
    )


class RatingSettingsView(APIView):
    def _check(self, request):
        org = getattr(request, 'org', None)
        if not request.user.is_authenticated or org is None or not is_org_admin(request.user, org):
            raise PermissionDenied('Only an org admin can manage rating settings.')
        return org

    def _data(self, org):
        prof = OrgRatingProfile.objects.filter(organization=org).first()
        return {
            'kind': prof.kind if prof else OrgRatingProfile.DEFAULT_KIND.value,
            'visibility': prof.visibility if prof else OrgRatingProfile.DEFAULT_VISIBILITY.value,
        }

    def get(self, request):
        return Response(self._data(self._check(request)))

    def patch(self, request):
        org = self._check(request)
        ser = SettingsSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        prof, _ = OrgRatingProfile.objects.get_or_create(organization=org)
        for f, v in ser.validated_data.items():
            setattr(prof, f, v)
        prof.save()
        svc.invalidate_cache()
        return Response(self._data(org))
