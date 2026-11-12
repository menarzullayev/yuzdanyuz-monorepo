"""Typing practice: texts per language and the runs users submit.

Both tables are intentionally NOT tenant tables: texts are platform content and the
per-language leaderboard is platform-wide (same as UserStreak / League).
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class TypingText(models.Model):
    class Language(models.TextChoices):
        UZ_LATN = 'uz-Latn', _("O'zbek (lotin)")
        UZ_CYRL = 'uz-Cyrl', _('Ўзбек (кирилл)')
        RU = 'ru', _('Русский')
        EN = 'en', _('English')
        KAA = 'kaa', _('Qaraqalpaq')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    language = models.CharField(max_length=8, choices=Language.choices, db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['language', 'title']

    def __str__(self):
        return f'[{self.language}] {self.title}'


class TypingRun(models.Model):
    """wpm/accuracy are always computed server-side from the keystroke summary."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='typing_runs'
    )
    text = models.ForeignKey(TypingText, on_delete=models.PROTECT, related_name='runs')
    language = models.CharField(max_length=8, db_index=True)  # denormalised from text
    duration_ms = models.PositiveIntegerField()
    typed_chars = models.PositiveIntegerField()
    correct_chars = models.PositiveIntegerField()
    errors = models.PositiveIntegerField()
    wpm = models.DecimalField(max_digits=6, decimal_places=2)
    accuracy = models.DecimalField(max_digits=5, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['language', '-wpm']),
            models.Index(fields=['user', 'language', '-wpm']),
        ]

    def __str__(self):
        return f'{self.user_id} {self.language} {self.wpm} wpm'
