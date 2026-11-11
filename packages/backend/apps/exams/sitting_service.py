"""
Sitting workflow: calculate (Rasch) and publish (certificates).

State transitions open/close live on ExamSitting. Everything here raises ValueError for
state-machine violations (views map it to HTTP 409) and runs inside the sitting's tenant.
"""

import logging
import secrets

from django.db import IntegrityError, transaction
from django.dispatch import Signal
from django.utils import timezone

from core.tenant import tenant_context

from . import rasch
from .models import ExamAttempt, MockExamQuestion, UserAnswer
from .sitting_models import (
    CERTIFICATE_VALIDITY_YEARS,
    Certificate,
    ExamSitting,
    SittingItem,
    SittingResult,
    add_years,
)

logger = logging.getLogger(__name__)

# Sent after a certificate row is committed. Telegram delivery is deliberately NOT done here:
# another session is reworking Telegram auth. Receivers get `certificate=<Certificate>`.
certificate_issued = Signal()

# No 0/O/1/I/L: numbers are read aloud and typed by hand.
_NUMBER_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def notify_certificate_issued(certificate: Certificate) -> None:
    """Hook point for student notification.

    TODO(telegram): deliver the certificate message once the Telegram auth rework lands.
    For now only the `certificate_issued` signal is emitted.
    """
    certificate_issued.send(sender=Certificate, certificate=certificate)


def generate_certificate_number(year: int) -> str:
    body = ''.join(secrets.choice(_NUMBER_ALPHABET) for _ in range(8))
    return f'YD-{year}-{body}'


def normalize_number(raw: str) -> str:
    return (raw or '').strip().upper()


def holder_initials(user) -> str:
    if user is None:
        return ''
    parts = [p for p in (user.first_name, user.last_name) if p and p.strip()]
    return ''.join(f'{p.strip()[0].upper()}.' for p in parts)


def collect_attempts(sitting: ExamSitting):
    """SUBMITTED attempts of the sitting's mock that started inside the sitting window."""
    return ExamAttempt.objects.filter(
        exam_id=sitting.mock_exam_id,
        status=ExamAttempt.Status.SUBMITTED,
        is_deleted=False,
        started_at__gte=sitting.opens_at,
        started_at__lte=sitting.closes_at,
    ).order_by('started_at', 'id')


def calculate(sitting: ExamSitting) -> ExamSitting:
    """CLOSED|CALCULATED -> CALCULATED. Replaces earlier results of this sitting."""
    sitting.validate_transition(ExamSitting.Status.CALCULATED)

    with tenant_context(sitting.organization), transaction.atomic():
        qv_ids = list(
            MockExamQuestion.objects.filter(mock_exam_id=sitting.mock_exam_id)
            .order_by('order')
            .values_list('question_version_id', flat=True)
        )
        if len(qv_ids) < 2:
            raise ValueError('Sitting needs a mock exam with at least 2 questions.')
        attempts = list(collect_attempts(sitting))
        if not attempts:
            raise ValueError('No submitted attempts in the sitting window.')

        correct = set(
            UserAnswer.objects.filter(attempt__in=attempts, is_deleted=False)
            .filter(is_correct=True)
            .values_list('attempt_id', 'question_version_id')
        ) | set(
            UserAnswer.objects.filter(
                attempt__in=attempts, is_deleted=False, auto_correct=True
            ).values_list('attempt_id', 'question_version_id')
        )
        # Skipped questions count as wrong (0).
        matrix = [[1 if (a.id, qv) in correct else 0 for qv in qv_ids] for a in attempts]

        fit = rasch.calibrate(matrix)
        scale = sitting.grade_scale

        SittingResult.objects.filter(sitting=sitting).delete()
        SittingItem.objects.filter(sitting=sitting).delete()

        SittingItem.objects.bulk_create(
            SittingItem(
                organization=sitting.organization,
                sitting=sitting,
                question_version_id=qv,
                difficulty=fit.item_difficulty[i],
                correct_count=fit.item_correct[i],
                attempt_count=len(attempts),
            )
            for i, qv in enumerate(qv_ids)
        )

        results = []
        for idx, attempt in enumerate(attempts):
            scaled = scale.scale_ability(fit.person_ability[idx])
            band = scale.band_for(scaled)
            results.append(
                SittingResult(
                    organization=sitting.organization,
                    sitting=sitting,
                    attempt=attempt,
                    user_id=attempt.user_id,
                    raw_score=fit.person_raw[idx],
                    max_raw_score=len(qv_ids),
                    ability=fit.person_ability[idx],
                    ability_se=fit.person_se[idx],
                    scaled_score=scaled,
                    grade=band['grade'],
                    certificate_eligible=bool(band['certificate']),
                )
            )
        SittingResult.objects.bulk_create(results)

        sitting.status = ExamSitting.Status.CALCULATED
        sitting.calculated_at = timezone.now()
        sitting.calibration = {
            **fit.meta,
            'iterations': fit.iterations,
            'converged': fit.converged,
            'n_persons': fit.n_persons,
            'n_items': fit.n_items,
        }
        sitting.save(update_fields=['status', 'calculated_at', 'calibration', 'updated_at'])
    return sitting


def _create_certificate(result: SittingResult, issued_at) -> Certificate:
    for _attempt in range(5):
        try:
            with transaction.atomic():
                return Certificate.objects.create(
                    organization=result.organization,
                    number=generate_certificate_number(issued_at.year),
                    user=result.user,
                    sitting=result.sitting,
                    result=result,
                    holder_initials=holder_initials(result.user),
                    scaled_score=result.scaled_score,
                    grade=result.grade,
                    issued_at=issued_at,
                    valid_until=add_years(issued_at, CERTIFICATE_VALIDITY_YEARS),
                )
        except IntegrityError as exc:
            if Certificate.objects.filter(result=result).exists():
                raise  # not a number collision
            logger.warning('certificate number collision, retrying: %s', exc)
    raise RuntimeError('Could not generate a unique certificate number.')


def publish(sitting: ExamSitting) -> list[Certificate]:
    """CALCULATED -> PUBLISHED. Issues certificates for C and above, then reveals results."""
    sitting.validate_transition(ExamSitting.Status.PUBLISHED)

    issued: list[Certificate] = []
    with tenant_context(sitting.organization), transaction.atomic():
        now = timezone.now()
        eligible = SittingResult.objects.filter(
            sitting=sitting, certificate_eligible=True, certificate__isnull=True
        ).select_related('user')
        for result in eligible:
            issued.append(_create_certificate(result, now))

        sitting.status = ExamSitting.Status.PUBLISHED
        sitting.published_at = now
        sitting.save(update_fields=['status', 'published_at', 'updated_at'])

        for cert in issued:
            transaction.on_commit(lambda c=cert: notify_certificate_issued(c))
    return issued
