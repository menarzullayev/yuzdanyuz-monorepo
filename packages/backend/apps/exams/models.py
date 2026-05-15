"""
Task 4 — Exam Engine modellari.

Arxitektura (docs/milliy_sertifikat_django.md, Bosqich 3+4+23):
  - MockExam        → statik, haftalik (yakshanba), reytingli (Bosqich 3)
  - PracticeSession → dinamik, blueprint asosida random tanlash, cheksiz (Bosqich 3)
  - ExamAttempt     → bir user bir mock'ni topshirganda (heartbeat, strikes — Bosqich 4)
  - UserAnswer      → har savol uchun javob (XOR: attempt yoki session)
  - AntiCheatEvent  → strike audit log (dispute uchun)
  - QuestionDispute → user shikoyati (5% trigger → quarantine — Bosqich 23)

Multi-tenancy:
  - Barcha modellar TenantTimestampMixin'dan meros (organization FK + TenantManager)
  - Faqat MockExam PublicOrTenantManager ishlatadi (is_public=True platform mocks)
  - L3 RLS DB darajasida ham filter qiladi
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, Q
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin

from .managers import PublicOrTenantManager


# ── 1. MockExam ────────────────────────────────────────────────────
class MockExam(TenantTimestampMixin):
    """
    Statik imtihon shabloni. Bir nechta foydalanuvchi shu mock'ni topshiradi.

    is_public=True → platform mock (barcha tenantlar ko'radi, adolatli leaderboard).
    is_public=False → faqat shu organization a'zolari ko'radi.

    Questions M2M `MockExamQuestion` orqali pin qilinadi (publish vaqtida snapshot).
    """

    class Status(models.TextChoices):
        DRAFT = 'draft', _('Qoralama')
        PUBLISHED = 'published', _("E'lon qilingan")
        CLOSED = 'closed', _('Yopilgan')
        CANCELLED = 'cancelled', _('Bekor qilingan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, verbose_name=_('Sarlavha'))
    description = models.TextField(blank=True, verbose_name=_('Tavsif'))

    duration_minutes = models.PositiveIntegerField(
        verbose_name=_('Davomiyligi (daqiqa)'),
    )
    scheduled_at = models.DateTimeField(
        verbose_name=_('Boshlanish vaqti'),
        help_text=_(
            "Yakshanba 10:00 (yoki B2B custom). Shu vaqtdan oldin attempt yaratib bo'lmaydi."
        ),
    )
    closes_at = models.DateTimeField(
        verbose_name=_('Yopilish vaqti'),
        help_text=_("Shu vaqtdan keyin yangi attempt yaratib bo'lmaydi."),
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    is_public = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_('Platform mock'),
        help_text=_("True bo'lsa barcha tenantlar ko'radi (cross-tenant leaderboard)."),
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='created_mock_exams',
    )

    # Through table — order va QuestionVersion pin uchun
    questions = models.ManyToManyField(
        'catalog.QuestionVersion',
        through='MockExamQuestion',
        related_name='mock_exams',
    )

    objects = PublicOrTenantManager()

    class Meta:
        verbose_name = _('Mock Exam')
        verbose_name_plural = _("Mock Exam'lar")
        ordering = ['-scheduled_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
            models.Index(fields=['is_public', 'status']),
        ]

    def __str__(self):
        return f'{self.title} ({self.scheduled_at:%Y-%m-%d %H:%M})'


# ── 2. MockExamQuestion (through) ──────────────────────────────────
class MockExamQuestion(models.Model):
    """
    MockExam → QuestionVersion through table.

    QuestionVersion FK (Question emas) — savol catalogda tahrirlansa ham
    mock'dagi snapshot o'zgarmaydi. Order va points shu yerda.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mock_exam = models.ForeignKey(
        MockExam,
        on_delete=models.CASCADE,
        related_name='question_links',
    )
    question_version = models.ForeignKey(
        'catalog.QuestionVersion',
        on_delete=models.PROTECT,
        related_name='mock_exam_links',
    )
    order = models.PositiveSmallIntegerField()
    points = models.PositiveSmallIntegerField(default=1)

    class Meta:
        verbose_name = _('Mock Exam Question')
        verbose_name_plural = _('Mock Exam Questions')
        unique_together = [('mock_exam', 'order'), ('mock_exam', 'question_version')]
        ordering = ['order']

    def __str__(self):
        return f'{self.mock_exam.title} #{self.order}'


# ── 3. ExamAttempt ─────────────────────────────────────────────────
class ExamAttempt(TenantTimestampMixin):
    """
    Bir foydalanuvchi bir MockExam'ni topshirganda. Anti-cheat fields
    (heartbeat, strikes) shu yerda. Bir user — bir mock — bir attempt.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', _('Davom etmoqda')
        SUBMITTED = 'submitted', _('Topshirilgan')
        CANCELLED = 'cancelled', _('Bekor qilingan')
        DISPUTED = 'disputed', _("Shikoyat ko'rib chiqilmoqda")
        EXPIRED = 'expired', _('Vaqti tugagan')

    class CancelReason(models.TextChoices):
        TAB_SWITCH = 'tab_switch', _("Tab o'zgartirish")
        FULLSCREEN_EXIT = 'fullscreen_exit', _('Fullscreen chiqish')
        HEARTBEAT_LOST = 'heartbeat_lost', _('Aloqa uzilgan')
        ADMIN_CANCELLED = 'admin_cancelled', _('Admin tomonidan bekor')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    exam = models.ForeignKey(MockExam, on_delete=models.PROTECT, related_name='attempts')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='exam_attempts',
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        db_index=True,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_('0.00 — 100.00'),
    )
    correct_count = models.PositiveIntegerField(default=0)
    total_points = models.PositiveIntegerField(default=0)

    # Anti-cheat
    heartbeat_last_at = models.DateTimeField(null=True, blank=True)
    strikes = models.PositiveSmallIntegerField(default=0)
    cancel_reason = models.CharField(
        max_length=24,
        choices=CancelReason.choices,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _('Exam Attempt')
        verbose_name_plural = _('Exam Attempts')
        unique_together = [('exam', 'user')]
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['exam', 'status']),
        ]

    def __str__(self):
        return f'{self.user} → {self.exam.title} ({self.status})'


# ── 4. PracticeSession ─────────────────────────────────────────────
class PracticeSession(TenantTimestampMixin):
    """
    Foydalanuvchi o'zi yaratgan dinamik mashg'ulot sessiyasi.
    Blueprint runtime'da tag/topic asosida random savol tanlaydi (snapshot yo'q).
    """

    class Status(models.TextChoices):
        IN_PROGRESS = 'in_progress', _('Davom etmoqda')
        COMPLETED = 'completed', _('Yakunlangan')
        ABANDONED = 'abandoned', _('Tashlab ketilgan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='practice_sessions',
    )

    blueprint = models.JSONField(
        verbose_name=_('Blueprint'),
        help_text=_('Misol: [{"topic_id": "uuid", "count": 10, "difficulty": "hard"}]'),
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
        db_index=True,
    )

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    correct_count = models.PositiveIntegerField(default=0)
    total_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('Practice Session')
        verbose_name_plural = _('Practice Sessions')
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        return f'{self.user} practice ({self.status})'


# ── 5. UserAnswer ──────────────────────────────────────────────────
class UserAnswer(TenantTimestampMixin):
    """
    Bir savolga bir javob. XOR constraint: yo `attempt` yo `session` to'ldirilgan.

    auto_correct=True bo'lsa — savol quarantined va bonus ball berilgan.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # XOR: aynan bittasi to'ldiriladi (CHECK constraint pastda)
    attempt = models.ForeignKey(
        ExamAttempt,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='answers',
    )
    session = models.ForeignKey(
        PracticeSession,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='answers',
    )

    question_version = models.ForeignKey(
        'catalog.QuestionVersion',
        on_delete=models.PROTECT,
        related_name='user_answers',
    )

    # SC: int, MC: list[int], FB: str, OE: str, FU: file_id
    selected = models.JSONField(
        verbose_name=_('Tanlangan javob'),
        help_text=_("Format question type'ga bog'liq."),
    )

    is_correct = models.BooleanField(null=True, blank=True)
    auto_correct = models.BooleanField(
        default=False,
        verbose_name=_('Auto-correct (quarantine bonus)'),
    )

    time_spent_seconds = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('User Answer')
        verbose_name_plural = _('User Answers')
        ordering = ['answered_at']
        constraints = [
            CheckConstraint(
                condition=(
                    Q(attempt__isnull=False, session__isnull=True)
                    | Q(attempt__isnull=True, session__isnull=False)
                ),
                name='useranswer_attempt_xor_session',
            ),
        ]
        indexes = [
            models.Index(fields=['attempt', 'question_version']),
            models.Index(fields=['session', 'question_version']),
        ]


