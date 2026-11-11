"""
Onboarding profile API (required answers are asked before the first result, not at signup).

  GET    /api/auth/onboarding/                    current answers + {is_complete, missing}
  PATCH  /api/auth/onboarding/                    partial update (see services.onboarding.apply)
  GET    /api/auth/onboarding/options/            reference data for the form (public)
  POST   /api/auth/onboarding/org-invite/         {code, consent: true}: join an organization
  DELETE /api/auth/onboarding/org-consent/<uuid>/ withdraw the "organization sees my results" consent
"""

import uuid

from django.utils import timezone
from rest_framework.response import Response

from apps.accounts.models import (
    DirectionPassingScore,
    DTMDirection,
    ExamCalendarEvent,
    ExamCertificate,
    ExamGoal,
    OrgResultsConsent,
    Region,
    TestLanguage,
)
from apps.accounts.services import onboarding as ob
from apps.accounts.views.base import AuthAPIView, PublicAPIView, api_error, field_errors


class OnboardingView(AuthAPIView):
    def get(self, request):
        return Response(ob.state(request.user))

    def patch(self, request):
        data = request.data
        # multipart/form input cannot carry lists/ints reliably: this API is JSON-only
        if hasattr(data, 'lists') or not isinstance(data, dict):
            return api_error('unsupported_media_type', 'Send application/json.', 415)
        errors = ob.apply(request.user, data)
        if errors:
            return api_error(
                'validation_error', 'Maʼlumotlar notoʻgʻri.', 400, field_errors(**errors)
            )
        return Response(ob.state(request.user))


class OnboardingOptionsView(PublicAPIView):
    def get(self, request):
        latest = {}
        for s in DirectionPassingScore.objects.order_by('direction_id', 'year'):
            latest[s.direction_id] = s
        today = timezone.localdate()
        return Response(
            {
                'goals': [
                    {
                        'code': g.code,
                        'name_uz': g.name_uz,
                        'name_ru': g.name_ru,
                        'name_en': g.name_en,
                        'requires_direction': g.requires_direction,
                    }
                    for g in ExamGoal.objects.filter(is_active=True)
                ],
                'dtm_directions': [
                    {
                        'code': d.code,
                        'name_uz': d.name_uz,
                        'name_ru': d.name_ru,
                        'name_en': d.name_en,
                        'subject_1': d.subject_1,
                        'subject_2': d.subject_2,
                        'passing_score': {
                            'year': latest[d.pk].year,
                            'grant': latest[d.pk].grant_score,
                            'contract': latest[d.pk].contract_score,
                        }
                        if d.pk in latest
                        else None,
                    }
                    for d in DTMDirection.objects.filter(is_active=True)
                ],
                'regions': [
                    {
                        'code': r.code,
                        'name_uz': r.name_uz,
                        'name_ru': r.name_ru,
                        'name_en': r.name_en,
                        'districts': [
                            {'id': d.pk, 'name_uz': d.name_uz, 'name_ru': d.name_ru}
                            for d in r.districts.all()
                        ],
                    }
                    for r in Region.objects.prefetch_related('districts')
                ],
                'exam_calendar': [
                    {
                        'exam_goal': e.exam_goal.code,
                        'title': e.title,
                        'date': e.date,
                        'registration_deadline': e.registration_deadline,
                    }
                    for e in ExamCalendarEvent.objects.filter(date__gte=today).select_related(
                        'exam_goal'
                    )[:50]
                ],
                'test_languages': [{'code': c, 'name': n} for c, n in TestLanguage.choices],
                'target_grades': list(ob.TARGET_GRADES),
                'certificate_types': [
                    {'code': c, 'name': n} for c, n in ExamCertificate.CertType.choices
                ],
                'acquisition_answers': list(ob.ACQUISITION_ANSWERS),
                'target_score_max': ob.MAX_TARGET_SCORE,
                'graduation_year_range': [today.year - 60, today.year + 8],
            }
        )


class OrgInviteView(AuthAPIView):
    def post(self, request):
        code = request.data.get('code', '')
        consent = request.data.get('consent', False)
        try:
            org = ob.join_with_invite(request.user, code, consent)
        except ob.InviteError as e:
            return api_error(e.code, str(e), 404 if e.code == 'invalid_invite' else 400)
        return Response({'organization': {'id': str(org.pk), 'name': org.name}})


class OrgConsentRevokeView(AuthAPIView):
    def delete(self, request, org_id: uuid.UUID):
        updated = OrgResultsConsent.objects.filter(
            user=request.user, organization_id=org_id, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        if not updated:
            return api_error('not_found', 'No such consent.', 404)
        return Response({'ok': True})
