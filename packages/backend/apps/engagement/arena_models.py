"""Arena: teacher-built quizzes that students play against a per-question timer.

Lifecycle: DRAFT -> PENDING_REVIEW -> APPROVED -> ARCHIVED. A teacher's quiz goes live
only after an org/platform admin approves it; an admin's own quiz is approved on submit.
All four tables are tenant tables (organization_id + RLS, see migration 0005).
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin

MIN_SECONDS_PER_QUESTION = 5
MAX_SECONDS_PER_QUESTION = 300
MIN_QUESTIONS_TO_SUBMIT = 3
MAX_QUESTIONS = 50


class Quiz(TenantTimestampMixin):
    class Status(models.TextChoices):
        DRAFT = 'draft', _('Qoralama')
        PENDING_REVIEW = 'pending_review', _('Tasdiqlash kutilmoqda')
        APPROVED = 'approved', _('Tasdiqlangan')
        ARCHIVED = 'archived', _('Arxivlangan')

    _ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.PENDING_REVIEW, Status.APPROVED, Status.ARCHIVED},
        Status.PENDING_REVIEW: {Status.APPROVED, Status.DRAFT, Status.ARCHIVED},
        Status.APPROVED: {Status.ARCHIVED},
        Status.ARCHIVED: set(),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    seconds_per_question = models.PositiveSmallIntegerField(
        default=30,
        validators=[
            MinValueValidator(MIN_SECONDS_PER_QUESTION),
            MaxValueValidator(MAX_SECONDS_PER_QUESTION),
        ],
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_quizzes',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Quiz')
        verbose_name_plural = _('Quizzes')
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', 'status'])]

    def __str__(self):
        return f'{self.title} ({self.status})'

    def transition(self, target: 'Quiz.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} -> {target} (allowed: {sorted(allowed)})'
            )
        self.status = target


class QuizQuestion(TenantTimestampMixin):
    """A catalog question (version snapshot) placed in a quiz."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='items')
    question_version = models.ForeignKey(
        'catalog.QuestionVersion', on_delete=models.PROTECT, related_name='+'
    )
    order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['quiz', 'order']
        constraints = [
            models.UniqueConstraint(fields=['quiz', 'order'], name='quizquestion_unique_order'),
            models.UniqueConstraint(
                fields=['quiz', 'question_version'], name='quizquestion_unique_version'
            ),
        ]

    def __str__(self):
        return f'{self.quiz_id} #{self.order}'


class QuizPlay(TenantTimestampMixin):
    """One student's single run through a quiz (server-side timer state lives here)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='plays')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quiz_plays'
    )
    current_index = models.PositiveSmallIntegerField(default=0)
    question_started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    correct_count = models.PositiveSmallIntegerField(default=0)
    points = models.PositiveIntegerField(default=0)
    total_time_ms = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['quiz', 'user'], name='quizplay_one_per_user'),
        ]
        indexes = [
            models.Index(
                fields=['quiz', '-points', 'total_time_ms'],
                name='quizplay_board_idx',
                condition=Q(finished_at__isnull=False),
            ),
        ]

    def __str__(self):
        return f'{self.user_id} on {self.quiz_id}: {self.points}'


class QuizPlayAnswer(TenantTimestampMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    play = models.ForeignKey(QuizPlay, on_delete=models.CASCADE, related_name='answers')
    quiz_question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name='+')
    selected = models.JSONField(null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    timed_out = models.BooleanField(default=False)
    elapsed_ms = models.PositiveIntegerField(default=0)
    points = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['play', 'quiz_question'], name='quizplayanswer_one_per_question'
            ),
        ]

    def __str__(self):
        return f'{self.play_id}/{self.quiz_question_id}: {self.points}'
