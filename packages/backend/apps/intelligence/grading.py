"""Grading helpers: AI <-> teacher calibration math and the final-score rules."""

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from .models import OpenEndedSubmission
from .tasks import SPEAKING_CRITERIA, WRITING_CRITERIA

# An AI criterion score agrees with the teacher's when they differ by at most this many points (0-100 scale).
CALIBRATION_TOLERANCE = 10.0
CALIBRATION_MIN_PAIRS = 30
CALIBRATION_WINDOW_DAYS = 365
CALIBRATION_CACHE_SECONDS = 600
# |mean difference| up to this many points is reported as "aligned" rather than a bias.
BIAS_NEUTRAL_BAND = 2.0

CALIBRATION_CAVEAT = (
    'Teachers saw the AI estimate before setting the final score, so the agreement rate '
    'may be inflated (anchoring).'
)

# API-facing exam type -> submission_type
EXAM_TYPES = {
    'writing': OpenEndedSubmission.Type.ESSAY,
    'speaking': OpenEndedSubmission.Type.AUDIO,
}
CRITERIA_BY_TYPE = {
    OpenEndedSubmission.Type.ESSAY: WRITING_CRITERIA,
    OpenEndedSubmission.Type.AUDIO: SPEAKING_CRITERIA,
}


def _stats(diffs: list[float]) -> dict:
    n = len(diffs)
    return {
        'n': n,
        'mean_diff': round(sum(diffs) / n, 2),
        'mean_abs_diff': round(sum(abs(d) for d in diffs) / n, 2),
        'agreement_rate': round(sum(1 for d in diffs if abs(d) <= CALIBRATION_TOLERANCE) / n, 4),
    }


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    return float(value)


def calibration_report(rows: list[dict]) -> dict:
    """rows: {'ai': score, 'human': score, 'ai_criteria': {..}, 'human_criteria': {..}}.

    diff = AI - teacher. Numbers are returned only with at least CALIBRATION_MIN_PAIRS pairs
    (per criterion: at least that many pairs having that criterion on both sides).
    """
    base = {
        'window_days': CALIBRATION_WINDOW_DAYS,
        'tolerance': CALIBRATION_TOLERANCE,
        'min_pairs': CALIBRATION_MIN_PAIRS,
        'pairs': len(rows),
        'caveat': CALIBRATION_CAVEAT,
    }
    if len(rows) < CALIBRATION_MIN_PAIRS:
        return {
            **base,
            'status': 'not_enough_data',
            'conclusion': None,
            'message': 'not enough data',
            'overall': None,
            'criteria': {},
        }

    overall_diffs = [r['ai'] - r['human'] for r in rows]
    per_criterion: dict[str, list[float]] = {}
    for r in rows:
        for key, human in (r['human_criteria'] or {}).items():
            ai = _number((r['ai_criteria'] or {}).get(key))
            human = _number(human)
            if ai is None or human is None:
                continue
            per_criterion.setdefault(key, []).append(ai - human)

    criteria = {}
    for key, diffs in sorted(per_criterion.items()):
        if len(diffs) < CALIBRATION_MIN_PAIRS:
            criteria[key] = {'n': len(diffs), 'status': 'not_enough_data'}
        else:
            criteria[key] = {'status': 'ok', **_stats(diffs)}

    overall = _stats(overall_diffs)
    mean = overall['mean_diff']
    bias = (
        'aligned' if abs(mean) <= BIAS_NEUTRAL_BAND else ('ai_higher' if mean > 0 else 'ai_lower')
    )
    return {
        **base,
        'status': 'ok',
        'conclusion': {
            'bias': bias,
            'agreement_rate': overall['agreement_rate'],
            'mean_diff': mean,
        },
        'message': '',
        'overall': overall,
        'criteria': criteria,
    }


def calibration_rows(queryset) -> list[dict]:
    since = timezone.now() - timedelta(days=CALIBRATION_WINDOW_DAYS)
    rows = []
    for sub in queryset.filter(
        is_deleted=False,
        ai_score__isnull=False,
        human_score__isnull=False,
        human_completed_at__gte=since,
    ).only('ai_score', 'human_score', 'ai_feedback', 'human_criteria'):
        rows.append(
            {
                'ai': float(sub.ai_score),
                'human': float(sub.human_score),
                'ai_criteria': sub.ai_feedback,
                'human_criteria': sub.human_criteria,
            }
        )
    return rows


def cached_calibration(queryset, *, org_id, exam_type: str | None) -> dict:
    """10-minute cache keyed by scope; the report is deliberately not invalidated on grading."""
    key = f'intelligence:calibration:{org_id or "all"}:{exam_type or "all"}'
    report = cache.get(key)
    if report is None:
        report = calibration_report(calibration_rows(queryset))
        cache.set(key, report, CALIBRATION_CACHE_SECONDS)
    return report
