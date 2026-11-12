"""
Monthly organization report (org admins for their org; platform staff for any org).

Month = calendar month in settings.TIME_ZONE. Definitions:

  attempts          exam attempts with status SUBMITTED, not deleted, score set and
                    submitted_at inside the month.
  active_students   distinct users who STARTED an exam attempt or a practice session in
                    the month (same definition as the funnel).
  avg_score         mean ExamAttempt.score (0..100) of those attempts, 2 decimals.
  pass_rate_percent share of those attempts with score >= pass_score (default 60), 1 decimal.
  *_delta           this month minus previous month (counts: difference; avg_score:
                    points; pass rate: percentage points); null when the previous month
                    has no attempts (nothing to compare with).
  by_exam_type      'platform_mock' (MockExam.is_public), 'org_exam' (own exams) and
                    'practice' (PracticeSession started in the month with total_count > 0;
                    its score is correct/total*100, "attempts" = sessions).
                    Practice is NOT part of the headline attempts/avg/pass numbers.
  top_students      up to 10 by mean score of the month (ties: more attempts, then name).
  certificates      exams.Certificate rows issued in the month: issued = all, valid = not
                    revoked and not expired now, revoked, expired.

PDF is deferred (no PDF library in requirements); CSV (UTF-8 with BOM for Excel) and a
printable HTML page are provided instead.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time
from decimal import Decimal

from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.html import escape

from apps.exams.models import ExamAttempt, PracticeSession
from apps.exams.sitting_models import Certificate

from .finance import add_months

DEFAULT_PASS_SCORE = 60
TOP_N = 10


def _aware(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def _f(value) -> float | None:
    return None if value is None else round(float(value), 2)


def _month_bounds(month_first: date):
    return _aware(month_first), _aware(add_months(month_first, 1))


def _attempts(org, start, end):
    return ExamAttempt.global_objects.filter(
        organization=org,
        status=ExamAttempt.Status.SUBMITTED,
        is_deleted=False,
        score__isnull=False,
        submitted_at__gte=start,
        submitted_at__lt=end,
    )


def _core(org, month_first: date, pass_score) -> dict:
    start, end = _month_bounds(month_first)
    qs = _attempts(org, start, end)
    agg = qs.aggregate(
        n=Count('id'), avg=Avg('score'), passed=Count('id', filter=Q(score__gte=pass_score))
    )
    active = set(
        ExamAttempt.global_objects.filter(
            organization=org,
            is_deleted=False,
            user__isnull=False,
            started_at__gte=start,
            started_at__lt=end,
        ).values_list('user_id', flat=True)
    ) | set(
        PracticeSession.global_objects.filter(
            organization=org, user__isnull=False, started_at__gte=start, started_at__lt=end
        ).values_list('user_id', flat=True)
    )
    n = agg['n']
    return {
        'attempts': n,
        'active_students': len(active),
        'avg_score': _f(agg['avg']),
        'pass_rate_percent': round(100 * agg['passed'] / n, 1) if n else None,
    }


def _delta(cur, prev, has_prev: bool, *, ndigits=2):
    if cur is None or prev is None or not has_prev:
        return None
    return round(cur - prev, ndigits)


def _by_type(org, start, end, pass_score) -> list[dict]:
    rows = []
    qs = _attempts(org, start, end)
    for label, flag in (('platform_mock', True), ('org_exam', False)):
        agg = qs.filter(exam__is_public=flag).aggregate(
            n=Count('id'), avg=Avg('score'), passed=Count('id', filter=Q(score__gte=pass_score))
        )
        rows.append(
            {
                'exam_type': label,
                'attempts': agg['n'],
                'avg_score': _f(agg['avg']),
                'pass_rate_percent': round(100 * agg['passed'] / agg['n'], 1) if agg['n'] else None,
            }
        )
    sessions = PracticeSession.global_objects.filter(
        organization=org, started_at__gte=start, started_at__lt=end, total_count__gt=0
    ).values_list('correct_count', 'total_count')
    pcts = [Decimal(c) * 100 / Decimal(t) for c, t in sessions]
    rows.append(
        {
            'exam_type': 'practice',
            'attempts': len(pcts),
            'avg_score': _f(sum(pcts) / len(pcts)) if pcts else None,
            'pass_rate_percent': (
                round(100 * sum(1 for p in pcts if p >= pass_score) / len(pcts), 1)
                if pcts
                else None
            ),
        }
    )
    return rows


def _top_students(org, start, end) -> list[dict]:
    rows = (
        _attempts(org, start, end)
        .filter(user__isnull=False)
        .values('user_id', 'user__username', 'user__first_name', 'user__last_name')
        .annotate(n=Count('id'), avg=Avg('score'))
    )
    out = []
    for r in rows:
        full = f'{r["user__first_name"]} {r["user__last_name"]}'.strip()
        out.append(
            {
                'user_id': str(r['user_id']),
                'name': full or r['user__username'],
                'attempts': r['n'],
                'avg_score': _f(r['avg']),
            }
        )
    out.sort(key=lambda x: (-x['avg_score'], -x['attempts'], x['name']))
    return out[:TOP_N]


def _certificates(org, start, end) -> dict:
    now = timezone.now()
    qs = Certificate.global_objects.filter(
        organization=org, issued_at__gte=start, issued_at__lt=end
    )
    revoked = qs.filter(revoked_at__isnull=False).count()
    valid = qs.filter(revoked_at__isnull=True, valid_until__gt=now).count()
    return {
        'issued': qs.count(),
        'valid': valid,
        'revoked': revoked,
        'expired': qs.filter(revoked_at__isnull=True, valid_until__lte=now).count(),
    }


def build_report(org, month_first: date, *, pass_score=DEFAULT_PASS_SCORE) -> dict:
    prev_first = add_months(month_first, -1)
    cur = _core(org, month_first, pass_score)
    prev = _core(org, prev_first, pass_score)
    has_prev = prev['attempts'] > 0
    start, end = _month_bounds(month_first)
    return {
        'organization_id': str(org.id),
        'organization_name': org.name,
        'month': month_first.strftime('%Y-%m'),
        'previous_month': prev_first.strftime('%Y-%m'),
        'pass_score': pass_score,
        **cur,
        'previous': prev,
        'deltas': {
            'attempts': cur['attempts'] - prev['attempts'],
            'active_students': cur['active_students'] - prev['active_students'],
            'avg_score': _delta(cur['avg_score'], prev['avg_score'], has_prev),
            'pass_rate_percent': _delta(
                cur['pass_rate_percent'], prev['pass_rate_percent'], has_prev, ndigits=1
            ),
        },
        'by_exam_type': _by_type(org, start, end, pass_score),
        'top_students': _top_students(org, start, end),
        'certificates': _certificates(org, start, end),
    }


# ── Exports ───────────────────────────────────────────────────────────────────


def _safe(value):
    """Neutralise spreadsheet formula injection (names are user-controlled)."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def _blank(v):
    return '' if v is None else v


