"""ISSUE-405 — Bridge existing domain signals to webhook dispatch.

Demonstration wiring: ExamAttempt → 'exam.submitted' webhook.

Design notes:
  - Uses post_save on ExamAttempt (mirrors existing pattern in apps/exams/signals.py).
  - Per-row guard `_webhook_fired_for` prevents duplicate fires when the model
    is saved more than once (e.g. score finalization writes status=SUBMITTED
    twice with different update_fields).
  - We deliberately import lazily inside the handler so apps.webhooks.ready()
    stays cheap.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

# Module-level set: attempt IDs we already dispatched 'exam.submitted' for.
# Bounded by process lifetime — acceptable: at worst we re-fire after a worker
# restart (idempotency is the receiver's responsibility, per webhook RFC).
_webhook_fired_for: set[str] = set()


def _exam_attempt_submitted(sender, instance, created, **kwargs):
    """Fire 'exam.submitted' webhook when an ExamAttempt enters SUBMITTED."""
    # Lazy imports: ExamAttempt / dispatch_webhook depend on apps that may
    # not be fully loaded at signal-connection time.
    from apps.exams.models import ExamAttempt

    if not isinstance(instance, ExamAttempt):
        return
    if instance.status != ExamAttempt.Status.SUBMITTED:
        return

    attempt_key = str(instance.pk)
    if attempt_key in _webhook_fired_for:
        return
    _webhook_fired_for.add(attempt_key)

    from .services import dispatch_webhook

    payload = {
        'event': 'exam.submitted',
        'attempt_id': str(instance.id),
        'exam_id': str(instance.exam_id),
        'user_id': instance.user_id,
        'organization_id': str(instance.organization_id),
        'score': float(instance.score) if instance.score is not None else None,
        'correct_count': instance.correct_count,
        'total_points': instance.total_points,
        'submitted_at': instance.submitted_at.isoformat() if instance.submitted_at else None,
    }

    try:
        dispatch_webhook('exam.submitted', payload, org=instance.organization)
    except Exception as exc:
        # Never break the parent transaction over a webhook bookkeeping error.
        logger.exception('dispatch_webhook(exam.submitted) failed: %s', exc)


def _connect():
    """Connect the receiver to ExamAttempt.post_save.

    Done in a function so `apps.py.ready()` can import this module without
    side-effects until Django's app registry is fully populated.
    """
    from apps.exams.models import ExamAttempt

    post_save.connect(
        _exam_attempt_submitted,
        sender=ExamAttempt,
        dispatch_uid='webhooks.exam_attempt_submitted',
    )


# Auto-connect on import (apps.ready calls `from . import signals`).
_connect()
