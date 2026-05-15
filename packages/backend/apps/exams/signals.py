"""
Exams signals.

  - QuestionDispute create   → quarantine_check.delay()
  - ExamAttempt SUBMITTED    → leaderboard.record_attempt() (Task 5)

Quarantine task idempotent — allaqachon quarantined bo'lsa skip.
Leaderboard fail-safe — Redis xato bo'lsa asosiy flow buzilmaydi.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.engagement import leaderboard

from .models import ExamAttempt, QuestionDispute
from .tasks import quarantine_check


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
