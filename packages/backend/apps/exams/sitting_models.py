"""
Official-style exam sittings: scheduled window, Rasch scoring, grade bands, certificates.

A sitting sits on top of an existing MockExam. ExamAttempt is NOT modified: the attempts of a
sitting are the SUBMITTED attempts of `mock_exam` that started inside [opens_at, closes_at]
(see sitting_service.collect_attempts); per-attempt output lives in SittingResult.

Tenant scope: every model here carries `organization` (the mock's organization) and has RLS
policies (migration 0009). Public certificate verification reads through unscoped_context().
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin

CERTIFICATE_VALIDITY_YEARS = 3

# Milliy sertifikat bands (verified 2026-11-11). `min` is inclusive; a band applies to
# scores >= min and below the next higher band's min. Below 46 -> no grade, no certificate.
NATIONAL_BANDS = [
    {'grade': 'A+', 'min': 70, 'certificate': True},
    {'grade': 'A', 'min': 65, 'certificate': True},
    {'grade': 'B+', 'min': 60, 'certificate': True},
    {'grade': 'B', 'min': 55, 'certificate': True},
    {'grade': 'C+', 'min': 50, 'certificate': True},
    {'grade': 'C', 'min': 46, 'certificate': True},
    {'grade': '', 'min': 0, 'certificate': False},
]


def quantize_score(value) -> Decimal:
    """Scores are stored (and graded) at 2 decimals, half-up, so displayed score == graded score."""
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def validate_bands(bands) -> None:
    if not isinstance(bands, list) or not bands:
        raise ValidationError('bands must be a non-empty list.')
    mins = []
    for b in bands:
        if not isinstance(b, dict) or not {'grade', 'min', 'certificate'} <= set(b):
            raise ValidationError('each band needs grade, min, certificate.')
        if not isinstance(b['min'], (int, float)) or isinstance(b['min'], bool) or b['min'] < 0:
            raise ValidationError('band min must be a non-negative number.')
        if not isinstance(b['certificate'], bool):
            raise ValidationError('band certificate must be boolean.')
        mins.append(b['min'])
    if len(set(mins)) != len(mins):
        raise ValidationError('band mins must be unique.')
    if 0 not in mins:
        raise ValidationError('one band must start at min=0 so every score has a band.')


def band_for_score(bands, score) -> dict:
    """Highest band whose min <= score."""
    score = Decimal(str(score))
    for b in sorted(bands, key=lambda x: x['min'], reverse=True):
        if score >= Decimal(str(b['min'])):
            return b
    raise ValueError('score below every band; bands must include min=0')


def add_years(dt, years: int):
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:  # Feb 29 -> Feb 28
        return dt.replace(year=dt.year + years, day=28)


class GradeScale(TenantTimestampMixin):
    """Reusable scoring scale: score range, ability->score transform and grade bands (data)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=75)
    # scaled = max_score/2 + points_per_logit * (ability - midpoint_ability), clamped to [0, max]
    # Item difficulties are centred at 0 logits, so ability 0 == a person at the average item.
    midpoint_ability = models.FloatField(default=0.0)
    points_per_logit = models.FloatField(
        default=9.375, help_text='75/8: +-4 logits span the whole 0-75 range.'
    )
    bands = models.JSONField(default=list, validators=[validate_bands])

    class Meta:
        verbose_name = _('Grade Scale')
        verbose_name_plural = _('Grade Scales')
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['organization', 'name'], name='gradescale_org_name_uq'),
        ]

    def scale_ability(self, ability: float) -> Decimal:
        mid = float(self.max_score) / 2
        raw = mid + self.points_per_logit * (ability - self.midpoint_ability)
        return quantize_score(min(max(raw, 0.0), float(self.max_score)))

    def band_for(self, scaled_score) -> dict:
        return band_for_score(self.bands, scaled_score)

    @classmethod
    def national_default(cls, organization):
        obj, _created = cls.objects.get_or_create(
            organization=organization,
            name='Milliy sertifikat (75)',
            defaults={'bands': NATIONAL_BANDS},
        )
        return obj

    def __str__(self):
        return self.name


