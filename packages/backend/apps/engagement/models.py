"""
Task 5 — LeaderboardSnapshot.

Haftalik / oylik / yillik snapshot — Redis ZSET'dagi joriy ranking'ni DB'ga
arxivlash. Maqsad:
  1. Tarixiy ko'rinish (oldingi haftalar reytingi)
  2. Disaster recovery (Redis crash bo'lsa snapshot bor)
  3. Analytics — ClickHouse'ga export uchun yengil base

Idempotent: (period, period_key, scope_kind, scope_id) unique.
Top-N (default 1000) — top entries JSONField'da. Past pozitsiyalar archive
qilinmaydi (ko'p ma'lumot, kam qiymat).

Boshqa modellar (UserStreak, League, ...) — kelajakdagi Task 9 ichida.
"""

import uuid

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
