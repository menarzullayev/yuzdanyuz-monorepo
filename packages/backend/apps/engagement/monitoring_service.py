"""Live exam monitoring: who is sitting an exam right now, how far along, any strikes.

Three queries regardless of page size: attempts (annotated counts), questions-per-exam is
folded into that query, and the latest anti-cheat events via a window function.
ExamAttempt.objects is tenant-scoped, so a teacher only ever sees their own org.
"""

from datetime import timedelta

from django.db.models import (
    Count,
    DateTimeField,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
    Value,
    Window,
)
from django.db.models.functions import RowNumber
from django.utils import timezone

from apps.exams.models import AntiCheatEvent, ExamAttempt

LIVE_GRACE_MINUTES = 5  # attempts past exam duration + grace are zombies awaiting expiry
LATEST_EVENTS = 3
DEFAULT_LIMIT = 100
MAX_LIMIT = 200


def live_attempts(*, exam_id=None, min_strikes=0, limit=DEFAULT_LIMIT):
    now = timezone.now()
    minutes = ExpressionWrapper(
        F('exam__duration_minutes') * Value(timedelta(minutes=1)), output_field=DurationField()
    )
    qs = (
        ExamAttempt.objects.filter(status=ExamAttempt.Status.IN_PROGRESS, is_deleted=False)
        .annotate(
            deadline=ExpressionWrapper(F('started_at') + minutes, output_field=DateTimeField())
        )
        .filter(deadline__gte=now - timedelta(minutes=LIVE_GRACE_MINUTES))
        .select_related('user', 'exam')
        .annotate(
            answered=Count('answers', filter=Q(answers__is_deleted=False), distinct=True),
            total_questions=Count('exam__question_links', distinct=True),
        )
        .order_by('-started_at')
    )
    if exam_id:
        qs = qs.filter(exam_id=exam_id)
    if min_strikes:
        qs = qs.filter(strikes__gte=min_strikes)
    attempts = list(qs[:limit])

    events_by_attempt: dict = {}
    if attempts:
        events = (
            AntiCheatEvent.objects.filter(attempt_id__in=[a.id for a in attempts])
            .annotate(
                rn=Window(
                    RowNumber(), partition_by=F('attempt_id'), order_by=F('occurred_at').desc()
                )
            )
            .filter(rn__lte=LATEST_EVENTS)
            .order_by('attempt_id', '-occurred_at')
        )
        for ev in events:
            events_by_attempt.setdefault(ev.attempt_id, []).append(
                {'event_type': ev.event_type, 'occurred_at': ev.occurred_at}
            )

    results = []
    for a in attempts:
        hb = a.heartbeat_last_at
        results.append(
            {
                'attempt_id': str(a.id),
                'student': {
                    'id': str(a.user_id) if a.user_id else None,
                    'username': a.user.username if a.user else None,
                    'name': a.user.display_name if a.user else None,
                },
                'exam': {
                    'id': str(a.exam_id),
                    'title': a.exam.title,
                    'duration_minutes': a.exam.duration_minutes,
                },
                'started_at': a.started_at,
                'progress': {'answered': a.answered, 'total': a.total_questions},
                'last_heartbeat_at': hb,
                'heartbeat_age_seconds': int((now - hb).total_seconds()) if hb else None,
                'strikes': a.strikes,
                'latest_events': events_by_attempt.get(a.id, []),
            }
        )
    return {'server_time': now, 'count': len(results), 'results': results}
