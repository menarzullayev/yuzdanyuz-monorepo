"""Serializers for sittings, results and certificates."""

from rest_framework import serializers

from .sitting_models import Certificate, ExamSitting, GradeScale, SittingItem, SittingResult


class GradeScaleSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeScale
        fields = ['id', 'name', 'max_score', 'midpoint_ability', 'points_per_logit', 'bands']


class SittingCreateSerializer(serializers.Serializer):
    mock_exam = serializers.UUIDField()
    title = serializers.CharField(max_length=255)
    opens_at = serializers.DateTimeField()
    closes_at = serializers.DateTimeField()
    grade_scale = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if attrs['closes_at'] <= attrs['opens_at']:
            raise serializers.ValidationError({'closes_at': 'closes_at must be after opens_at.'})
        return attrs


class SittingSerializer(serializers.ModelSerializer):
    grade_scale = GradeScaleSerializer(read_only=True)
    result_count = serializers.SerializerMethodField()

    class Meta:
        model = ExamSitting
        fields = [
            'id',
            'mock_exam',
            'title',
            'opens_at',
            'closes_at',
            'status',
            'calculated_at',
            'published_at',
            'calibration',
            'grade_scale',
            'result_count',
        ]
        read_only_fields = fields

    def get_result_count(self, obj) -> int:
        return obj.results.count()


class SittingItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = SittingItem
        fields = ['question_version', 'difficulty', 'correct_count', 'attempt_count']


class StaffResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = SittingResult
        fields = [
            'id',
            'attempt',
            'user',
            'raw_score',
            'max_raw_score',
            'ability',
            'ability_se',
            'scaled_score',
            'grade',
            'certificate_eligible',
        ]


class StudentResultSerializer(serializers.ModelSerializer):
    """What a student may see — only ever built for PUBLISHED sittings."""

    sitting = serializers.UUIDField(source='sitting_id')
    sitting_title = serializers.CharField(source='sitting.title')
    certificate_number = serializers.SerializerMethodField()

    class Meta:
        model = SittingResult
        fields = [
            'id',
            'sitting',
            'sitting_title',
            'raw_score',
            'max_raw_score',
            'scaled_score',
            'grade',
            'certificate_number',
        ]

    def get_certificate_number(self, obj):
        cert = getattr(obj, 'certificate', None)
        return cert.number if cert else None


class CertificateSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)

    class Meta:
        model = Certificate
        fields = [
            'id',
            'number',
            'sitting',
            'scaled_score',
            'grade',
            'issued_at',
            'valid_until',
            'revoked_at',
            'status',
        ]
