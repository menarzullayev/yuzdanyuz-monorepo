"""
Platform finance (staff only): revenue, expenses, net result.

Definitions (all dates are calendar dates in settings.TIME_ZONE, Asia/Tashkent):

  revenue   = sum of commerce.PaymentIntent.amount_uzs where status == SUCCEEDED and
              provider != STUB (stub is a dev/test provider, never real money).
              The confirmation moment is PaymentIntent.updated_at: the model has no
              dedicated confirmed_at, and the webhook is the last writer of a succeeded
              intent (soft-delete / PII redaction do not touch updated_at).
              Soft-deleted intents stay in revenue (GDPR erasure must not change the
              books; only metadata is redacted).
  expenses  = sum of analytics.Expense.amount_uzs of non-deleted rows, by Expense.date.
  net       = revenue - expenses.
  payer     = 'organization' when the intent has target_organization (an org subscription),
              otherwise 'student' (a wallet top-up by an individual).
  plan      = intent.target_subscription_plan; wallet top-ups have no plan and are
              reported as slug=None.

Periods: this_month = 1st of the current month .. now; last_month = the whole previous
month; this_year = 1 Jan .. now. The series covers the last 12 calendar months, oldest
first, the current month included (month-to-date).
Money is returned as Decimal (rendered as a string by DRF) to avoid float drift.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from apps.commerce.models import PaymentIntent

from .models import Expense

ZERO = Decimal('0.00')
PERIODS = ('this_month', 'last_month', 'this_year')


def _aware(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, n: int) -> date:
    """First-of-month arithmetic (d must be a 1st)."""
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def period_bounds(period: str, today: date) -> tuple[date, date]:
    """Half-open local-date range [start, end) for a named period."""
    if period == 'this_month':
        return month_start(today), today + timedelta(days=1)
    if period == 'last_month':
        this = month_start(today)
        return add_months(this, -1), this
    if period == 'this_year':
        return date(today.year, 1, 1), today + timedelta(days=1)
    raise ValueError(f'unknown period {period!r}')


def revenue_qs():
    return PaymentIntent.objects.filter(status=PaymentIntent.Status.SUCCEEDED).exclude(
        provider=PaymentIntent.Provider.STUB
    )


def _revenue_in(start: date, end: date):
    return revenue_qs().filter(updated_at__gte=_aware(start), updated_at__lt=_aware(end))


def _expenses_in(start: date, end: date):
    return Expense.objects.filter(is_deleted=False, date__gte=start, date__lt=end)


def _sum(qs, field='amount_uzs') -> Decimal:
    return qs.aggregate(t=Sum(field))['t'] or ZERO


def _money(value) -> Decimal:
    return Decimal(value).quantize(Decimal('0.01'))


def period_summary(period: str, today: date) -> dict:
    start, end = period_bounds(period, today)
    revenue = _money(_sum(_revenue_in(start, end)))
    expenses = _money(_sum(_expenses_in(start, end)))
    return {
        'from': start,
        'to': end - timedelta(days=1),
        'revenue': revenue,
        'expenses': expenses,
        'net': revenue - expenses,
    }


def monthly_series(today: date, months: int = 12) -> list[dict]:
    first = add_months(month_start(today), -(months - 1))
    end = today + timedelta(days=1)
    rev = {
        timezone.localtime(r['m']).date(): r['t']
        for r in revenue_qs()
        .filter(updated_at__gte=_aware(first), updated_at__lt=_aware(end))
        .annotate(m=TruncMonth('updated_at'))
        .values('m')
        .annotate(t=Sum('amount_uzs'))
    }
    exp = {
        r['m']: r['t']
        for r in _expenses_in(first, end)
        .annotate(m=TruncMonth('date'))
        .values('m')
        .annotate(t=Sum('amount_uzs'))
    }
    out = []
    for i in range(months):
        m = add_months(first, i)
        r, e = _money(rev.get(m, ZERO)), _money(exp.get(m, ZERO))
        out.append({'month': m.strftime('%Y-%m'), 'revenue': r, 'expenses': e, 'net': r - e})
    return out


def breakdown(period: str, today: date) -> dict:
    start, end = period_bounds(period, today)
    qs = _revenue_in(start, end)

    def rows(values, label):
        return [
            {**label(r), 'amount': _money(r['t']), 'count': r['n']}
            for r in qs.values(*values).annotate(t=Sum('amount_uzs'), n=Count('id')).order_by('-t')
        ]

    by_method = rows(('provider',), lambda r: {'provider': r['provider']})
    by_plan = rows(
        ('target_subscription_plan__slug', 'target_subscription_plan__name'),
        lambda r: {
            'plan_slug': r['target_subscription_plan__slug'],
            'plan_name': r['target_subscription_plan__name'],
        },
    )
    payer = {'student': [ZERO, 0], 'organization': [ZERO, 0]}
    for r in qs.values('target_organization_id').annotate(t=Sum('amount_uzs'), n=Count('id')):
        key = 'student' if r['target_organization_id'] is None else 'organization'
        payer[key][0] += r['t']
        payer[key][1] += r['n']
    by_payer = [
        {'payer_type': k, 'amount': _money(v[0]), 'count': v[1]}
        for k, v in sorted(payer.items(), key=lambda kv: -kv[1][0])
    ]
    exp = [
        {'type': r['type'], 'amount': _money(r['t']), 'count': r['n']}
        for r in _expenses_in(start, end)
        .values('type')
        .annotate(t=Sum('amount_uzs'), n=Count('id'))
        .order_by('-t')
    ]
    return {
        'period': period,
        'by_method': by_method,
        'by_payer_type': by_payer,
        'by_plan': by_plan,
        'expenses_by_type': exp,
    }


def finance_summary(period: str = 'this_month', now: datetime | None = None) -> dict:
    today = timezone.localtime(now or timezone.now()).date()
    return {
        'currency': 'UZS',
        'periods': {p: period_summary(p, today) for p in PERIODS},
        'series': monthly_series(today),
        'breakdown': breakdown(period, today),
    }
