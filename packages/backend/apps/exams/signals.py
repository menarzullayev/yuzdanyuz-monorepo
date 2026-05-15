"""
Exams signals — QuestionDispute → quarantine_check task trigger.

QuestionDispute har create'da `quarantine_check.delay(question_version_id)`
chaqiriladi. Task idempotent (allaqachon quarantined bo'lsa skip), ratio
threshold'dan past bo'lsa hech narsa qilmaydi.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import QuestionDispute
from .tasks import quarantine_check


@receiver(post_save, sender=QuestionDispute)
def trigger_quarantine_check(sender, instance, created, **kwargs):
    if not created:
        return
    quarantine_check.delay(str(instance.question_version_id))
