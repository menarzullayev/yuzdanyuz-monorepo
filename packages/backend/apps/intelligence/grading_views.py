"""Teacher grading queue + AI calibration report.

  GET  /grading/queue/               pending submissions (?type=writing|speaking, ?org= staff only)
  GET  /grading/queue/next/          oldest pending item with its AI estimate (?skip=<id,id>)
  GET  /grading/queue/<id>/          one submission with the AI estimate
  POST /grading/queue/<id>/grade/    {score?, criteria?, comment?} -> final score
  GET  /grading/calibration/         AI vs teacher agreement report

Access: platform staff (all organizations) or an organization member holding `exam:edit`
(calibration: `analytics:view`), limited to submissions of questions owned by their
organization. Another organization's submission is a 404, never a 403.
"""

import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .grading import CRITERIA_BY_TYPE, EXAM_TYPES, cached_calibration
from .models import OpenEndedSubmission

PENDING_STATUSES = (
    OpenEndedSubmission.Status.PENDING,
    OpenEndedSubmission.Status.AI_REVIEWED,
    OpenEndedSubmission.Status.AWAITING_HUMAN,
    OpenEndedSubmission.Status.FAILED,
)
ORG_FIELD = 'question_version__question__organization_id'
MAX_COMMENT = 5000


def _is_platform_staff(user) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or user.is_staff))


class _OrgPermission(BasePermission):
    perm = ''

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        if _is_platform_staff(user):
            return True
        org = getattr(request, 'org', None)
        return org is not None and user.has_org_permission(org, self.perm)


class CanGrade(_OrgPermission):
    perm = 'exam:edit'


class CanViewCalibration(_OrgPermission):
    perm = 'analytics:view'


def _scope(request):
    """(organization id or None for all, optional ?type= submission_type)."""
    org_param = request.query_params.get('org')
    if org_param:
        try:
            org_param = str(uuid.UUID(org_param))
        except ValueError:
            raise ValidationError({'org': 'invalid id'}) from None
    if _is_platform_staff(request.user):
        org_id = org_param or None
    else:
        org_id = str(request.org.id)
        if org_param and org_param != org_id:
            raise PermissionDenied('Other organizations are not accessible.')
    exam_type = request.query_params.get('type')
    if exam_type and exam_type not in EXAM_TYPES:
        raise ValidationError({'type': "'writing' or 'speaking'"})
    return org_id, EXAM_TYPES[exam_type] if exam_type else None


def _submissions(request):
    org_id, sub_type = _scope(request)
    qs = OpenEndedSubmission.objects.filter(is_deleted=False)
    if org_id:
        qs = qs.filter(**{ORG_FIELD: org_id})
    if sub_type:
        qs = qs.filter(submission_type=sub_type)
    return qs, org_id, sub_type


def _exam_type(sub) -> str:
    return 'speaking' if sub.submission_type == OpenEndedSubmission.Type.AUDIO else 'writing'


def _ai_estimate(sub) -> dict | None:
    if sub.ai_score is None:
        return None
    criteria = CRITERIA_BY_TYPE[sub.submission_type]
    feedback = sub.ai_feedback or {}
    return {
        'score': float(sub.ai_score),
        'is_estimate': True,
        'criteria': {k: feedback[k] for k in criteria if k in feedback},
        'rationale': feedback.get('rationale', ''),
        'not_assessed': feedback.get('not_assessed', []),
    }


def _final(sub) -> dict | None:
    if sub.human_score is None:
        return None
    return {
        'score': float(sub.human_score),
        'criteria': sub.human_criteria or {},
        'comment': sub.human_notes,
        'graded_at': sub.human_completed_at,
    }


def _item(sub, *, full: bool = False) -> dict:
    qv = sub.question_version
    data = {
        'id': str(sub.id),
        'exam_type': _exam_type(sub),
        'status': sub.status,
        'submitted_at': sub.submitted_at,
        'user_id': str(sub.user_id) if sub.user_id else None,
        'question': (qv.content or {}).get('text', ''),
        'ai_estimate': _ai_estimate(sub),
        'final': _final(sub),
        'ai_skipped': (sub.ai_feedback or {}).get('skipped'),
    }
    if full:
        data['content'] = sub.content
        data['audio_url'] = sub.audio_file.url if sub.audio_file else None
        data['criteria_keys'] = list(CRITERIA_BY_TYPE[sub.submission_type])
    else:
        data['content'] = (sub.content or '')[:200]
    return data


class QueuePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def _pending(request):
    qs, _org, _type = _submissions(request)
    qs = qs.filter(human_score__isnull=True, status__in=PENDING_STATUSES)
    status_param = request.query_params.get('status')
    if status_param:
        if status_param not in PENDING_STATUSES:
            raise ValidationError({'status': f'one of {list(PENDING_STATUSES)}'})
        qs = qs.filter(status=status_param)
    return qs.select_related('question_version').order_by('submitted_at', 'id')


class GradingQueueView(APIView):
    permission_classes = [CanGrade]

    def get(self, request):
        paginator = QueuePagination()
        page = paginator.paginate_queryset(_pending(request), request)
        return paginator.get_paginated_response([_item(s) for s in page])


class GradingNextView(APIView):
    permission_classes = [CanGrade]

    def get(self, request):
        qs = _pending(request)
        skip = [s for s in request.query_params.get('skip', '').split(',') if s]
        try:
            skip = [str(uuid.UUID(s)) for s in skip]
        except ValueError:
            raise ValidationError({'skip': 'comma-separated submission ids'}) from None
        remaining = qs.count()
        nxt = qs.exclude(pk__in=skip).first() if skip else qs.first()
        return Response({'remaining': remaining, 'item': _item(nxt, full=True) if nxt else None})


class GradingDetailView(APIView):
    permission_classes = [CanGrade]

    def get(self, request, submission_id):
        qs, _org, _type = _submissions(request)
        sub = get_object_or_404(qs.select_related('question_version'), pk=submission_id)
        return Response(_item(sub, full=True))


def _score(value, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({field: 'must be a number'}) from None
    if isinstance(value, bool) or not number.is_finite() or not (0 <= number <= 100):
        raise ValidationError({field: 'must be between 0 and 100'})
    return number.quantize(Decimal('0.01'))


class GradingGradeView(APIView):
    permission_classes = [CanGrade]

    def post(self, request, submission_id):
        qs, _org, _type = _submissions(request)
        visible = get_object_or_404(qs, pk=submission_id)  # 404 across organizations

        data = request.data
        raw_criteria = data.get('criteria') or {}
        if not isinstance(raw_criteria, dict):
            raise ValidationError({'criteria': 'must be an object'})
        allowed = CRITERIA_BY_TYPE[visible.submission_type]
        unknown = sorted(set(raw_criteria) - set(allowed))
        if unknown:
            raise ValidationError(
                {'criteria': f'unknown criteria {unknown}; allowed {list(allowed)}'}
            )
        criteria = {k: float(_score(v, f'criteria.{k}')) for k, v in raw_criteria.items()}

        if data.get('score') is not None:
            score = _score(data['score'], 'score')
        elif criteria:
            score = _score(sum(criteria.values()) / len(criteria), 'score')
        else:
            raise ValidationError({'score': 'score or criteria is required'})
        comment = str(data.get('comment') or '')
        if len(comment) > MAX_COMMENT:
            raise ValidationError({'comment': f'at most {MAX_COMMENT} characters'})

        with transaction.atomic():
            sub = OpenEndedSubmission.objects.select_for_update().get(pk=visible.pk)
            # The teacher's score is final and overrides the AI estimate (re-grading replaces it).
            sub.human_score = score
            sub.human_criteria = criteria
            sub.human_notes = comment
            sub.human_reviewer = request.user
            sub.human_completed_at = timezone.now()
            sub.status = OpenEndedSubmission.Status.HUMAN_APPROVED
            sub.save(
                update_fields=[
                    'human_score',
                    'human_criteria',
                    'human_notes',
                    'human_reviewer',
                    'human_completed_at',
                    'status',
                ]
            )
        sub = OpenEndedSubmission.objects.select_related('question_version').get(pk=sub.pk)
        return Response(_item(sub, full=True))


class CalibrationView(APIView):
    permission_classes = [CanViewCalibration]

    def get(self, request):
        qs, org_id, sub_type = _submissions(request)
        exam_type = next((k for k, v in EXAM_TYPES.items() if v == sub_type), None)
        return Response(cached_calibration(qs, org_id=org_id, exam_type=exam_type))
