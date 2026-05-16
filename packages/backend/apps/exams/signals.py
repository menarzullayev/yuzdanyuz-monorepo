"""
Exams signals.

  - QuestionDispute create   → quarantine_check.delay()
  - ExamAttempt SUBMITTED    → leaderboard.record_attempt() (Task 5)
  - UserAnswer create+graded → intelligence.update_user_mastery (Task 6)

Quarantine task idempotent — allaqachon quarantined bo'lsa skip.
Leaderboard fail-safe — Redis xato bo'lsa asosiy flow buzilmaydi.
Mastery update fail-safe — SkillTag bo'lmasa silently skip.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.engagement import leaderboard

from .models import ExamAttempt, QuestionDispute, UserAnswer
from .tasks import quarantine_check

logger = logging.getLogger(__name__)


@receiver(post_save, sender=QuestionDispute)
def trigger_quarantine_check(sender, instance, created, **kwargs):
    if not created:
        return
    quarantine_check.delay(str(instance.question_version_id))


@receiver(post_save, sender=ExamAttempt)
def update_leaderboard_on_submit(sender, instance, created, update_fields=None, **kwargs):
    """
    ExamAttempt SUBMITTED + score'i bor → leaderboard'ga yozish.

    Idempotent: agar score eski max'dan past bo'lsa global/region/tenant ZSET
    o'zgarmaydi (leaderboard service ichidagi GT semantics).
    """
    if instance.status != ExamAttempt.Status.SUBMITTED:
        return
    if instance.score is None:
        # finalize_attempt_score hali ishlatmagan — score keyinroq keladi.
        return

    user = instance.user
    leaderboard.record_attempt(
        user_id=user.id,
        score=instance.score,
        mock_id=instance.exam_id,
        region_id=user.region_id,
        org_id=instance.organization_id,
    )

    # Task 8 — analytics event recording
    try:
        from apps.analytics.services import record_exam_event

        record_exam_event(instance)
    except Exception as e:
        logger.warning('record_exam_event failed for attempt %s: %s', instance.id, e)


@receiver(post_save, sender=UserAnswer)
def update_mastery_on_answer(sender, instance, created, **kwargs):
    """
    UserAnswer create + is_correct aniqlangan bo'lsa — Bayesian mastery update.
    SkillTag M2M bo'sh bo'lsa silently skip (no-op).
    """
    if instance.is_correct is None:
        return

    try:
        # Lazy import — circular import oldini olish (intelligence imports catalog)
        from apps.intelligence.services import update_user_mastery

        # Question (parent of QuestionVersion) skill'lari
        question = instance.question_version.question
        update_user_mastery(
            user=instance.attempt.user if instance.attempt else instance.session.user,
            question=question,
            is_correct=bool(instance.is_correct or instance.auto_correct),
        )
    except Exception as e:
        logger.warning('mastery update failed for answer %s: %s', instance.id, e)
