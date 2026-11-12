"""Typing practice API (mounted under /api/v1/engagement/typing/).

GET  texts/?language=            active practice texts
POST runs/                       submit {text_id, duration_ms, typed_chars, errors}
GET  runs/?language=             own history (latest 50)
GET  best/?language=             personal best (clean runs only)
GET  leaderboard/?language=      top 20 per language + own rank
"""

from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import typing_service as svc
from .typing_models import TypingRun, TypingText

HISTORY_LIMIT = 50


def _language(request, required=False):
    lang = request.query_params.get('language')
    if not lang:
        if required:
            raise ValidationError({'language': 'This query parameter is required.'})
        return None
    if lang not in TypingText.Language.values:
        raise ValidationError({'language': f'Must be one of {TypingText.Language.values}.'})
    return lang


def _run_dict(r):
    return {
        'id': str(r.id),
        'text_id': str(r.text_id),
        'language': r.language,
        'duration_ms': r.duration_ms,
        'typed_chars': r.typed_chars,
        'correct_chars': r.correct_chars,
        'errors': r.errors,
        'wpm': r.wpm,
        'accuracy': r.accuracy,
        'created_at': r.created_at,
    }


class TextListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = TypingText.objects.filter(is_active=True)
        lang = _language(request)
        if lang:
            qs = qs.filter(language=lang)
        return Response(
            [
                {'id': str(t.id), 'language': t.language, 'title': t.title, 'body': t.body}
                for t in qs
            ]
        )


class RunSerializer(serializers.Serializer):
    # wpm / accuracy sent by a client are not declared, so they are dropped on purpose.
    text_id = serializers.UUIDField()
    duration_ms = serializers.IntegerField(min_value=0)
    typed_chars = serializers.IntegerField(min_value=0)
    errors = serializers.IntegerField(min_value=0)


class RunListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = TypingRun.objects.filter(user=request.user)
        lang = _language(request)
        if lang:
            qs = qs.filter(language=lang)
        return Response([_run_dict(r) for r in qs[:HISTORY_LIMIT]])

    def post(self, request):
        ser = RunSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        text = get_object_or_404(TypingText, pk=d['text_id'], is_active=True)
        try:
            correct, wpm, accuracy = svc.score_run(
                text_length=len(text.body),
                duration_ms=d['duration_ms'],
                typed_chars=d['typed_chars'],
                errors=d['errors'],
            )
        except svc.TypingRejected as e:
            raise ValidationError({'detail': str(e)}) from e
        previous = svc.personal_best(request.user, text.language)
        run = TypingRun.objects.create(
            user=request.user,
            text=text,
            language=text.language,
            duration_ms=d['duration_ms'],
            typed_chars=d['typed_chars'],
            correct_chars=correct,
            errors=d['errors'],
            wpm=wpm,
            accuracy=accuracy,
        )
        is_best = accuracy >= svc.TYPING_MIN_ACCURACY_FOR_BEST and (
            previous is None or wpm > previous.wpm
        )
        return Response({**_run_dict(run), 'is_personal_best': is_best}, status=201)


class BestView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lang = _language(request, required=True)
        best = svc.personal_best(request.user, lang)
        return Response(
            {
                'language': lang,
                'best': _run_dict(best) if best else None,
                'runs_count': TypingRun.objects.filter(user=request.user, language=lang).count(),
            }
        )


class LeaderboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(svc.leaderboard(_language(request, required=True), request.user))