# ── 6. AntiCheatEvent ──────────────────────────────────────────────
class AntiCheatEvent(TenantTimestampMixin):
    """
    Strike audit log. Dispute paytida 'qaysi voqea qachon bo'lgan?'
    degan savolga javob beradi.
    """

    class EventType(models.TextChoices):
        TAB_SWITCH = 'tab_switch', _("Tab o'zgartirish")
        WINDOW_BLUR = 'window_blur', _('Window blur')
        FULLSCREEN_EXIT = 'fullscreen_exit', _('Fullscreen chiqish')
        HEARTBEAT_MISS = 'heartbeat_miss', _("Heartbeat o'tkazib yuborilgan")
        DEVTOOLS_OPEN = 'devtools_open', _('DevTools ochilgan')
        COPY_ATTEMPT = 'copy_attempt', _('Nusxalash urinishi')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        ExamAttempt,
        on_delete=models.CASCADE,
        related_name='anti_cheat_events',
    )
    event_type = models.CharField(max_length=24, choices=EventType.choices, db_index=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('Misol: {"user_agent": "...", "screen_w": 1920}'),
    )

    class Meta:
        verbose_name = _('Anti-Cheat Event')
        verbose_name_plural = _('Anti-Cheat Events')
        ordering = ['occurred_at']


