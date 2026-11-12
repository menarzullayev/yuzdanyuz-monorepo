"""B2B tenant models: Group, GroupMember, Lesson.

All three inherit TenantTimestampMixin (organization FK + TenantManager) and are
protected by RLS (migration 0005).

Linkage note: Group is the canonical "class/cohort" of an organization. Billing
(commerce) currently keeps a free-text group_label on B2B exam keys because no group
model existed; it can now be replaced by an FK to organizations.Group.
"""

from uuid import uuid4

from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.db import models

from core.mixins import TenantTimestampMixin


class Group(TenantTimestampMixin):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=120, verbose_name='Guruh nomi')
    description = models.TextField(blank=True)
    subject = models.ForeignKey(
        'catalog.Subject',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='b2b_groups',
    )
    # Free-form exam target, e.g. 'milliy_sertifikat', 'dtm', 'ielts'
    exam_type = models.CharField(max_length=50, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        verbose_name = 'Guruh'
        verbose_name_plural = 'Guruhlar'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'], name='uniq_group_name_per_org')
        ]

    def __str__(self):
        return f'{self.name} ({self.organization_id})'


class GroupRole(models.TextChoices):
    STUDENT = 'student', "O'quvchi"
    TEACHER = 'teacher', "O'qituvchi"


class GroupMember(TenantTimestampMixin):
    """A user in a group, either as student or as teacher.

    A student may belong to several groups of the same organization. The user must
    hold an ACTIVE Membership of the group's organization (enforced in
    group_services; clean() guards the organization match).
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_memberships',
    )
    role = models.CharField(max_length=10, choices=GroupRole.choices, default=GroupRole.STUDENT)
    added_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        verbose_name = "Guruh a'zosi"
        verbose_name_plural = "Guruh a'zolari"
        ordering = ['created_at']
        constraints = [models.UniqueConstraint(fields=['group', 'user'], name='uniq_group_member')]
        indexes = [models.Index(fields=['organization', 'user'])]

    def __str__(self):
        return f'{self.user_id} in {self.group_id} ({self.role})'

    def clean(self):
        super().clean()
        if (
            self.group_id
            and self.organization_id
            and self.group.organization_id != self.organization_id
        ):
            raise ValidationError({'group': 'Guruh boshqa tashkilotga tegishli.'})


class Lesson(TenantTimestampMixin):
    """A scheduled group lesson. `exam` is only a reference for the UI; assigning it
    creates nothing in the exams app."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField(max_length=200)
    topic = models.CharField(max_length=200, blank=True)
    # [{'kind': 'link', 'title': str, 'url': str} | {'kind': 'text', 'title': str, 'body': str}]
    materials = models.JSONField(default=list, blank=True)
    scheduled_at = models.DateTimeField(db_index=True)
    exam = models.ForeignKey(
        'exams.MockExam',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='group_lessons',
    )
    created_by = models.ForeignKey(
        django_settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        verbose_name = 'Dars'
        verbose_name_plural = 'Darslar'
        ordering = ['scheduled_at']
        indexes = [models.Index(fields=['group', 'scheduled_at'])]

    def __str__(self):
        return f'{self.title} @ {self.scheduled_at:%Y-%m-%d %H:%M}'

    def clean(self):
        super().clean()
        if (
            self.group_id
            and self.organization_id
            and self.group.organization_id != self.organization_id
        ):
            raise ValidationError({'group': 'Guruh boshqa tashkilotga tegishli.'})
