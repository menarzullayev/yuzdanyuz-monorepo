from django.core.validators import URLValidator
from rest_framework import serializers

from apps.catalog.models import Subject
from apps.exams.models import MockExam

from .group_models import Group, GroupMember, GroupRole, Lesson

MAX_MATERIALS = 20
_url_validator = URLValidator(schemes=['http', 'https'])


class TenantScopedPKField(serializers.PrimaryKeyRelatedField):
    """PK field whose queryset is built per request.

    A manager queryset created at import time would be bound to "no tenant" (empty);
    evaluating ``model.objects`` lazily keeps the current organization filter.
    """

    def __init__(self, model, **kwargs):
        self._model = model
        super().__init__(queryset=model._default_manager.none(), **kwargs)

    def get_queryset(self):
        return self._model.objects.all()


class GroupSerializer(serializers.ModelSerializer):
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(is_active=True), required=False, allow_null=True
    )
    teachers = serializers.SerializerMethodField()
    student_count = serializers.SerializerMethodField()
    # write-only: usernames of teachers to attach on creation
    teacher_usernames = serializers.ListField(
        child=serializers.CharField(max_length=150),
        write_only=True,
        required=False,
        max_length=20,
    )

    class Meta:
        model = Group
        fields = [
            'id', 'name', 'description', 'subject', 'exam_type', 'is_archived',
            'teachers', 'student_count', 'teacher_usernames', 'created_at',
        ]  # fmt: skip
        read_only_fields = ['id', 'created_at']

    def get_teachers(self, obj):
        return [
            {'id': str(m.user_id), 'username': m.user.username}
            for m in obj.members.all()
            if m.role == GroupRole.TEACHER
        ]

    def get_student_count(self, obj):
        return sum(1 for m in obj.members.all() if m.role == GroupRole.STUDENT)

    def validate_subject(self, value):
        org = self.context['org']
        if value is not None and value.organization_id not in (None, org.id):
            raise serializers.ValidationError('Fan topilmadi.')
        return value

    def validate_name(self, value):
        value = value.strip()
        qs = Group.objects.filter(name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('Bu nomdagi guruh allaqachon mavjud.')
        return value


class MemberSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = GroupMember
        fields = ['id', 'user', 'username', 'full_name', 'role', 'created_at']

    def get_full_name(self, obj):
        return obj.user.get_full_name()


class AddMemberSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=GroupRole.choices, default=GroupRole.STUDENT)


class BulkMembersSerializer(serializers.Serializer):
    usernames = serializers.ListField(
        child=serializers.CharField(max_length=150, allow_blank=True),
        allow_empty=False,
        max_length=500,
    )
    role = serializers.ChoiceField(choices=GroupRole.choices, default=GroupRole.STUDENT)
    all_or_nothing = serializers.BooleanField(default=False)


def validate_materials(value):
    if len(value) > MAX_MATERIALS:
        raise serializers.ValidationError(f"Ko'pi bilan {MAX_MATERIALS} ta material.")
    clean = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise serializers.ValidationError(f"#{i}: obyekt bo'lishi kerak.")
        kind = item.get('kind')
        title = str(item.get('title', '')).strip()
        if not title or len(title) > 200:
            raise serializers.ValidationError(f'#{i}: title 1..200 belgi.')
        if kind == 'link':
            url = str(item.get('url', '')).strip()
            if len(url) > 500:
                raise serializers.ValidationError(f'#{i}: url juda uzun.')
            try:
                _url_validator(url)
            except Exception as exc:
                raise serializers.ValidationError(f"#{i}: noto'g'ri http(s) url.") from exc
            extra = set(item) - {'kind', 'title', 'url'}
            clean.append({'kind': 'link', 'title': title, 'url': url})
        elif kind == 'text':
            body = str(item.get('body', '')).strip()
            if not body or len(body) > 5000:
                raise serializers.ValidationError(f'#{i}: body 1..5000 belgi.')
            extra = set(item) - {'kind', 'title', 'body'}
            clean.append({'kind': 'text', 'title': title, 'body': body})
        else:
            raise serializers.ValidationError(f"#{i}: kind 'link' yoki 'text' bo'lishi kerak.")
        if extra:
            raise serializers.ValidationError(f'#{i}: ruxsat etilmagan maydonlar: {sorted(extra)}')
    return clean


class LessonSerializer(serializers.ModelSerializer):
    # Group.objects is tenant-scoped: another organization's group id does not resolve.
    group = TenantScopedPKField(Group)
    exam = TenantScopedPKField(MockExam, required=False, allow_null=True)
    exam_title = serializers.CharField(source='exam.title', read_only=True, default=None)
    materials = serializers.JSONField(required=False)

    class Meta:
        model = Lesson
        fields = [
            'id', 'group', 'title', 'topic', 'materials', 'scheduled_at', 'exam', 'exam_title',
            'created_at',
        ]  # fmt: skip
        read_only_fields = ['id', 'created_at']

    def validate_materials(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Ro'yxat bo'lishi kerak.")
        return validate_materials(value)

    def validate_exam(self, value):
        org = self.context['org']
        if value is not None and value.organization_id != org.id and not value.is_public:
            raise serializers.ValidationError('Imtihon topilmadi.')
        return value

    def validate_group(self, value):
        if self.instance and value.pk != self.instance.group_id:
            raise serializers.ValidationError("Darsni boshqa guruhga ko'chirib bo'lmaydi.")
        return value


# Annotated queryset helper kept here to avoid N+1 on list
def groups_with_members():
    return Group.objects.prefetch_related('members__user')


__all__ = [
    'AddMemberSerializer', 'BulkMembersSerializer', 'GroupSerializer',
    'LessonSerializer', 'MemberSerializer', 'groups_with_members',
]  # fmt: skip
