"""Serializers for the mistake notebook (xatolar daftari)."""

from rest_framework import serializers

from apps.exams.serializers import QuestionVersionInExamSerializer

from .mistakes import current_version
from .models import MistakeCard


class MistakeCardSerializer(serializers.ModelSerializer):
    question_id = serializers.UUIDField(read_only=True)
    type = serializers.CharField(source='question.type', read_only=True)
    subject_id = serializers.UUIDField(source='question.subject_id', read_only=True)
    # is_correct is stripped from options, so the answer is never leaked.
    question = serializers.SerializerMethodField()

    class Meta:
        model = MistakeCard
        fields = [
            'id',
            'question_id',
            'type',
            'subject_id',
            'stage',
            'due_at',
            'last_reviewed_at',
            'lapses',
            'question',
        ]

    def get_question(self, card):
        version = current_version(card.question)
        return QuestionVersionInExamSerializer(version).data if version else None


class MistakeReviewSerializer(serializers.Serializer):
    # SC: {"id": int}, MC: [int, ...] — same shape as exams answers
    selected = serializers.JSONField()
