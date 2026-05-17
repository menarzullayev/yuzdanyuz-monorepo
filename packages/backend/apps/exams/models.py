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
from simple_history.models import HistoricalRecords

from core.mixins import AuditUserMixin, SoftDeleteMixin, TenantTimestampMixin

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

    # ISSUE-205: state machine
    #   DRAFT ──publish()──► PUBLISHED ──close()──► CLOSED
    #     │                     │
    #     └──cancel()──► CANCELLED ◄──cancel()──┘
    _ALLOWED_TRANSITIONS = {
        Status.DRAFT: {Status.PUBLISHED, Status.CANCELLED},
        Status.PUBLISHED: {Status.CLOSED, Status.CANCELLED},
        Status.CLOSED: set(),
        Status.CANCELLED: set(),
    }

    def _validate_transition(self, target: 'MockExam.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def publish(self) -> None:
        """DRAFT → PUBLISHED. Questions M2M snapshot mock'ga bog'lanadi."""
        self._validate_transition(self.Status.PUBLISHED)
        self.status = self.Status.PUBLISHED
        self.save(update_fields=['status', 'updated_at'])

    def close(self) -> None:
        """PUBLISHED → CLOSED. closes_at vaqti tugagandan keyin yangi attempt yo'q."""
        self._validate_transition(self.Status.CLOSED)
        self.status = self.Status.CLOSED
        self.save(update_fields=['status', 'updated_at'])

    def cancel(self) -> None:
        """{DRAFT, PUBLISHED} → CANCELLED. Admin bekor qilganda."""
        self._validate_transition(self.Status.CANCELLED)
        self.status = self.Status.CANCELLED
        self.save(update_fields=['status', 'updated_at'])

    def __str__(self):
        return f'{self.title} ({self.scheduled_at:%Y-%m-%d %H:%M})'


# ── 2. MockExamQuestion (through) ──────────────────────────────────
class MockExamQuestion(models.Model):
    """MockExam → QuestionVersion through table.

    QuestionVersion FK (Question emas) — savol catalogda tahrirlansa ham
    mock'dagi snapshot o'zgarmaydi. Order va points shu yerda.

    Tenant scope (ISSUE-110 W4): `organization` FK YO'Q — L2 isolation parent FK
    (`mock_exam`) orqali kelib chiqadi. `MockExam` org-scoped, shuning uchun
    `MockExamQuestion.objects.filter(mock_exam__organization=org)` natural filter.
    L3 PostgreSQL RLS policy (`exams/migrations/0002_enable_rls.py`) ham parent
    FK orqali — `mock_exam_id IN (SELECT id FROM exams_mockexam WHERE organization_id = ...)`.
    Raw `MockExamQuestion.objects.filter(...)` ishlatishda hech bo'lmaganda
    `mock_exam__organization=request.org` bilan filter qilish shart.
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
class ExamAttempt(SoftDeleteMixin, AuditUserMixin, TenantTimestampMixin):
    """
    Bir foydalanuvchi bir MockExam'ni topshirganda. Anti-cheat fields
    (heartbeat, strikes) shu yerda. Bir user — bir mock — bir attempt.

    ISSUE-103: soft-delete bilan — GDPR erasure'da score + status saqlanadi
    (DTM compliance + leaderboard tarixi anonim user bilan ham haqiqiy qoladi).
    """

    pii_fields = ()  # ExamAttempt'da direct PII yo'q (faqat user FK + score)

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
    # ISSUE-103: SET_NULL bilan — User.delete() audit row'ni yo'q qilmaydi
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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

    # ISSUE-401: SOC2 audit trail (DTM compliance — 2 yil retention)
    history = HistoricalRecords()

    class Meta:
        verbose_name = _('Exam Attempt')
        verbose_name_plural = _('Exam Attempts')
        unique_together = [('exam', 'user')]
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['exam', 'status']),
        ]

    # ISSUE-205: state machine transitions (django-fsm-2 pattern, library
    # wrapper kelajak refactor'da). Har transition aniq source/target validate
    # qiladi. Race-condition + invalid state'dan himoya.
    #
    # State diagram:
    #   IN_PROGRESS ──submit()──► SUBMITTED
    #               │
    #               ├──cancel_for_cheating()──► CANCELLED
    #               │
    #               ├──expire()──► EXPIRED
    #               │
    #               └──open_dispute()──► DISPUTED
    #
    # SUBMITTED, CANCELLED, EXPIRED — terminal (transition'lar yo'q)
    # DISPUTED'dan SUBMITTED/CANCELLED'ga admin orqali qaytarish mumkin (resolve_dispute)

    _ALLOWED_TRANSITIONS = {
        Status.IN_PROGRESS: {Status.SUBMITTED, Status.CANCELLED, Status.EXPIRED, Status.DISPUTED},
        Status.SUBMITTED: {Status.DISPUTED},  # nizo ochilishi mumkin
        Status.DISPUTED: {Status.SUBMITTED, Status.CANCELLED},  # resolve
        Status.CANCELLED: set(),  # terminal
        Status.EXPIRED: set(),  # terminal
    }

    def _validate_transition(self, target: 'ExamAttempt.Status') -> None:
        """Raise ValueError agar source → target ruxsat etilmagan."""
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def submit(self) -> None:
        """IN_PROGRESS → SUBMITTED. Score finalize task signal orqali."""
        self._validate_transition(self.Status.SUBMITTED)
        self.status = self.Status.SUBMITTED
        from django.utils import timezone

        self.submitted_at = timezone.now()
        self.save(update_fields=['status', 'submitted_at', 'updated_at'])

    def cancel_for_cheating(self, reason: 'ExamAttempt.CancelReason') -> None:
        """IN_PROGRESS → CANCELLED (anti-cheat 3+ strikes)."""
        self._validate_transition(self.Status.CANCELLED)
        self.status = self.Status.CANCELLED
        self.cancel_reason = reason
        self.save(update_fields=['status', 'cancel_reason', 'updated_at'])

    def expire(self) -> None:
        """IN_PROGRESS → EXPIRED (duration tugagan, submit qilinmagan)."""
        self._validate_transition(self.Status.EXPIRED)
        self.status = self.Status.EXPIRED
        self.save(update_fields=['status', 'updated_at'])

    def open_dispute(self) -> None:
        """{IN_PROGRESS, SUBMITTED} → DISPUTED."""
        self._validate_transition(self.Status.DISPUTED)
        self.status = self.Status.DISPUTED
        self.save(update_fields=['status', 'updated_at'])

    def resolve_dispute(self, *, into: 'ExamAttempt.Status') -> None:
        """DISPUTED → SUBMITTED (uphold) yoki CANCELLED (admin decision)."""
        self._validate_transition(into)
        self.status = into
        self.save(update_fields=['status', 'updated_at'])

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

    # ISSUE-205: state machine
    #   IN_PROGRESS ──complete()──► COMPLETED (terminal)
    #               └──abandon()──► ABANDONED (terminal)
    _ALLOWED_TRANSITIONS = {
        Status.IN_PROGRESS: {Status.COMPLETED, Status.ABANDONED},
        Status.COMPLETED: set(),
        Status.ABANDONED: set(),
    }

    def _validate_transition(self, target: 'PracticeSession.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def complete(self) -> None:
        """IN_PROGRESS → COMPLETED. User barcha savollarni javoblaganda."""
        self._validate_transition(self.Status.COMPLETED)
        from django.utils import timezone

        self.status = self.Status.COMPLETED
        self.ended_at = timezone.now()
        self.save(update_fields=['status', 'ended_at', 'updated_at'])

    def abandon(self) -> None:
        """IN_PROGRESS → ABANDONED. Tashlab ketilgan (timeout yoki user exit)."""
        self._validate_transition(self.Status.ABANDONED)
        from django.utils import timezone

        self.status = self.Status.ABANDONED
        self.ended_at = timezone.now()
        self.save(update_fields=['status', 'ended_at', 'updated_at'])

    def __str__(self):
        return f'{self.user} practice ({self.status})'


# ── 5. UserAnswer ──────────────────────────────────────────────────
class UserAnswer(SoftDeleteMixin, TenantTimestampMixin):
    """
    Bir savolga bir javob. XOR constraint: yo `attempt` yo `session` to'ldirilgan.

    auto_correct=True bo'lsa — savol quarantined va bonus ball berilgan.

    ISSUE-103: soft-delete + PII redaction. selected (essay matni bo'lishi
    mumkin) redacted, lekin is_correct + time_spent saqlanadi (skill mastery
    aggregation uchun).
    """

    pii_fields = ('selected',)  # OE savolda essay matni bo'lishi mumkin

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

    # ISSUE-205: state machine
    #   OPEN ──quarantine()──► QUARANTINED (terminal) — savol blokga olindi
    #        └──reject()──► REJECTED (terminal) — shikoyat asossiz
    _ALLOWED_TRANSITIONS = {
        Status.OPEN: {Status.QUARANTINED, Status.REJECTED},
        Status.QUARANTINED: set(),
        Status.REJECTED: set(),
    }

    def _validate_transition(self, target: 'QuestionDispute.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def quarantine(self) -> None:
        """OPEN → QUARANTINED. Dispute soni threshold'dan oshganda yoki admin."""
        self._validate_transition(self.Status.QUARANTINED)
        self.status = self.Status.QUARANTINED
        self.save(update_fields=['status', 'updated_at'])

    def reject(self) -> None:
        """OPEN → REJECTED. Admin shikoyatni rad etganda."""
        self._validate_transition(self.Status.REJECTED)
        self.status = self.Status.REJECTED
        self.save(update_fields=['status', 'updated_at'])
