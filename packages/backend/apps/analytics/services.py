"""
Analytics service — dashboard queries + event recording.

Stub mode: faqat PostgreSQL ExamEvent jadvalidan SQL aggregation.
Production: ClickHouse'ga parallel write + heavy aggregation queries CH'dan.

Multi-tenant: barcha funksiyalar `org` parametrini oladi va shunga filter qiladi.
ExamEvent.global_objects ishlatamiz (tenant context bo'lmagan Celery'dan ham
chaqirilishi mumkin), `organization=org` explicit filter bilan.
"""

from datetime import timedelta

from django.db.models import Avg, Count, FloatField
from django.db.models.functions import TruncWeek
from django.utils import timezone

from . import clickhouse_client
from .models import ExamEvent


def record_exam_event(attempt) -> ExamEvent | None:
    """
    ExamAttempt SUBMITTED bo'lganda chaqiriladi (signal'dan).
    Idempotent: shu attempt uchun event mavjud bo'lsa skip.
    """
    if attempt.score is None:
        return None
    if ExamEvent.global_objects.filter(exam_attempt_id=attempt.id).exists():
        return None

    subject_id = None
    subject_name = ''
    first_link = attempt.exam.question_links.select_related(
        'question_version__question__subject'
    ).first()
    if first_link:
        subj = first_link.question_version.question.subject
        if subj:
            subject_id = subj.id
            subject_name = subj.name

    duration = 0
    if attempt.submitted_at and attempt.started_at:
        duration = int((attempt.submitted_at - attempt.started_at).total_seconds())

    event = ExamEvent.objects.create(
        organization=attempt.organization,
        user=attempt.user,
        exam_attempt_id=attempt.id,
        mock_exam_id=attempt.exam_id,
        subject_id=subject_id,
        subject_name=subject_name,
        score=attempt.score,
        correct_count=attempt.correct_count,
        total_count=attempt.exam.question_links.count(),
        duration_seconds=duration,
        region_id=attempt.user.region_id,
        completed_at=attempt.submitted_at or timezone.now(),
    )

    # Stub mode: no-op. Production: parallel ClickHouse write.
    try:
        clickhouse_client.write_event(
            {
                'event_id': str(event.id),
                'organization_id': str(attempt.organization_id),
                'user_id': str(attempt.user_id),
                'subject_id': str(subject_id) if subject_id else None,
                'score': float(attempt.score),
                'completed_at': event.completed_at.isoformat(),
            }
        )
    except Exception:
        pass

    return event


# ─── Dashboard queries ────────────────────────────────────────────────────────


def get_subject_averages(org, *, days: int = 30) -> list[dict]:
    """Bar chart: har subject bo'yicha o'rtacha ball (oxirgi N kun)."""
    cutoff = timezone.now() - timedelta(days=days)
    qs = (
        ExamEvent.global_objects.filter(organization=org, completed_at__gte=cutoff)
        .values('subject_id', 'subject_name')
        .annotate(avg_score=Avg('score', output_field=FloatField()), event_count=Count('id'))
        .order_by('-avg_score')
    )
    return [
        {
            'subject_id': str(r['subject_id']) if r['subject_id'] else None,
            'subject_name': r['subject_name'] or 'Aniqlanmagan',
            'avg_score': round(r['avg_score'] or 0, 2),
            'event_count': r['event_count'],
        }
        for r in qs
    ]


def get_weekly_growth(org, *, weeks: int = 12) -> list[dict]:
    """Line chart: haftalik o'sish dinamikasi (week → avg_score, count)."""
    cutoff = timezone.now() - timedelta(weeks=weeks)
    qs = (
        ExamEvent.global_objects.filter(organization=org, completed_at__gte=cutoff)
        .annotate(week=TruncWeek('completed_at'))
        .values('week')
        .annotate(avg_score=Avg('score', output_field=FloatField()), event_count=Count('id'))
        .order_by('week')
    )
    return [
        {
            'week': r['week'].date().isoformat(),
            'avg_score': round(r['avg_score'] or 0, 2),
            'event_count': r['event_count'],
        }
        for r in qs
    ]


def get_score_distribution(org, *, days: int = 30) -> list[dict]:
    """Pie chart: score taqsimoti (0-25, 26-50, 51-75, 76-100)."""
    cutoff = timezone.now() - timedelta(days=days)
    events = ExamEvent.global_objects.filter(
        organization=org, completed_at__gte=cutoff
    ).values_list('score', flat=True)

    buckets = {'0-25': 0, '26-50': 0, '51-75': 0, '76-100': 0}
    for score in events:
        s = float(score)
        if s <= 25:
            buckets['0-25'] += 1
        elif s <= 50:
            buckets['26-50'] += 1
        elif s <= 75:
            buckets['51-75'] += 1
        else:
            buckets['76-100'] += 1

    return [{'bucket': k, 'count': v} for k, v in buckets.items()]


def get_weak_students(org, *, limit: int = 10, days: int = 30) -> list[dict]:
    """Eng zaif o'quvchilar (oxirgi N kun avg_score past)."""
    cutoff = timezone.now() - timedelta(days=days)
    qs = (
        ExamEvent.global_objects.filter(organization=org, completed_at__gte=cutoff)
        .values('user_id', 'user__username', 'user__email')
        .annotate(avg_score=Avg('score', output_field=FloatField()), event_count=Count('id'))
        .filter(event_count__gte=2)
        .order_by('avg_score')[:limit]
    )
    return [
        {
            'user_id': str(r['user_id']),
            'username': r['user__username'],
            'email': r['user__email'],
            'avg_score': round(r['avg_score'] or 0, 2),
            'event_count': r['event_count'],
        }
        for r in qs
    ]


def get_dashboard_summary(org, *, days: int = 30) -> dict:
    """Dashboard overview — barcha widget data bir joyda."""
    return {
        'period_days': days,
        'subject_averages': get_subject_averages(org, days=days),
        'weekly_growth': get_weekly_growth(org, weeks=12),
        'score_distribution': get_score_distribution(org, days=days),
        'weak_students': get_weak_students(org, limit=10, days=days),
        'total_events': ExamEvent.global_objects.filter(
            organization=org, completed_at__gte=timezone.now() - timedelta(days=days)
        ).count(),
    }
