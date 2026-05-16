"""
Task 6 — AI Diagnostika va Knowledge Graph modellari.

Architecture (docs/milliy_sertifikat_django.md, Bosqich 5+20):
  - Zero-Hallucination: matematika backend'da, LLM faqat "suhandon"
  - Mastery: Bayesian beta distribution (alpha=correct+1, beta=incorrect+1)
  - AI Tutor: on-demand Celery task, deterministik xulosani LLM'ga uzatadi
  - Open-Ended: AI-First scoring + Human QA workflow

Models:
  - SkillTag: micro-skill taxonomy (global, parent FK for hierarchy)
  - UserSkillProfile: per-(user,skill) Bayesian mastery state
  - AIFeedback: tutor response cache (avoid duplicate LLM calls)
  - OpenEndedSubmission: essay/audio answers awaiting AI + human QA
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


# ── 1. SkillTag ──────────────────────────────────────────────────────────────
class SkillTag(models.Model):
    """
    Micro-skill: 'Algebra: chiziqli tenglamalar', 'Fizika: Nyuton qonunlari'.
    Global (tenant-aware emas) — taksonomiya universal. parent FK bilan ierarxik.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name=_('Skill nomi'))
    slug = models.SlugField(max_length=120, unique=True)
    description = models.TextField(blank=True)

    parent = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='children',
        help_text=_('Hierarchy: Math → Algebra → Linear equations'),
    )
    subject = models.ForeignKey(
        'catalog.Subject',
        on_delete=models.PROTECT,
        related_name='skills',
        null=True,
        blank=True,
    )

    questions = models.ManyToManyField(
        'catalog.Question',
        blank=True,
        related_name='skills',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Skill Tag')
        verbose_name_plural = _('Skill Taglar')
        ordering = ['name']
        indexes = [
            models.Index(fields=['parent']),
            models.Index(fields=['subject']),
        ]

    def __str__(self):
        return self.name


# ── 2. UserSkillProfile ──────────────────────────────────────────────────────
class UserSkillProfile(models.Model):
    """
    Per-(user, skill) Bayesian beta distribution state.

    Mastery formula:
      mean        = alpha / (alpha + beta)
      variance    = (alpha * beta) / ((alpha+beta)^2 * (alpha+beta+1))
      confidence  = 1 - sqrt(variance)

    Update on each answer:
      correct   → alpha += 1
      incorrect → beta  += 1

    Prior: alpha=1, beta=1 (uniform — 50% mean, low confidence).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='skill_profiles',
    )
    skill = models.ForeignKey(SkillTag, on_delete=models.CASCADE, related_name='user_profiles')

    alpha = models.PositiveIntegerField(default=1, help_text=_('Successes + 1'))
    beta = models.PositiveIntegerField(default=1, help_text=_('Failures + 1'))

    mastery = models.FloatField(default=0.5, db_index=True, help_text=_('alpha / (alpha+beta)'))
    confidence = models.FloatField(default=0.0, help_text=_('1 - sqrt(variance)'))

    last_updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('User Skill Profile')
        verbose_name_plural = _('User Skill Profiles')
        unique_together = [('user', 'skill')]
        ordering = ['mastery']
        indexes = [
            models.Index(fields=['user', 'mastery']),
        ]

    def __str__(self):
        return f'{self.user} / {self.skill}: {self.mastery:.0%}'


# ── 3. AIFeedback ────────────────────────────────────────────────────────────
class AIFeedback(models.Model):
    """
    AI Tutor LLM javobi. On-demand generated, cached by prompt_hash.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Kutilmoqda')
        READY = 'ready', _('Tayyor')
        FAILED = 'failed', _('Xato')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_feedbacks',
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    # Backend hisoblangan deterministik xulosa (LLM'ga shu beriladi)
    summary = models.JSONField(
        default=dict,
        help_text=_('{"weak": [...], "strong": [...], "stats": {...}}'),
    )
    content = models.TextField(blank=True, help_text=_('LLM dan kelgan motivatsion matn'))
    error = models.TextField(blank=True)

    prompt_hash = models.CharField(max_length=64, db_index=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('AI Feedback')
        verbose_name_plural = _('AI Feedbacks')
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['user', '-requested_at']),
        ]

    def __str__(self):
        return f'{self.user} AI feedback ({self.status})'


# ── 4. OpenEndedSubmission ───────────────────────────────────────────────────
class OpenEndedSubmission(models.Model):
    """
    Open-ended (insho/audio) javob. AI-First scoring + Human QA workflow.

    Lifecycle:
      PENDING → AI_REVIEWED → HUMAN_APPROVED  (yashil)
                          → DISPUTED         (qizil — admin'ga signal)
    """

    class Type(models.TextChoices):
        ESSAY = 'essay', _('Insho (matn)')
        AUDIO = 'audio', _('Ovozli javob')

    class Status(models.TextChoices):
        PENDING = 'pending', _('AI tahlil kutilmoqda')
        AI_REVIEWED = 'ai_reviewed', _('AI baholangan')
        HUMAN_APPROVED = 'human_approved', _('Inson tasdiqlagan')
        DISPUTED = 'disputed', _("Bahsli (admin ko'radi)")
        FAILED = 'failed', _('AI xato')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='openended_submissions',
    )
    submission_type = models.CharField(max_length=8, choices=Type.choices)

    content = models.TextField(blank=True, help_text=_('Insho matni yoki audio transcript'))
    audio_file = models.FileField(upload_to='openended/audio/', null=True, blank=True)

    question_version = models.ForeignKey(
        'catalog.QuestionVersion',
        on_delete=models.PROTECT,
        related_name='openended_submissions',
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    ai_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ai_feedback = models.JSONField(
        default=dict,
        help_text=_('{"grammar": 80, "content": 70, "structure": 90, "rationale": "..."}'),
    )

    human_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='openended_reviews',
    )
    human_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    human_notes = models.TextField(blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    ai_completed_at = models.DateTimeField(null=True, blank=True)
    human_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Open-Ended Submission')
        verbose_name_plural = _('Open-Ended Submissions')
        ordering = ['-submitted_at']
        indexes = [
            models.Index(fields=['status', 'submitted_at']),
            models.Index(fields=['user', '-submitted_at']),
        ]

    def __str__(self):
        return f'{self.user} {self.submission_type} ({self.status})'
