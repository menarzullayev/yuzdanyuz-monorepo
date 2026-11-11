"""
Question quality statistics (savol sifati).

Per organization and QuestionVersion we count, from exams.UserAnswer:

  attempts       = graded answers + skips
  correct_count  = non-skip answers with is_correct=True
  skipped_count  = SKIPS, defined as the union of
                     (a) a UserAnswer row whose `selected` is empty
                         (JSON null, {}, [] or ""), and
                     (b) an implicit skip: a SUBMITTED ExamAttempt whose exam pins the
                         question version (MockExamQuestion) but has no UserAnswer row
                         for it. The exam engine stores no row for questions the student
                         never touched, so without (b) skips would be invisible.

What is NOT counted:
  - answers with a non-empty selection but is_correct IS NULL (not graded yet, e.g.
    open-ended awaiting grading);
  - answers with auto_correct=True and all data of quarantined versions (the quarantine
    bonus makes them meaningless for quality);
  - soft-deleted answers/attempts;
  - answers of attempts that are not SUBMITTED (in progress, cancelled, ...).
  Practice-session answers count as explicit answers/skips only (a session has no fixed
  question list, so there are no implicit skips).

Flags need at least MIN_ATTEMPTS_FOR_FLAGS attempts; below PRELIMINARY_BELOW_ATTEMPTS the
stat is marked preliminary. All comparisons use integer math (no float edge cases at 60%).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, F, JSONField, Q, Value
from django.utils import timezone

from apps.exams.models import ExamAttempt, UserAnswer

from .models import QuestionQualityStat

logger = logging.getLogger(__name__)

MIN_ATTEMPTS_FOR_FLAGS = 8
PRELIMINARY_BELOW_ATTEMPTS = 20
SKIP_RATE_THRESHOLD_PERCENT = 60

# JSON values that mean "nothing selected".
_EMPTY_SELECTED = (
    Q(selected={}) | Q(selected=[]) | Q(selected='') | Q(selected=Value(None, JSONField()))
)


@dataclass(frozen=True)
class Counts:
    attempts: int = 0
    correct: int = 0
    skipped: int = 0


def evaluate_flags(attempts: int, correct: int, skipped: int) -> list[str]:
    """Pure function: flags for one question version. Empty below MIN_ATTEMPTS_FOR_FLAGS."""
    if attempts < MIN_ATTEMPTS_FOR_FLAGS:
        return []
    flags = []
    # All-skipped is a wording problem, not an answer-key problem, so ALL_WRONG needs at
    # least one real (non-skip) answer.
    if correct == 0 and skipped < attempts:
        flags.append(QuestionQualityStat.Flag.ALL_WRONG)
    if correct == attempts:
        flags.append(QuestionQualityStat.Flag.ALL_RIGHT)
    if skipped * 100 >= SKIP_RATE_THRESHOLD_PERCENT * attempts:
        flags.append(QuestionQualityStat.Flag.OFTEN_SKIPPED)
    return [str(f) for f in flags]


def compute_counts(org) -> dict:
    """question_version_id -> Counts for one organization (every query is org-filtered)."""
    submitted = ExamAttempt.Status.SUBMITTED
    valid_attempt = Q(attempt__isnull=True) | Q(
        attempt__status=submitted, attempt__is_deleted=False
    )
    base = UserAnswer.objects.filter(
        valid_attempt,
        organization=org,
        is_deleted=False,
        auto_correct=False,
        question_version__is_quarantined=False,
    )

    explicit_skips: dict = defaultdict(int)
    graded: dict = defaultdict(int)
    correct: dict = defaultdict(int)
    rows = base.values('question_version_id').annotate(
        skipped=Count('id', filter=_EMPTY_SELECTED),
        graded=Count('id', filter=~_EMPTY_SELECTED & Q(is_correct__isnull=False)),
        correct=Count('id', filter=~_EMPTY_SELECTED & Q(is_correct=True)),
    )
    for r in rows:
        qv = r['question_version_id']
        explicit_skips[qv] = r['skipped']
        graded[qv] = r['graded']
        correct[qv] = r['correct']

    # Implicit skips: submitted attempts whose exam pins the version but have no row for it.
    attempts_with_row: dict = {}
    for r in (
        UserAnswer.objects.filter(
            organization=org,
            is_deleted=False,
            attempt__status=submitted,
            attempt__is_deleted=False,
        )
        .values('question_version_id')
        .annotate(n=Count('attempt_id', distinct=True))
    ):
        attempts_with_row[r['question_version_id']] = r['n']

    exposed: dict = {}
    for r in (
        ExamAttempt.objects.filter(
            organization=org,
            status=submitted,
            is_deleted=False,
            exam__question_links__question_version__is_quarantined=False,
        )
        .values(qv=F('exam__question_links__question_version_id'))
        .annotate(n=Count('id', distinct=True))
    ):
        exposed[r['qv']] = r['n']

    result = {}
    for qv in set(graded) | set(explicit_skips) | set(exposed):
        implicit = max(0, exposed.get(qv, 0) - attempts_with_row.get(qv, 0))
        skipped = explicit_skips[qv] + implicit
        attempts = graded[qv] + skipped
        if attempts:
            result[qv] = Counts(attempts=attempts, correct=correct[qv], skipped=skipped)
    return result


def refresh_for_org(org) -> dict:
    """Recompute and upsert all stats of one organization. Idempotent."""
    now = timezone.now()
    counts = compute_counts(org)
    objs = [
        QuestionQualityStat(
            organization=org,
            question_version_id=qv_id,
            attempts=c.attempts,
            correct_count=c.correct,
            skipped_count=c.skipped,
            correct_rate=c.correct / c.attempts,
            skip_rate=c.skipped / c.attempts,
            flags=evaluate_flags(c.attempts, c.correct, c.skipped),
            is_preliminary=c.attempts < PRELIMINARY_BELOW_ATTEMPTS,
            computed_at=now,
        )
        for qv_id, c in counts.items()
    ]
    QuestionQualityStat.objects.bulk_create(
        objs,
        batch_size=500,
        update_conflicts=True,
        unique_fields=['organization', 'question_version'],
        update_fields=[
            'attempts',
            'correct_count',
            'skipped_count',
            'correct_rate',
            'skip_rate',
            'flags',
            'is_preliminary',
            'computed_at',
            'updated_at',
        ],
    )
    # Derived cache: drop rows whose source data disappeared (soft-deleted, quarantined).
    stale = QuestionQualityStat.objects.filter(organization=org).exclude(
        question_version_id__in=list(counts)
    )
    removed = stale.count()
    stale.delete()
    return {
        'org': str(org.id),
        'versions': len(objs),
        'flagged': sum(1 for o in objs if o.flags),
        'removed': removed,
    }
