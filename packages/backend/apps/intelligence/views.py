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

from decimal import Decimal

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import QuestionVersion

from .mistakes import STAGE_INTERVAL_DAYS, NotDueError, ReviewError, review_card
from .models import AIFeedback, MistakeCard, OpenEndedSubmission
from .serializers import MistakeCardSerializer, MistakeReviewSerializer
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


MAX_AUDIO_BYTES = 25 * 1024 * 1024
AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.ogg', '.oga', '.webm', '.flac', '.mp4')


def _validate_audio(audio, transcript: str) -> None:
    """Speaking needs an audio file (or, for tests/imports, a ready transcript)."""
    if audio is None:
        if not transcript.strip():
            raise ValidationError({'audio': 'audio file is required'})
        return
    if audio.size > MAX_AUDIO_BYTES:
        raise ValidationError({'audio': 'file is larger than 25 MB'})
    if not audio.name.lower().endswith(AUDIO_EXTENSIONS):
        raise ValidationError({'audio': f'allowed formats: {", ".join(AUDIO_EXTENSIONS)}'})


MAX_ESSAY_CHARS = 20_000  # also bounds what is sent to the LLM


class OpenEndedSubmitRequestSerializer(serializers.Serializer):
    question_version = serializers.UUIDField()
    submission_type = serializers.ChoiceField(choices=('essay', 'audio'), default='essay')
    content = serializers.CharField(
        max_length=MAX_ESSAY_CHARS, allow_blank=True, default='', trim_whitespace=False
    )


class OpenEndedQARequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=('approve', 'dispute'))
    score = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal('0'),
        max_value=Decimal('100'),
        required=False,
        allow_null=True,
        default=None,
    )
    notes = serializers.CharField(max_length=5000, allow_blank=True, default='')


class OpenEndedSubmitView(APIView):
    def post(self, request):
        ser = OpenEndedSubmitRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qv_id = ser.validated_data['question_version']
        sub_type = ser.validated_data['submission_type']
        content = ser.validated_data['content']
        audio = request.FILES.get('audio')

        if sub_type == 'essay' and not content.strip():
            raise ValidationError({'content': "essay bo'sh bo'lmasligi kerak"})
        if sub_type == 'audio':
            _validate_audio(audio, content)

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
            audio_file=audio if sub_type == 'audio' else None,
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
        ser = OpenEndedQARequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        action = ser.validated_data['action']

        sub.human_reviewer = request.user
        sub.human_score = ser.validated_data['score']
        sub.human_notes = ser.validated_data['notes']
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
    # The AI score is an estimate until a teacher sets the final score; students must be able
    # to tell the two apart, so `score` says which one it is.
    if sub.human_score is not None:
        score = {'value': float(sub.human_score), 'is_final': True, 'label': 'final'}
    elif sub.ai_score is not None:
        score = {'value': float(sub.ai_score), 'is_final': False, 'label': 'AI estimate'}
    else:
        score = None
    feedback = sub.ai_feedback or {}
    return {
        'id': str(sub.id),
        'submission_type': sub.submission_type,
        'status': sub.status,
        'content': sub.content[:500] + '...' if len(sub.content) > 500 else sub.content,
        'score': score,
        'ai_score': float(sub.ai_score) if sub.ai_score is not None else None,
        'ai_feedback': sub.ai_feedback,
        'ai_not_assessed': feedback.get('not_assessed', []),
        'human_score': float(sub.human_score) if sub.human_score is not None else None,
        'human_criteria': sub.human_criteria or {},
        'human_notes': sub.human_notes,
        'submitted_at': sub.submitted_at,
        'ai_completed_at': sub.ai_completed_at,
        'human_completed_at': sub.human_completed_at,
    }


# ─── Mistake notebook (xatolar daftari) ──────────────────────────────────────


def _user_cards(request):
    # TenantManager scopes to the current org; user filter scopes to the owner.
    return MistakeCard.objects.filter(user=request.user)


class MistakeDueListView(generics.ListAPIView):
    """GET /api/intelligence/mistakes/due/ — due, not yet mastered cards (paginated)."""

    serializer_class = MistakeCardSerializer

    def get_queryset(self):
        return (
            _user_cards(self.request)
            .filter(mastered_at__isnull=True, due_at__lte=timezone.now())
            .select_related('question')
            .prefetch_related('question__versions')
            .order_by('due_at', 'id')
        )


class MistakeSummaryView(APIView):
    """GET /api/intelligence/mistakes/summary/ — counts per stage, due and mastered."""

    def get(self, request):
        now = timezone.now()
        active = _user_cards(request).filter(mastered_at__isnull=True)
        totals = dict(active.values_list('stage').annotate(n=Count('id')))
        due = dict(active.filter(due_at__lte=now).values_list('stage').annotate(n=Count('id')))
        return Response(
            {
                'stages': [
                    {
                        'stage': stage,
                        'interval_days': days,
                        'total': totals.get(stage, 0),
                        'due': due.get(stage, 0),
                    }
                    for stage, days in STAGE_INTERVAL_DAYS.items()
                ],
                'due_total': sum(due.values()),
                'active_total': sum(totals.values()),
                'mastered': _user_cards(request).filter(mastered_at__isnull=False).count(),
            }
        )


class MistakeReviewView(APIView):
    """POST /api/intelligence/mistakes/<card_id>/review/ {selected} — grade a review."""

    def post(self, request, card_id):
        ser = MistakeReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            result = review_card(card_id, request.user, ser.validated_data['selected'])
        except MistakeCard.DoesNotExist:
            raise NotFound('Card not found.') from None  # also hides other users' cards
        except NotDueError as e:
            raise ValidationError({'detail': str(e), 'code': 'not_due'}) from None
        except ReviewError as e:
            raise ValidationError({'detail': str(e)}) from None

        card = result['card']
        return Response(
            {
                'correct': result['correct'],
                'correct_option_ids': result['correct_option_ids'],
                'explanation': result['explanation'],
                'stage': card.stage,
                'due_at': card.due_at,
                'lapses': card.lapses,
                'mastered': card.mastered_at is not None,
            }
        )