def to_csv(report: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Report', _safe(report['organization_name']), report['month']])
    w.writerow([])
    w.writerow(['metric', 'this_month', 'previous_month', 'delta'])
    prev, d = report['previous'], report['deltas']
    for key in ('attempts', 'active_students', 'avg_score', 'pass_rate_percent'):
        w.writerow([key, _blank(report[key]), _blank(prev[key]), _blank(d[key])])
    w.writerow([])
    w.writerow(['exam_type', 'attempts', 'avg_score', 'pass_rate_percent'])
    for r in report['by_exam_type']:
        w.writerow(
            [r['exam_type'], r['attempts'], _blank(r['avg_score']), _blank(r['pass_rate_percent'])]
        )
    w.writerow([])
    w.writerow(['rank', 'student', 'attempts', 'avg_score'])
    for i, s in enumerate(report['top_students'], 1):
        w.writerow([i, _safe(s['name']), s['attempts'], s['avg_score']])
    w.writerow([])
    w.writerow(['certificates', 'count'])
    for k, v in report['certificates'].items():
        w.writerow([k, v])
    return b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8')


def to_html(report: dict) -> str:
    def fmt(v):
        return '&ndash;' if v is None else escape(str(v))

    def table(head, rows):
        th = ''.join(f'<th>{escape(h)}</th>' for h in head)
        body = ''.join('<tr>' + ''.join(f'<td>{fmt(c)}</td>' for c in r) + '</tr>' for r in rows)
        return f'<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'

    prev, d = report['previous'], report['deltas']
    metrics = table(
        ['metric', 'this month', 'previous month', 'delta'],
        [
            [k, report[k], prev[k], d[k]]
            for k in ('attempts', 'active_students', 'avg_score', 'pass_rate_percent')
        ],
    )
    types = table(
        ['exam type', 'attempts', 'avg score', 'pass rate %'],
        [
            [r['exam_type'], r['attempts'], r['avg_score'], r['pass_rate_percent']]
            for r in report['by_exam_type']
        ],
    )
    top = table(
        ['#', 'student', 'attempts', 'avg score'],
        [
            [i, s['name'], s['attempts'], s['avg_score']]
            for i, s in enumerate(report['top_students'], 1)
        ],
    )
    certs = table(['status', 'count'], [[k, v] for k, v in report['certificates'].items()])
    return (
        '<!doctype html><html lang="uz"><head><meta charset="utf-8">'
        f'<title>{escape(report["organization_name"])} {escape(report["month"])}</title>'
        '<style>body{font-family:sans-serif;margin:2rem}table{border-collapse:collapse;margin:1rem 0}'
        'td,th{border:1px solid #999;padding:4px 10px;text-align:left}'
        '@media print{body{margin:0}}</style></head><body>'
        f'<h1>{escape(report["organization_name"])} &mdash; {escape(report["month"])}</h1>'
        f'<h2>Summary</h2>{metrics}<h2>By exam type</h2>{types}'
        f'<h2>Top students</h2>{top}<h2>Certificates</h2>{certs}</body></html>'
    )
