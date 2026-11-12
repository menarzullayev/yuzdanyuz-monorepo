"""Arena REST API (mounted under /api/v1/engagement/arena/).

GET/POST  quizzes/                    list visible quizzes / create (exam:create)
GET/PATCH quizzes/<id>/               detail / edit a DRAFT (author or admin)
POST      quizzes/<id>/submit|approve|reject|archive/
GET/POST  quizzes/<id>/play/          resume state / start (or resume) a play
POST      quizzes/<id>/play/answer/   {question_id, selected}  (selected=null skips)
GET       quizzes/<id>/leaderboard/
"""

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import arena_service as svc
from .arena_models import MAX_SECONDS_PER_QUESTION, MIN_SECONDS_PER_QUESTION, Quiz, QuizPlay
from .permissions import has_org_perm, is_org_admin


class QuizWriteSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    seconds_per_question = serializers.IntegerField(
        min_value=MIN_SECONDS_PER_QUESTION, max_value=MAX_SECONDS_PER_QUESTION, required=False
    )
    question_version_ids = serializers.ListField(child=serializers.UUIDField(), required=False)


class AnswerSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    selected = serializers.JSONField(allow_null=True)


class RejectSerializer(serializers.Serializer):
    note = serializers.CharField(allow_blank=True, required=False, default='')


def _quiz_dict(quiz, *, detail=False, include_answers=False):
    data = {
        'id': str(quiz.id),
        'title': quiz.title,
        'description': quiz.description,
        'status': quiz.status,
        'seconds_per_question': quiz.seconds_per_question,
        'question_count': quiz.items.count(),
        'created_by': quiz.created_by_id and str(quiz.created_by_id),
        'created_at': quiz.created_at,
    }
    if include_answers:  # author/admin view only
        data['review_note'] = quiz.review_note
        data['reviewed_at'] = quiz.reviewed_at
    if detail and include_answers:
        items = quiz.items.select_related('question_version__question').order_by('order')
        data['questions'] = [svc.question_payload(i, include_answers=True) for i in items]
    return data


def _org(request):
    return getattr(request, 'org', None)


def _get_visible_quiz(request, pk) -> Quiz:
    # Not visible == 404, so a student can't even learn that a pending quiz exists.
    return get_object_or_404(svc.visible_quizzes(request.user, _org(request)), pk=pk)


def _manages(request, quiz) -> bool:
    return quiz.created_by_id == request.user.id or is_org_admin(request.user, _org(request))


class QuizListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = svc.visible_quizzes(request.user, _org(request)).select_related()
        wanted = request.query_params.get('status')
        if wanted:
            qs = qs.filter(status=wanted)
        quizzes = list(qs[:200])
        admin = is_org_admin(request.user, _org(request))
        return Response(
            [
                _quiz_dict(q, include_answers=admin or q.created_by_id == request.user.id)
                for q in quizzes
            ]
        )

    def post(self, request):
        org = _org(request)
        if org is None or not has_org_perm(request.user, org, 'exam:create'):
            raise PermissionDenied('You cannot create quizzes.')
        ser = QuizWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        if not d.get('title'):
            return Response({'title': ['This field is required.']}, status=400)
        quiz = Quiz.objects.create(
            organization=org,
            title=d['title'],
            description=d.get('description', ''),
            seconds_per_question=d.get('seconds_per_question', 30),
            created_by=request.user,
        )
        try:
            if d.get('question_version_ids'):
                svc.set_questions(quiz, org, d['question_version_ids'])
        except svc.ArenaError as e:
            quiz.delete()
            return Response({'detail': str(e)}, status=e.status)
        return Response(
            _quiz_dict(quiz, detail=True, include_answers=True), status=status.HTTP_201_CREATED
        )


class QuizDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        quiz = _get_visible_quiz(request, pk)
        return Response(_quiz_dict(quiz, detail=True, include_answers=_manages(request, quiz)))

    def patch(self, request, pk):
        quiz = _get_visible_quiz(request, pk)
        org = _org(request)
        if not svc.can_edit(request.user, org, quiz):
            raise PermissionDenied('Only a draft can be edited, by its author or an admin.')
        ser = QuizWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        # status is deliberately not writable here; it only moves through the action endpoints
        for f in ('title', 'description', 'seconds_per_question'):
            if f in d:
                setattr(quiz, f, d[f])
        quiz.save()
        if 'question_version_ids' in d:
            try:
                svc.set_questions(quiz, quiz.organization, d['question_version_ids'])
            except svc.ArenaError as e:
                return Response({'detail': str(e)}, status=e.status)
        return Response(_quiz_dict(quiz, detail=True, include_answers=True))


class QuizActionView(APIView):
    """POST quizzes/<id>/<action>/ — `action` is bound in urls.py."""

    permission_classes = [IsAuthenticated]
    action = None

    def post(self, request, pk):
        quiz = _get_visible_quiz(request, pk)
        org = _org(request)
        try:
            if self.action == 'submit':
                svc.submit_for_review(quiz, request.user, org)
            elif self.action == 'approve':
                svc.approve(quiz, request.user, org)
            elif self.action == 'reject':
                ser = RejectSerializer(data=request.data)
                ser.is_valid(raise_exception=True)
                svc.reject(quiz, request.user, org, ser.validated_data['note'])
            elif self.action == 'archive':
                svc.archive(quiz, request.user, org)
        except svc.ArenaError as e:
            if e.status == 403:
                raise PermissionDenied(str(e)) from e
            return Response({'detail': str(e)}, status=e.status)
        return Response(_quiz_dict(quiz, include_answers=True))


class QuizPlayView(APIView):
    permission_classes = [IsAuthenticated]

    def _play(self, request, pk, create):
        quiz = _get_visible_quiz(request, pk)
        if create:
            return svc.start_play(quiz, request.user, _org(request))
        return get_object_or_404(
            QuizPlay.objects.select_related('quiz'), quiz=quiz, user=request.user
        )

    def get(self, request, pk):
        return Response(svc.play_state(self._play(request, pk, create=False)))

    def post(self, request, pk):
        try:
            play = self._play(request, pk, create=True)
        except svc.ArenaError as e:
            return Response({'detail': str(e)}, status=e.status)
        return Response(svc.play_state(play), status=status.HTTP_200_OK)


class QuizAnswerView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quiz = _get_visible_quiz(request, pk)
        play = get_object_or_404(
            QuizPlay.objects.select_related('quiz'), quiz=quiz, user=request.user
        )
        ser = AnswerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            play, result = svc.answer(
                play, ser.validated_data['question_id'], ser.validated_data['selected']
            )
        except svc.ArenaError as e:
            return Response({'detail': str(e)}, status=e.status)
        return Response({**result, **svc.play_state(play)})


class QuizLeaderboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        quiz = _get_visible_quiz(request, pk)
        return Response(svc.leaderboard(quiz, request.user))
