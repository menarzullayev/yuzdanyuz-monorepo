"""
Task 4 — Exam Engine Celery tasks.

Tasklar:
  - publish_scheduled_mocks   → har 5 daqiqada beat. DRAFT mocks yetib kelsa publish.
  - quarantine_check          → har bir QuestionDispute create'da signal'dan chaqiriladi.
                                Disputes >= MIN_DISPUTES va ratio >= QUARANTINE_THRESHOLD
                                bo'lsa, savolni quarantine + affected attempts'ga bonus.
  - finalize_attempt_score    → ExamAttempt submit'dan keyin chaqiriladi.
                                correct_count, total_points, score'ni recompute qiladi.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.tenant import unscoped_context

logger = logging.getLogger(__name__)

# Quarantine threshold sozlamalari (production-da settings.py'ga ko'chirish mumkin)
QUARANTINE_MIN_DISPUTES = getattr(settings, 'QUARANTINE_MIN_DISPUTES', 5)
QUARANTINE_RATIO_THRESHOLD = getattr(settings, 'QUARANTINE_RATIO_THRESHOLD', 0.05)


# ── 1. Auto-publish weekly mocks ──────────────────────────────────────────────
@shared_task(name='exams.publish_scheduled_mocks')
def publish_scheduled_mocks():
    """
    DRAFT statusidagi mocks'ni scheduled_at vaqti yetganda PUBLISHED'ga o'zgartiradi.

    Beat schedule: har 5 daqiqada (django-celery-beat admin'da boshqariladi).
    Idempotent: ikki marta ishlasa hech narsa qilmaydi.
    """
    from apps.exams.models import MockExam

    now = timezone.now()
    with unscoped_context():
        ready_mocks = MockExam.objects.filter(
            status=MockExam.Status.DRAFT,
            scheduled_at__lte=now,
        )

        published_count = 0
        for mock in ready_mocks:
            # Mock'da kamida bitta savol bor bo'lishi kerak
            if not mock.question_links.exists():
                logger.warning('Mock %s scheduled but has no questions — skipping publish', mock.id)
                continue
            mock.status = MockExam.Status.PUBLISHED
            mock.save(update_fields=['status', 'updated_at'])
            published_count += 1
            logger.info('Published mock %s (%s)', mock.id, mock.title)

    return {'published': published_count}


# ── 2. Quarantine check ───────────────────────────────────────────────────────
@shared_task(name='exams.quarantine_check')
def quarantine_check(question_version_id: str):
    """
    Bir QuestionVersion uchun dispute statistikasini hisoblab, agar threshold'dan
    o'tsa quarantine qiladi va affected attempt'larni recompute qilinishga jo'natadi.

    Trigger: QuestionDispute post_save signal'dan (apps/exams/signals.py).
    Idempotent: allaqachon quarantined bo'lsa hech narsa qilmaydi.
    """
    from apps.catalog.models import QuestionVersion
    from apps.exams.models import QuestionDispute, UserAnswer

    with unscoped_context():
        try:
            qv = QuestionVersion.objects.get(pk=question_version_id)
        except QuestionVersion.DoesNotExist:
            logger.warning('quarantine_check: QuestionVersion %s not found', question_version_id)
            return {'status': 'not_found'}

        if qv.is_quarantined:
            return {'status': 'already_quarantined'}

        total_answers = UserAnswer.objects.filter(question_version=qv).count()
        dispute_count = QuestionDispute.objects.filter(question_version=qv).count()

        if dispute_count < QUARANTINE_MIN_DISPUTES:
            return {
                'status': 'below_min',
                'disputes': dispute_count,
                'threshold': QUARANTINE_MIN_DISPUTES,
            }

        if total_answers == 0:
            ratio = 1.0  # Hech kim ishlamagan, lekin shikoyatlar bor → suspect
        else:
            ratio = dispute_count / total_answers

        if ratio < QUARANTINE_RATIO_THRESHOLD:
            return {'status': 'below_ratio', 'ratio': ratio}

        # Quarantine: atomic transaction
        with transaction.atomic():
            qv.is_quarantined = True
            qv.save(update_fields=['is_quarantined'])

            # Affected UserAnswer'larni auto_correct = True qilish
            updated_answers = UserAnswer.objects.filter(question_version=qv).update(
                auto_correct=True
            )

            # Affected dispute'larni QUARANTINED status qilish
            QuestionDispute.objects.filter(question_version=qv).update(
                status=QuestionDispute.Status.QUARANTINED
            )

        # Affected attempt'lar score'ini recompute qilish (parallel tasks)
        affected_attempt_ids = (
            UserAnswer.objects.filter(question_version=qv, attempt__isnull=False)
            .values_list('attempt_id', flat=True)
            .distinct()
        )
        for attempt_id in affected_attempt_ids:
            finalize_attempt_score.delay(str(attempt_id))

        logger.info(
            'Quarantined QuestionVersion %s — %d disputes / %d answers (%.2f%%) — '
            '%d answers updated, %d attempts recompute queued',
            qv.id,
            dispute_count,
            total_answers,
            ratio * 100,
            updated_answers,
            len(affected_attempt_ids),
        )

        return {
            'status': 'quarantined',
            'disputes': dispute_count,
            'total_answers': total_answers,
            'ratio': ratio,
            'updated_answers': updated_answers,
            'recompute_attempts': len(affected_attempt_ids),
        }


# ── 3. Finalize attempt score ─────────────────────────────────────────────────
@shared_task(name='exams.finalize_attempt_score')
def finalize_attempt_score(attempt_id: str):
    """
    ExamAttempt submit'dan keyin yoki quarantine recompute paytida chaqiriladi.

    Hisob:
      - correct_count = is_correct=True YOKI auto_correct=True bo'lgan answer'lar soni
      - total_points  = correct answer'lar uchun MockExamQuestion.points sum
      - score         = (achieved / max_points) * 100
    """
    from apps.exams.models import ExamAttempt, MockExamQuestion, UserAnswer

    with unscoped_context():
        try:
            attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id)
        except ExamAttempt.DoesNotExist:
            logger.warning('finalize_attempt_score: ExamAttempt %s not found', attempt_id)
            return {'status': 'not_found'}

        # Mock'dagi har savolning ball'i
        question_points = dict(
            MockExamQuestion.objects.filter(mock_exam=attempt.exam).values_list(
                'question_version_id', 'points'
            )
        )
        max_points = sum(question_points.values())

        # Bu attempt'ning javoblari
        answers = UserAnswer.objects.filter(attempt=attempt)

        achieved = 0
        correct_count = 0
        for ans in answers:
            if ans.is_correct or ans.auto_correct:
                achieved += question_points.get(ans.question_version_id, 0)
                correct_count += 1

        attempt.correct_count = correct_count
        attempt.total_points = achieved
        attempt.score = round((achieved / max_points) * 100, 2) if max_points > 0 else 0
        attempt.save(update_fields=['correct_count', 'total_points', 'score', 'updated_at'])

        return {
            'status': 'finalized',
            'attempt_id': str(attempt_id),
            'correct_count': correct_count,
            'achieved_points': achieved,
            'max_points': max_points,
            'score': float(attempt.score),
        }