# ── 7. QuestionDispute ─────────────────────────────────────────────
class QuestionDispute(TenantTimestampMixin):
    """
    Foydalanuvchi savol haqida shikoyati. >5% bo'lsa Celery task
    Question.is_quarantined = True qiladi va barchaga bonus beradi.
    """

    class Reason(models.TextChoices):
        WRONG_ANSWER = 'wrong_answer', _("To'g'ri javob yo'q")
        IMAGE_BROKEN = 'image_broken', _('Rasm ochilmadi')
        AMBIGUOUS = 'ambiguous', _("Bir nechta to'g'ri javob bor")
        TYPO = 'typo', _('Imloda xato')
        OTHER = 'other', _('Boshqa')

    class Status(models.TextChoices):
        OPEN = 'open', _('Ochiq')
        QUARANTINED = 'quarantined', _('Karantin qilingan')
        REJECTED = 'rejected', _('Rad etilgan')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        ExamAttempt,
        on_delete=models.CASCADE,
        related_name='disputes',
    )
    question_version = models.ForeignKey(
        'catalog.QuestionVersion',
        on_delete=models.PROTECT,
        related_name='disputes',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='disputes_filed',
    )

    reason = models.CharField(max_length=16, choices=Reason.choices, db_index=True)
    note = models.TextField(blank=True)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )

    class Meta:
        verbose_name = _('Question Dispute')
        verbose_name_plural = _('Question Disputes')
        ordering = ['-created_at']
        unique_together = [('attempt', 'question_version', 'user')]
        indexes = [
            models.Index(fields=['question_version', 'status']),
        ]
