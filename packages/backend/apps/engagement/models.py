"""
Engagement modellari (Task 5 + Task 9).

Task 5 — LeaderboardSnapshot
Task 9 — UserStreak, League, LeagueMembership, Notification

Architecture (Bosqich 21+22+15+16):
  - Streak: Duolingo style — kunlik faollik zanjiri + Telegram ogohlantirish
  - Leagues: weekly recalc — top 10 promote, bottom 10 demote (race style)
  - Notifications: omni-channel sequential (TG → Push → SMS)
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class LeaderboardSnapshot(models.Model):
    """
    Bir period (hafta/oy/yil) + bir scope (global/region/tenant/mock) uchun
    Top-N ranking snapshot.
    """

    class Period(models.TextChoices):
        WEEKLY = 'weekly', _('Haftalik')
        MONTHLY = 'monthly', _('Oylik')
        YEARLY = 'yearly', _('Yillik')

    class ScopeKind(models.TextChoices):
        GLOBAL = 'global', _('Platforma (global)')
        REGION = 'region', _('Viloyat')
        TENANT = 'tenant', _('Tashkilot')
        MOCK = 'mock', _('Mock imtihon')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    period = models.CharField(max_length=8, choices=Period.choices, db_index=True)
    period_key = models.CharField(
        max_length=16,
        db_index=True,
        help_text=_('Misol: "2026-W19" (weekly), "2026-05" (monthly), "2026" (yearly)'),
    )
    scope_kind = models.CharField(max_length=8, choices=ScopeKind.choices, db_index=True)
    scope_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text=_("global scope uchun bo'sh; boshqalar uchun region_id/org_id/mock_id"),
    )

    taken_at = models.DateTimeField(auto_now_add=True, db_index=True)
    total = models.PositiveIntegerField(default=0)
    entries = models.JSONField(
        default=list,
        help_text=_('Top-N: [{"user_id": "uuid", "score": float, "rank": int}, ...]'),
    )

    class Meta:
        verbose_name = _('Leaderboard Snapshot')
        verbose_name_plural = _('Leaderboard Snapshots')
        ordering = ['-taken_at']
        unique_together = [('period', 'period_key', 'scope_kind', 'scope_id')]
        indexes = [
            models.Index(fields=['period', 'scope_kind', 'period_key']),
        ]

    def __str__(self):
        scope = f'{self.scope_kind}:{self.scope_id}' if self.scope_id else self.scope_kind
        return f'{self.period} {self.period_key} {scope} ({self.total} entries)'


# ── ISSUE-304: LeaderboardEntry — normalized snapshot entries ────────────────


class LeaderboardEntry(models.Model):
    """Bitta snapshot ichidagi user entry. JSON 'entries' field o'rniga
    normalized — index'lar tezroq, "user X'ning Y haftadagi rank'i" query
    O(log N) bo'ladi.

    Migration: existing LeaderboardSnapshot.entries JSONField saqlanadi
    (back-compat 3 oy), yangi archive task'lar bu jadvalga yozadi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(
        LeaderboardSnapshot,
        on_delete=models.CASCADE,
        related_name='entry_rows',
    )
    user_id = models.UUIDField(db_index=True)  # FK emas — user delete bo'lganda saqlash
    rank = models.PositiveIntegerField()
    score = models.FloatField()

    class Meta:
        verbose_name = _('Leaderboard Entry')
        verbose_name_plural = _('Leaderboard Entries')
        ordering = ['snapshot', 'rank']
        unique_together = [('snapshot', 'user_id')]
        indexes = [
            models.Index(fields=['snapshot', 'rank']),
            models.Index(fields=['user_id', '-snapshot']),  # user history query
        ]

    def __str__(self):
        return f'#{self.rank} user={str(self.user_id)[:8]} score={self.score}'


# ── Task 9 — UserStreak ──────────────────────────────────────────────────────


class UserStreak(models.Model):
    """
    Duolingo style daily streak.
    last_activity_date orqali kun zanjiri kuzatiladi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='streak',
    )
    current_streak = models.PositiveIntegerField(default=0)
    max_streak = models.PositiveIntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('User Streak')
        verbose_name_plural = _('User Streaks')

    def __str__(self):
        return f'{self.user} streak={self.current_streak}'


# ── Task 9 — Leagues ─────────────────────────────────────────────────────────


class League(models.Model):
    """
    Liga shabloni (Bronza, Kumush, Oltin, Olmos). rank_order'dan kelib chiqib
    promote/demote qilinadi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=32, unique=True)
    rank_order = models.PositiveSmallIntegerField(
        unique=True,
        help_text=_('1=Bronza, 2=Kumush, 3=Oltin, 4=Olmos'),
    )
    color_hex = models.CharField(max_length=7, default='#888888')
    promote_reward_coins = models.PositiveIntegerField(
        default=100, help_text=_("Bu liga'ga ko'tarilgan user'ga beriladigan Coin")
    )

    class Meta:
        verbose_name = _('League')
        verbose_name_plural = _('Leagues')
        ordering = ['rank_order']

    def __str__(self):
        return f'{self.name} (rank {self.rank_order})'


