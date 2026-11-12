"""
Task 4 — Exam Engine REST API views.

Endpoint xulosasi:
  GET    /api/exams/mocks/                    — list mocks (PUBLISHED, available)
  GET    /api/exams/mocks/{id}/                — detail (savollar attempt boshlangan bo'lsa)
  POST   /api/exams/mocks/{id}/start/           — yangi attempt yaratish
  GET    /api/exams/attempts/{id}/              — joriy attempt holati
  POST   /api/exams/attempts/{id}/answer/       — bitta savolga javob jo'natish
  POST   /api/exams/attempts/{id}/submit/       — yakunlash + score finalize
  POST   /api/exams/attempts/{id}/dispute/      — savol shikoyati
  POST   /api/exams/attempts/{id}/anticheat/    — browser lock event log
  POST   /api/exams/practice/                   — yangi practice sessiya
  GET    /api/exams/practice/{id}/              — joriy session holati
  POST   /api/exams/practice/{id}/answer/       — javob jo'natish
  POST   /api/exams/practice/{id}/finish/       — sessiyani yakunlash

Permissions:
  - Foydalanuvchi authenticated bo'lishi kerak (DRF default)
  - Attempt/Session — faqat o'z user'iniki (404 to others)
  - Tenant isolation — TenantManager / PublicOrTenantManager avtomat

Eslatma: WebSocket consumer (PR #29) heartbeat va 3-strike auto-cancel'ni
real-time hal qiladi. Bu view'lar HTTP fallback va admin/UI uchun.
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from core.config import get_org_setting

from .models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    MockExamQuestion,
    PracticeSession,
    QuestionDispute,
    UserAnswer,
)
from .onboarding_gate import missing_for, require_onboarding
from .serializers import (
    AntiCheatEventCreateSerializer,
    ExamAttemptSerializer,
    MockExamListSerializer,
    PracticeSessionCreateSerializer,
    PracticeSessionSerializer,
    QuestionDisputeCreateSerializer,
    QuestionDisputeReadSerializer,
    StartAttemptResponseSerializer,
    UserAnswerReadSerializer,
    UserAnswerSubmitSerializer,
)
from .tasks import finalize_attempt_score

# ─── Helpers ──────────────────────────────────────────────────────────────────


# ISSUE-404: legacy default, kept as fallback. Use get_org_setting() per-attempt.
MAX_STRIKES = 3


def _get_user_attempt_or_404(user, attempt_id):
    """Foydalanuvchi o'z attempt'ini olishi yoki 404."""
    attempt = get_object_or_404(ExamAttempt, pk=attempt_id)
    if attempt.user_id != user.id:
        raise PermissionDenied('Bu sizning attempt emas.')
    return attempt


RESULT_FIELDS = ('score', 'correct_count', 'total_points')


def _attempt_payload(user, attempt) -> dict:
    """Attempt JSON; the result fields are withheld until onboarding is complete."""
    data = ExamAttemptSerializer(attempt).data
    missing = missing_for(user)
    if missing:
        data = {k: (None if k in RESULT_FIELDS else v) for k, v in data.items()}
        data['onboarding_required'] = True
        data['missing'] = missing
    return data


def _get_user_session_or_404(user, session_id):
    session = get_object_or_404(PracticeSession, pk=session_id)
    if session.user_id != user.id:
        raise PermissionDenied('Bu sizning session emas.')
    return session


def _practisable(question_version, session) -> bool:
    """Practice grading answers "is this option correct?" for any id it is given,
    so it must stay away from questions a student could meet in a real exam:
    only the session's own organization, never quarantined, and never pinned to
    a mock that is still being prepared, scheduled or open."""
    if question_version.is_quarantined:
        return False
    from apps.catalog.models import Question

    if not Question._base_manager.filter(
        pk=question_version.question_id, organization_id=session.organization_id
    ).exists():
        return False
    return not MockExamQuestion.objects.filter(
        question_version=question_version,
        mock_exam__status__in=(MockExam.Status.DRAFT, MockExam.Status.PUBLISHED),
        mock_exam__closes_at__gt=timezone.now(),
    ).exists()


def _evaluate_answer(question_version, selected) -> bool:
    """
    Single-Choice: selected = {id: int}, options'da is_correct=True bor variantni topish.
    Multi-Choice: selected = [id, ...], to'liq teng kelish.
    Boshqa type'lar: hozircha False (Task 4'ning kelajakdagi qismida widen).
    """
    options = question_version.options or []
    if not isinstance(options, list):
        return False
    correct_ids = {o['id'] for o in options if o.get('is_correct')}
    if isinstance(selected, dict) and 'id' in selected:
        return {selected['id']} == correct_ids
    if isinstance(selected, list):
        return set(selected) == correct_ids
    return False


# ─── MockExam list/detail ─────────────────────────────────────────────────────


class MockExamListView(generics.ListAPIView):
    """
    GET /api/exams/mocks/
    PUBLISHED holatdagi (current org + public) mocks. Yopilgan vaqtdan oldin va
    ochiq oynada bo'lganlari ko'rinadi.
    """

    serializer_class = MockExamListSerializer

    def get_queryset(self):
        now = timezone.now()
        return MockExam.objects.filter(
            status=MockExam.Status.PUBLISHED, closes_at__gte=now
        ).order_by('-scheduled_at')


class MockExamDetailView(generics.RetrieveAPIView):
    """
    GET /api/exams/mocks/{id}/
    Detail (savollarsiz) — savollar faqat attempt start'dan keyin /attempts/{id}/da.
    """

    serializer_class = MockExamListSerializer
    queryset = MockExam.objects.all()


def _enforce_exam_access(user, mock, attempt) -> None:
    """Billing hook: a new attempt on a public (platform) mock must be covered by free
    attempts, a tariff, a B2B key or a pass. Off unless BILLING_ENFORCE_EXAM_ACCESS."""
    from django.conf import settings

    if not getattr(settings, 'BILLING_ENFORCE_EXAM_ACCESS', False) or not mock.is_public:
        return
    from apps.commerce.access_service import consume_access
    from apps.commerce.billing_views import AccessDenied

    decision = consume_access(user, mock.exam_type, reference=str(attempt.id))
    if not decision.allowed:
        raise AccessDenied(decision)


# ─── Start attempt ────────────────────────────────────────────────────────────


class StartAttemptView(APIView):
    """
    POST /api/exams/mocks/{mock_id}/start/

    Idempotent: agar user'da bu mock uchun in-progress attempt bor bo'lsa, uni qaytaradi.
    Aks holda yangi yaratadi.

    Validation:
      - Mock PUBLISHED bo'lishi
      - Mock ochiq oynada (scheduled_at <= now <= closes_at)
      - Foydalanuvchi shu mock'ga kirish huquqiga ega (PublicOrTenantManager)
    """

    def post(self, request, mock_id):
        now = timezone.now()
        mock = get_object_or_404(MockExam, pk=mock_id)

        if mock.status != MockExam.Status.PUBLISHED:
            raise ValidationError("Mock hali e'lon qilinmagan yoki yopilgan.")
        if not (mock.scheduled_at <= now <= mock.closes_at):
            raise ValidationError('Mock ochiq oynada emas.')

        # Atomic: if the paywall denies, the freshly created attempt is rolled back.
        with transaction.atomic():
            attempt, created = ExamAttempt.objects.get_or_create(
                exam=mock,
                user=request.user,
                defaults={'organization': mock.organization},
            )
            if created:
                _enforce_exam_access(request.user, mock, attempt)

        if attempt.status not in {ExamAttempt.Status.IN_PROGRESS}:
            raise ValidationError(
                f'Sizda allaqachon yakunlangan attempt bor (status={attempt.status}).'
            )

        return Response(
            StartAttemptResponseSerializer(
                {'attempt': attempt, 'exam': mock}, context={'request': request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


# ─── Attempt detail / answer / submit ─────────────────────────────────────────


class AttemptDetailView(APIView):
    """GET /api/exams/attempts/{id}/  — joriy attempt holati"""

    def get(self, request, attempt_id):
        attempt = _get_user_attempt_or_404(request.user, attempt_id)
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            require_onboarding(request.user)  # a finished attempt shows the result
        return Response(ExamAttemptSerializer(attempt).data)


class SubmitAnswerView(APIView):
    """
    POST /api/exams/attempts/{id}/answer/
    Bitta savolga javob (yangi yoki update — answer per question unique).
    """

    def post(self, request, attempt_id):
        attempt = _get_user_attempt_or_404(request.user, attempt_id)
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            raise ValidationError(f'Attempt holati: {attempt.status} — javob qabul qilinmaydi.')

        ser = UserAnswerSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qv = ser.validated_data['question_version']

        # Savol shu mock'ga tegishli bo'lishi kerak
        if not attempt.exam.question_links.filter(question_version=qv).exists():
            raise ValidationError("Bu savol shu mock imtihonida yo'q.")

        is_correct = _evaluate_answer(qv, ser.validated_data['selected'])

        with transaction.atomic():
            answer, _ = UserAnswer.objects.update_or_create(
                attempt=attempt,
                question_version=qv,
                defaults={
                    'organization': attempt.organization,
                    'selected': ser.validated_data['selected'],
                    'is_correct': is_correct,
                    'time_spent_seconds': ser.validated_data.get('time_spent_seconds', 0),
                },
            )

        return Response(UserAnswerReadSerializer(answer).data, status=status.HTTP_201_CREATED)


class SubmitAttemptView(APIView):
    """
    POST /api/exams/attempts/{id}/submit/
    Yakunlash + finalize_attempt_score.delay(). Idempotent.
    """

    def post(self, request, attempt_id):
        attempt = _get_user_attempt_or_404(request.user, attempt_id)
        if attempt.status == ExamAttempt.Status.SUBMITTED:
            return Response(_attempt_payload(request.user, attempt))
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            raise ValidationError(f"Attempt holati: {attempt.status} — yakunlab bo'lmaydi.")

        attempt.status = ExamAttempt.Status.SUBMITTED
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=['status', 'submitted_at', 'updated_at'])

        # Score recompute (Celery)
        finalize_attempt_score.delay(str(attempt.id))

        attempt.refresh_from_db()
        return Response(_attempt_payload(request.user, attempt))


class AntiCheatEventView(APIView):
    """
    POST /api/exams/attempts/{id}/anticheat/
    Browser lock event log + strike count.
    3 ta strike → attempt CANCELLED (auto).
    """

    def post(self, request, attempt_id):
        attempt = _get_user_attempt_or_404(request.user, attempt_id)
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            raise ValidationError(f'Attempt holati: {attempt.status}')

        ser = AntiCheatEventCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        with transaction.atomic():
            AntiCheatEvent.objects.create(
                organization=attempt.organization,
                attempt=attempt,
                event_type=ser.validated_data['event_type'],
                metadata=ser.validated_data.get('metadata', {}),
            )

            attempt.strikes = (attempt.strikes or 0) + 1
            update_fields = ['strikes', 'updated_at']

            # ISSUE-404: per-tenant override via Organization.settings.anti_cheat.max_strikes
            max_strikes = get_org_setting(attempt.organization, 'anti_cheat.max_strikes')
            if attempt.strikes >= max_strikes:
                attempt.status = ExamAttempt.Status.CANCELLED
                attempt.cancel_reason = ExamAttempt.CancelReason.TAB_SWITCH
                attempt.submitted_at = timezone.now()
                update_fields += ['status', 'cancel_reason', 'submitted_at']

            attempt.save(update_fields=update_fields)

        return Response(ExamAttemptSerializer(attempt).data)


class FileDisputeView(APIView):
    """POST /api/exams/attempts/{id}/dispute/  — savol shikoyati"""

    def post(self, request, attempt_id):
        attempt = _get_user_attempt_or_404(request.user, attempt_id)

        ser = QuestionDisputeCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qv = ser.validated_data['question_version']

        # Savol shu mock'da bo'lishi
        if not attempt.exam.question_links.filter(question_version=qv).exists():
            raise ValidationError("Bu savol shu mock imtihonida yo'q.")

        dispute, created = QuestionDispute.objects.get_or_create(
            attempt=attempt,
            question_version=qv,
            user=request.user,
            defaults={
                'organization': attempt.organization,
                'reason': ser.validated_data['reason'],
                'note': ser.validated_data.get('note', ''),
            },
        )
        if not created:
            raise ValidationError('Bu savolga sizning shikoyat allaqachon yuborilgan.')

        return Response(QuestionDisputeReadSerializer(dispute).data, status=status.HTTP_201_CREATED)


# ─── PracticeSession ──────────────────────────────────────────────────────────


class PracticeCreateView(generics.CreateAPIView):
    """
    POST /api/exams/practice/
    Body: { "blueprint": [{"topic_id": "uuid?", "count": 10, "difficulty": "hard?"}] }
    """

    serializer_class = PracticeSessionCreateSerializer

    def perform_create(self, serializer):
        # Foydalanuvchi current_org'i kerak — request.org middleware orqali set qilingan
        org = getattr(self.request, 'org', None)
        if org is None:
            raise ValidationError('Tenant context topilmadi.')
        serializer.save(user=self.request.user, organization=org)

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        instance = ser.instance
        return Response(PracticeSessionSerializer(instance).data, status=status.HTTP_201_CREATED)


class PracticeDetailView(generics.RetrieveAPIView):
    serializer_class = PracticeSessionSerializer

    def get_object(self):
        return _get_user_session_or_404(self.request.user, self.kwargs['session_id'])


class PracticeAnswerView(APIView):
    """
    POST /api/exams/practice/{id}/answer/
    Bitta savolga javob — practice session'lar dinamik, savol mock'ka bog'liq emas.
    """

    def post(self, request, session_id):
        session = _get_user_session_or_404(request.user, session_id)
        if session.status != PracticeSession.Status.IN_PROGRESS:
            raise ValidationError(f'Session holati: {session.status}')

        ser = UserAnswerSubmitSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qv = ser.validated_data['question_version']
        if not _practisable(qv, session):
            raise ValidationError({'question_version': 'Bu savol mashq uchun mavjud emas.'})

        is_correct = _evaluate_answer(qv, ser.validated_data['selected'])

        with transaction.atomic():
            answer, _ = UserAnswer.objects.update_or_create(
                session=session,
                question_version=qv,
                defaults={
                    'organization': session.organization,
                    'selected': ser.validated_data['selected'],
                    'is_correct': is_correct,
                    'time_spent_seconds': ser.validated_data.get('time_spent_seconds', 0),
                },
            )

        return Response(UserAnswerReadSerializer(answer).data, status=status.HTTP_201_CREATED)


class PracticeFinishView(APIView):
    """
    POST /api/exams/practice/{id}/finish/
    Sessiyani yakunlash — correct_count va total_count'ni hisoblaydi.
    """

    def post(self, request, session_id):
        session = _get_user_session_or_404(request.user, session_id)
        if session.status == PracticeSession.Status.COMPLETED:
            return Response(PracticeSessionSerializer(session).data)
        if session.status != PracticeSession.Status.IN_PROGRESS:
            raise ValidationError(f'Session holati: {session.status}')

        answers = session.answers.all()
        correct = sum(1 for a in answers if a.is_correct or a.auto_correct)
        total = answers.count()

        session.status = PracticeSession.Status.COMPLETED
        session.ended_at = timezone.now()
        session.correct_count = correct
        session.total_count = total
        session.save(
            update_fields=['status', 'ended_at', 'correct_count', 'total_count', 'updated_at']
        )

        return Response(PracticeSessionSerializer(session).data)
