"""
Intelligence signals.

  - UserAnswer saved with is_correct=False → mistake notebook card (create / reset)

Registered here (not in exams) so the exams app stays untouched. Fail-safe: a
notebook error must never break answering an exam question.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.exams.models import UserAnswer

from .mistakes import record_mistake

logger = logging.getLogger(__name__)


@receiver(post_save, sender=UserAnswer, dispatch_uid='intelligence_record_mistake')
def record_mistake_on_wrong_answer(sender, instance, raw=False, **kwargs):
    if raw or instance.is_correct is not False:
        return
    try:
        # savepoint: a failure must not poison the answer's own transaction
        with transaction.atomic():
            record_mistake(instance)
    except Exception as e:
        logger.warning('mistake card failed for answer %s: %s', instance.id, e)