class LeagueMembership(models.Model):
    """
    Per-(user, week) liga membership. Hafta oxirida promotion/demotion
    Celery task tomonidan hisoblanadi.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='league_memberships',
    )
    league = models.ForeignKey(League, on_delete=models.PROTECT, related_name='memberships')

    # Hafta boshi (Monday) va oxiri (Sunday)
    period_start = models.DateField(db_index=True)
    period_end = models.DateField()

    points_earned = models.PositiveIntegerField(default=0)
    rank_in_league = models.PositiveIntegerField(null=True, blank=True)

    promoted = models.BooleanField(default=False)
    demoted = models.BooleanField(default=False)
    next_league = models.ForeignKey(
        League,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('League Membership')
        verbose_name_plural = _('League Memberships')
        ordering = ['-period_start']
        unique_together = [('user', 'period_start')]
        indexes = [
            models.Index(fields=['league', 'period_start', '-points_earned']),
        ]

    def __str__(self):
        return f'{self.user} {self.league} ({self.period_start})'


# ── Task 9 — Notifications ───────────────────────────────────────────────────


class Notification(models.Model):
    """
    Omni-channel notification queue. Sequential fan-out:
    Telegram → Push → SMS (priority muhim bo'lsa fallback).
    """

    class Channel(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram bot'
        PUSH = 'push', 'Push notification'
        SMS = 'sms', 'SMS PlayMobile'
        IN_APP = 'in_app', 'In-app feed'

    class Priority(models.TextChoices):
        LOW = 'low', _('Past')
        NORMAL = 'normal', _('Oddiy')
        IMPORTANT = 'important', _('Muhim')
        URGENT = 'urgent', _('Shoshilinch')

    class Status(models.TextChoices):
        PENDING = 'pending', _('Kutilmoqda')
        SENT = 'sent', _("Jo'natilgan")
        FAILED = 'failed', _('Xato')
        READ = 'read', _("O'qilgan")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    channel = models.CharField(
        max_length=10,
        choices=Channel.choices,
        db_index=True,
        help_text=_('Yo IN_APP (har doim) yo external channel actual delivery'),
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL, db_index=True
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    title = models.CharField(max_length=255)
    body = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    delivery_attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Notification')
        verbose_name_plural = _('Notifications')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', 'priority']),
        ]

    # ISSUE-205: state machine
    #   PENDING ──mark_sent()──► SENT ──mark_read()──► READ (terminal)
    #           └──mark_failed()──► FAILED ──retry()──► PENDING (re-attempt)
    _ALLOWED_TRANSITIONS = {
        Status.PENDING: {Status.SENT, Status.FAILED},
        Status.SENT: {Status.READ, Status.FAILED},  # FAILED — webhook ack timeout
        Status.FAILED: {Status.PENDING},  # retry path
        Status.READ: set(),
    }

    def __str__(self):
        return f'{self.user} [{self.channel}/{self.priority}] {self.title[:30]}'

    def _validate_transition(self, target: 'Notification.Status') -> None:
        allowed = self._ALLOWED_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise ValueError(
                f'Invalid state transition: {self.status} → {target} (allowed: {sorted(allowed)})'
            )

    def mark_sent(self) -> None:
        """PENDING → SENT. Channel adapter muvaffaqiyatli delivery qildi."""
        self._validate_transition(self.Status.SENT)
        from django.utils import timezone

        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_at'])

    def mark_failed(self, *, error: str = '') -> None:
        """{PENDING, SENT} → FAILED. Channel adapter error qaytardi."""
        self._validate_transition(self.Status.FAILED)
        self.status = self.Status.FAILED
        self.delivery_attempts += 1
        if error:
            self.error = error[:1000]
        self.save(update_fields=['status', 'delivery_attempts', 'error'])

    def mark_read(self) -> None:
        """SENT → READ. User in-app notification'ni ochdi."""
        self._validate_transition(self.Status.READ)
        from django.utils import timezone

        self.status = self.Status.READ
        self.read_at = timezone.now()
        self.save(update_fields=['status', 'read_at'])

    def retry(self) -> None:
        """FAILED → PENDING. Worker yoki admin qayta urinib ko'rish uchun."""
        self._validate_transition(self.Status.PENDING)
        self.status = self.Status.PENDING
        self.save(update_fields=['status'])