class ExamSitting(TenantTimestampMixin):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Qoralama')
        OPEN = 'open', _('Ochiq')
        CLOSED = 'closed', _('Yopilgan')
        CALCULATED = 'calculated', _('Hisoblangan')
        PUBLISHED = 'published', _("E'lon qilingan")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mock_exam = models.ForeignKey(
        'exams.MockExam', on_delete=models.PROTECT, related_name='sittings'
    )
    grade_scale = models.ForeignKey(GradeScale, on_delete=models.PROTECT, related_name='sittings')
    title = models.CharField(max_length=255)
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    calculated_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    calibration = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_sittings'
    )

    class Meta:
        verbose_name = _('Exam Sitting')
        verbose_name_plural = _('Exam Sittings')
        ordering = ['-opens_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(closes_at__gt=models.F('opens_at')), name='sitting_window_valid'
            ),
            models.UniqueConstraint(fields=['mock_exam'], name='sitting_one_per_mock'),
        ]
        indexes = [models.Index(fields=['organization', 'status'])]

    _ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.OPEN},
        Status.OPEN: {Status.CLOSED},
        Status.CLOSED: {Status.CALCULATED},
        # re-calculation is allowed until results are published
        Status.CALCULATED: {Status.CALCULATED, Status.PUBLISHED},
        Status.PUBLISHED: set(),
    }

    def validate_transition(self, target) -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid sitting transition: {self.status} -> {target} '
                f'(allowed: {sorted(allowed)})'
            )

    def open(self) -> None:
        self.validate_transition(self.Status.OPEN)
        self.status = self.Status.OPEN
        self.save(update_fields=['status', 'updated_at'])

    def close(self) -> None:
        self.validate_transition(self.Status.CLOSED)
        self.status = self.Status.CLOSED
        self.save(update_fields=['status', 'updated_at'])

    def clean(self):
        if self.closes_at and self.opens_at and self.closes_at <= self.opens_at:
            raise ValidationError('closes_at must be after opens_at.')
        if (
            self.mock_exam_id
            and self.organization_id
            and self.mock_exam.organization_id != self.organization_id
        ):
            raise ValidationError('Cross-tenant mock exam forbidden.')

    def __str__(self):
        return f'{self.title} ({self.status})'


class SittingItem(TenantTimestampMixin):
    """Item (question) difficulty estimated for one sitting, in logits (mean 0)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sitting = models.ForeignKey(ExamSitting, on_delete=models.CASCADE, related_name='items')
    question_version = models.ForeignKey(
        'catalog.QuestionVersion', on_delete=models.PROTECT, related_name='sitting_items'
    )
    difficulty = models.FloatField()
    correct_count = models.PositiveIntegerField()
    attempt_count = models.PositiveIntegerField()

    class Meta:
        verbose_name = _('Sitting Item')
        verbose_name_plural = _('Sitting Items')
        constraints = [
            models.UniqueConstraint(
                fields=['sitting', 'question_version'], name='sittingitem_sitting_qv_uq'
            ),
        ]

    def __str__(self):
        return f'{self.sitting_id}:{self.question_version_id} b={self.difficulty:.2f}'


class SittingResult(TenantTimestampMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sitting = models.ForeignKey(ExamSitting, on_delete=models.CASCADE, related_name='results')
    attempt = models.OneToOneField(
        'exams.ExamAttempt', on_delete=models.PROTECT, related_name='sitting_result'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sitting_results',
    )
    raw_score = models.PositiveIntegerField()
    max_raw_score = models.PositiveIntegerField()
    ability = models.FloatField(help_text='Rasch ability in logits.')
    ability_se = models.FloatField(null=True, blank=True)
    scaled_score = models.DecimalField(max_digits=6, decimal_places=2)
    grade = models.CharField(max_length=4, blank=True)
    certificate_eligible = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Sitting Result')
        verbose_name_plural = _('Sitting Results')
        ordering = ['-scaled_score']
        indexes = [models.Index(fields=['sitting', 'user'])]

    def __str__(self):
        return f'{self.sitting_id} {self.scaled_score} {self.grade}'


class Certificate(TenantTimestampMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='certificates',
    )
    sitting = models.ForeignKey(ExamSitting, on_delete=models.PROTECT, related_name='certificates')
    result = models.OneToOneField(
        SittingResult, on_delete=models.PROTECT, related_name='certificate'
    )
    # Snapshot: survives user erasure (GDPR soft-delete) and is all the public page may show.
    holder_initials = models.CharField(max_length=16, blank=True)
    scaled_score = models.DecimalField(max_digits=6, decimal_places=2)
    grade = models.CharField(max_length=4)
    issued_at = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _('Certificate')
        verbose_name_plural = _('Certificates')
        ordering = ['-issued_at']
        indexes = [models.Index(fields=['organization', 'user'])]

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return 'revoked'
        if self.valid_until <= timezone.now():
            return 'expired'
        return 'valid'

    def revoke(self, reason: str = '') -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_reason = reason[:255]
            self.save(update_fields=['revoked_at', 'revoked_reason', 'updated_at'])

    def __str__(self):
        return self.number
