"""
Task 4 — Exam Engine DRF serializers.

Serializer'lar minimal va aniq: faqat front-end'ga zarur maydonlar.
Admin/internal field'lar (organization, created_by, internal IDs) odatda
yashirin yoki read-only.

Authorization: serializerlar permission tekshirmaydi — bu view qatlami ishi.
Tenant isolation: TenantManager / PublicOrTenantManager avtomat hal qiladi
(view set_current_org middleware orqali set qilingan org context'da ishlaydi).
"""

from rest_framework import serializers

from apps.catalog.models import QuestionVersion

from .models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    PracticeSession,
    QuestionDispute,
    UserAnswer,
)

# ─── QuestionVersion (lite — exam ichida ko'rsatish uchun) ────────────────────


class QuestionVersionInExamSerializer(serializers.ModelSerializer):
    """Exam ichida ko'rsatiladigan QuestionVersion — to'g'ri javob ko'rinmaydi."""

    class Meta:
        model = QuestionVersion
        fields = ['id', 'content', 'options']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # SC/MC: options dan is_correct'ni olib tashlash (front-end'ga ko'rsatilmasin)
        if isinstance(data.get('options'), list):
            data['options'] = [
                {k: v for k, v in opt.items() if k != 'is_correct'} for opt in data['options']
            ]
        return data


# ─── MockExam ─────────────────────────────────────────────────────────────────


class MockExamListSerializer(serializers.ModelSerializer):
    """List view — savollarsiz yengil version."""

    question_count = serializers.SerializerMethodField()

    class Meta:
        model = MockExam
        fields = [
            'id',
            'title',
            'description',
            'duration_minutes',
            'scheduled_at',
            'closes_at',
            'status',
            'is_public',
            'question_count',
        ]

    def get_question_count(self, obj):
        return obj.question_links.count()


class MockExamDetailSerializer(MockExamListSerializer):
    """Detail view — savollar ham qo'shilgan (faqat in-progress attempt'lar uchun)."""

    questions = serializers.SerializerMethodField()

    class Meta(MockExamListSerializer.Meta):
        fields = [*MockExamListSerializer.Meta.fields, 'questions']

    def get_questions(self, obj):
        links = obj.question_links.select_related('question_version').order_by('order')
        return [
            {
                'order': link.order,
                'points': link.points,
                'question': QuestionVersionInExamSerializer(link.question_version).data,
            }
            for link in links
        ]


# ─── ExamAttempt ──────────────────────────────────────────────────────────────


class ExamAttemptSerializer(serializers.ModelSerializer):
    """In-progress yoki yakunlangan attempt — user o'zining attempt'ini ko'radi."""

    exam_title = serializers.CharField(source='exam.title', read_only=True)
    duration_minutes = serializers.IntegerField(source='exam.duration_minutes', read_only=True)

    class Meta:
        model = ExamAttempt
        fields = [
            'id',
            'exam',
            'exam_title',
            'duration_minutes',
            'status',
            'started_at',
            'submitted_at',
            'score',
            'correct_count',
            'total_points',
            'strikes',
            'cancel_reason',
        ]
        read_only_fields = fields  # Faqat o'qish — modifikatsiya view action'larida


class StartAttemptResponseSerializer(serializers.Serializer):
    """Mock'ni boshlaganda qaytadigan payload — attempt ID + savollar."""

    attempt = ExamAttemptSerializer(read_only=True)
    exam = MockExamDetailSerializer(read_only=True)


# ─── UserAnswer ───────────────────────────────────────────────────────────────


class UserAnswerSubmitSerializer(serializers.ModelSerializer):
    """User javob jo'natganda — selected va time_spent qabul qilinadi."""

    question_version = serializers.PrimaryKeyRelatedField(queryset=QuestionVersion.objects.all())

    class Meta:
        model = UserAnswer
        fields = ['question_version', 'selected', 'time_spent_seconds']


class UserAnswerReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAnswer
        fields = [
            'id',
            'question_version',
            'selected',
            'is_correct',
            'auto_correct',
            'time_spent_seconds',
            'answered_at',
        ]
        read_only_fields = fields


# ─── PracticeSession ──────────────────────────────────────────────────────────


class PracticeSessionCreateSerializer(serializers.ModelSerializer):
    """User practice sessiya boshlaganda — blueprint qabul qilinadi."""

    class Meta:
        model = PracticeSession
        fields = ['blueprint']

    def validate_blueprint(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError("Blueprint bo'sh bo'lmagan list bo'lishi kerak.")
        for item in value:
            if not isinstance(item, dict) or 'count' not in item:
                raise serializers.ValidationError(
                    "Har bir blueprint item dict bo'lib, 'count' kaliti talab qilinadi."
                )
            if not isinstance(item['count'], int) or item['count'] <= 0 or item['count'] > 100:
                raise serializers.ValidationError("'count' 1..100 oralig'ida bo'lishi kerak.")
        return value


class PracticeSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PracticeSession
        fields = [
            'id',
            'blueprint',
            'status',
            'started_at',
            'ended_at',
            'correct_count',
            'total_count',
        ]
        read_only_fields = fields


# ─── QuestionDispute ──────────────────────────────────────────────────────────


class QuestionDisputeCreateSerializer(serializers.ModelSerializer):
    """User savol haqida shikoyat yuboradi."""

    class Meta:
        model = QuestionDispute
        fields = ['question_version', 'reason', 'note']


class QuestionDisputeReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionDispute
        fields = [
            'id',
            'attempt',
            'question_version',
            'reason',
            'note',
            'status',
            'created_at',
        ]
        read_only_fields = fields


# ─── AntiCheatEvent ───────────────────────────────────────────────────────────


class AntiCheatEventCreateSerializer(serializers.ModelSerializer):
    """Frontend browser lock voqeasini jo'natadi (strike count'ni view oshiradi)."""

    class Meta:
        model = AntiCheatEvent
        fields = ['event_type', 'metadata']
