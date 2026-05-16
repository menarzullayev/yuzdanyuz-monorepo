"""
Task 6 — AI Diagnostika REST API.

Endpoints:
  GET  /api/intelligence/skills/mastery/         user'ning skill profile
  GET  /api/intelligence/skills/recommendations/ savol tavsiyasi
  POST /api/intelligence/tutor/generate/         AI Tutor trigger (Celery)
  GET  /api/intelligence/tutor/latest/           so'nggi AIFeedback
  POST /api/intelligence/openended/submit/       essay/audio jo'natish
  GET  /api/intelligence/openended/<id>/         score + status
  POST /api/intelligence/openended/<id>/qa/      admin tasdiqlash
"""

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import QuestionVersion

from .models import AIFeedback, OpenEndedSubmission
from .services import get_user_summary, recommend_questions
from .tasks import (
    compute_summary_hash,
    evaluate_openended_submission,
    generate_ai_tutor_feedback,
)

# ─── Mastery / Recommendations ───────────────────────────────────────────────


class SkillMasteryView(APIView):
    def get(self, request):
        return Response(get_user_summary(request.user))


class SkillRecommendationsView(APIView):
    def get(self, request):
        try:
            limit = int(request.query_params.get('limit', 10))
        except (TypeError, ValueError):
            raise ValidationError({'limit': 'integer'}) from None
        if limit <= 0 or limit > 50:
            raise ValidationError({'limit': "1..50 oralig'ida"})

        questions = recommend_questions(request.user, limit=limit)
        return Response(
            {
                'count': len(questions),
                'questions': [
                    {
                        'id': str(q.id),
                        'subject_id': str(q.subject_id) if q.subject_id else None,
                        'type': q.type,
                    }
                    for q in questions
                ],
            }
        )


# ─── AI Tutor ────────────────────────────────────────────────────────────────


class GenerateTutorView(APIView):
    def post(self, request):
        summary = get_user_summary(request.user)
        prompt_hash = compute_summary_hash(summary)

        existing = (
            AIFeedback.objects.filter(
                user=request.user, prompt_hash=prompt_hash, status=AIFeedback.Status.READY
            )
            .order_by('-completed_at')
            .first()
        )
        if existing:
            return Response(_serialize_feedback(existing), status=status.HTTP_200_OK)

        fb = AIFeedback.objects.create(
            user=request.user,
            summary=summary,
            prompt_hash=prompt_hash,
            status=AIFeedback.Status.PENDING,
        )
        generate_ai_tutor_feedback.delay(str(fb.id))
        # Eager mode (test): task allaqachon ishladi → refresh
        fb.refresh_from_db()
        return Response(_serialize_feedback(fb), status=status.HTTP_202_ACCEPTED)


class LatestTutorView(APIView):
    def get(self, request):
        fb = AIFeedback.objects.filter(user=request.user).order_by('-requested_at').first()
        if fb is None:
            return Response({'detail': "Hech qanday feedback yo'q."}, status=404)
        return Response(_serialize_feedback(fb))


def _serialize_feedback(fb: AIFeedback) -> dict:
    return {
        'id': str(fb.id),
        'status': fb.status,
        'summary': fb.summary,
        'content': fb.content,
        'error': fb.error,
        'requested_at': fb.requested_at,
        'completed_at': fb.completed_at,
    }


# ─── Open-Ended Submission ───────────────────────────────────────────────────


class OpenEndedSubmitView(APIView):
    def post(self, request):
        qv_id = request.data.get('question_version')
        sub_type = request.data.get('submission_type', 'essay')
        content = request.data.get('content', '')

        if not qv_id:
            raise ValidationError({'question_version': 'required'})
        if sub_type not in ('essay', 'audio'):
            raise ValidationError({'submission_type': "'essay' yoki 'audio'"})
        if sub_type == 'essay' and not content.strip():
            raise ValidationError({'content': "essay bo'sh bo'lmasligi kerak"})

        qv = get_object_or_404(QuestionVersion, pk=qv_id)

        # OpenEnded faqat OE (essay) yoki FU (fayl) savollarga qo'llaniladi.
        # Aks holda Anthropic API'ga keraksiz pul ketadi va AI single-choice
        # savolga noto'g'ri rubrika bilan javob beradi (S13 bug, 2026-05-16).
        from apps.catalog.models import Question

        allowed_types = (Question.Type.OPEN_ENDED, Question.Type.FILE_UPLOAD)
        if qv.question.type not in allowed_types:
            raise ValidationError(
                {
                    'question_version': (
                        f"Savol type'i '{qv.question.type}' OpenEnded uchun mos emas. "
                        f'Faqat OE (Essay) yoki FU (File upload) qabul qilinadi.'
                    )
                }
            )

        sub = OpenEndedSubmission.objects.create(
            user=request.user,
            question_version=qv,
            submission_type=sub_type,
            content=content,
            status=OpenEndedSubmission.Status.PENDING,
        )
        evaluate_openended_submission.delay(str(sub.id))
        sub.refresh_from_db()
        return Response(_serialize_submission(sub), status=status.HTTP_202_ACCEPTED)


class OpenEndedDetailView(APIView):
    def get(self, request, submission_id):
        sub = get_object_or_404(OpenEndedSubmission, pk=submission_id)
        if sub.user_id != request.user.id and not request.user.is_staff:
            raise PermissionDenied('Bu sizning submission emas.')
        return Response(_serialize_submission(sub))


class OpenEndedQAView(APIView):
    def post(self, request, submission_id):
        if not (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied('Faqat staff/admin QA qila oladi.')

        sub = get_object_or_404(OpenEndedSubmission, pk=submission_id)
        action = request.data.get('action')
        if action not in ('approve', 'dispute'):
            raise ValidationError({'action': "'approve' yoki 'dispute'"})

        sub.human_reviewer = request.user
        sub.human_score = request.data.get('score')
        sub.human_notes = request.data.get('notes', '')
        sub.human_completed_at = timezone.now()
        sub.status = (
            OpenEndedSubmission.Status.HUMAN_APPROVED
            if action == 'approve'
            else OpenEndedSubmission.Status.DISPUTED
        )
        sub.save(
            update_fields=[
                'human_reviewer',
                'human_score',
                'human_notes',
                'human_completed_at',
                'status',
            ]
        )
        return Response(_serialize_submission(sub))


def _serialize_submission(sub: OpenEndedSubmission) -> dict:
    return {
        'id': str(sub.id),
        'submission_type': sub.submission_type,
        'status': sub.status,
        'content': sub.content[:500] + '...' if len(sub.content) > 500 else sub.content,
        'ai_score': float(sub.ai_score) if sub.ai_score is not None else None,
        'ai_feedback': sub.ai_feedback,
        'human_score': float(sub.human_score) if sub.human_score is not None else None,
        'human_notes': sub.human_notes,
        'submitted_at': sub.submitted_at,
        'ai_completed_at': sub.ai_completed_at,
        'human_completed_at': sub.human_completed_at,
    }
