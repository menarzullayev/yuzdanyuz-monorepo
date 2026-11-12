"""
Funnel and retention (platform staff: whole platform; org admins: their own organization).

All dates are calendar dates in settings.TIME_ZONE (Asia/Tashkent); "today" is the current,
still incomplete local day.

Population ("students"):
  platform scope  every user with is_staff=False and is_superuser=False;
                  signup date = User.date_joined.
  organization    users with an ACTIVE membership in the org whose role is 'student';
                  signup date = Membership.joined_at (falls back to created_at).

Activity = starting an exam attempt (ExamAttempt.started_at, any status, not deleted) or a
practice session (PracticeSession.started_at). In organization scope only activity inside
that organization counts. A user is "active on day X" if they have at least one activity
on local date X.

Metrics:
  new_users              signups with a date in the last `days` days (today included).
  dau                    per local day of that window: distinct active users;
                         `dau_today` is the (partial) current day.
  retention D1           user signed up on local date S is retained if active on S+1.
  retention D7           retained if active on any day S+7 .. S+13.
  retention D30          retained if active on any day S+30 .. S+59.
                         Each rate counts only users whose window has FULLY passed, i.e.
                         the last day of the window is strictly before today
                         (D1: S <= today-2, D7: S <= today-14, D30: S <= today-60);
                         rate = retained / eligible (null when nobody is eligible yet).
                         Cohorts are users who signed up in the last `cohort_days` days.
                         Day S itself never counts as activity for any window.
  weekly_active_students distinct active users per ISO week (Monday start) for the last 8
                         weeks, the current week included (flagged partial).
  attempts_per_active_student
                         (exam attempts + practice sessions started in the last 30 days,
                         today included) / (distinct users active in those 30 days);
                         null when nobody was active.
  demo_conversion (platform scope only)
                         demo orgs = tenant organizations with status 'trial' or a
                         trial_ends_at; converted = a demo org with at least one confirmed
                         (SUCCEEDED, non-stub) PaymentIntent whose target_organization is
                         it. rate = converted / demo orgs. Approximation: the platform keeps
                         no demo-start history, so an org that never had trial fields set
                         is not in the cohort.

Results are cached for 15 minutes per (scope, days, cohort_days).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Q
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.commerce.models import PaymentIntent
from apps.exams.models import ExamAttempt, PracticeSession
from apps.organizations.models import (
    Membership,
    MembershipStatus,
    Organization,
    OrgStatus,
    OrgType,
)

CACHE_TTL = 15 * 60
RETENTION_WINDOWS = {'D1': (1, 1), 'D7': (7, 13), 'D30': (30, 59)}
ACTIVE_WINDOW_DAYS = 30
WEEKS = 8


@dataclass(frozen=True)
class Scope:
    org: Organization | None = None  # None = whole platform

    @property
    def key(self) -> str:
        return str(self.org.id) if self.org else 'platform'


def _aware(d: date) -> datetime:
    return timezone.make_aware(datetime.combine(d, time.min))


def _student_memberships(org):
    return Membership.objects.filter(
        organization=org, role__name='student', status=MembershipStatus.ACTIVE
    )


def _activity_qs(model, scope: Scope, since: date):
    if scope.org is None:
        flt = Q(user__is_staff=False, user__is_superuser=False)
    else:
        flt = Q(
            organization=scope.org, user_id__in=_student_memberships(scope.org).values('user_id')
        )
    qs = model.global_objects.filter(flt, user__isnull=False, started_at__gte=_aware(since))
    if model is ExamAttempt:
        qs = qs.filter(is_deleted=False)
    return qs


def activity_pairs(scope: Scope, since: date) -> set[tuple]:
    """Distinct (user_id, local_date) pairs with activity on or after `since`."""
    pairs: set[tuple] = set()
    for model in (ExamAttempt, PracticeSession):
        qs = _activity_qs(model, scope, since)
        pairs.update(qs.annotate(d=TruncDate('started_at')).values_list('user_id', 'd').distinct())
    return pairs


def signups(scope: Scope, since: date) -> dict:
    """user_id -> local signup date, for signups on or after `since`."""
    if scope.org is None:
        rows = (
            get_user_model()
            .objects.filter(is_staff=False, is_superuser=False, date_joined__gte=_aware(since))
            .values_list('id', 'date_joined')
        )
    else:
        rows = (
            _student_memberships(scope.org)
            .annotate(signup=Coalesce('joined_at', 'created_at'))
            .filter(signup__gte=_aware(since))
            .values_list('user_id', 'signup')
        )
    return {uid: timezone.localtime(dt).date() for uid, dt in rows}


def retention(cohort: dict, by_user: dict, today: date) -> dict:
    out = {}
    for name, (lo, hi) in RETENTION_WINDOWS.items():
        eligible = retained = 0
        for uid, s in cohort.items():
            if s + timedelta(days=hi) >= today:
                continue  # window not fully passed yet
            eligible += 1
            acts = by_user.get(uid, ())
            if any(s + timedelta(days=lo) <= a <= s + timedelta(days=hi) for a in acts):
                retained += 1
        out[name] = {
            'window_days': [lo, hi],
            'eligible': eligible,
            'retained': retained,
            'rate_percent': round(100 * retained / eligible, 1) if eligible else None,
        }
    return out


def demo_conversion(today: date) -> dict:
    demo = Organization.objects.filter(org_type=OrgType.TENANT).filter(
        Q(status=OrgStatus.TRIAL) | Q(trial_ends_at__isnull=False)
    )
    total = demo.count()
    paid = (
        PaymentIntent.objects.filter(
            status=PaymentIntent.Status.SUCCEEDED, target_organization__in=demo
        )
        .exclude(provider=PaymentIntent.Provider.STUB)
        .values('target_organization_id')
        .distinct()
    )
    recent = paid.filter(updated_at__gte=_aware(today - timedelta(days=ACTIVE_WINDOW_DAYS - 1)))
    converted = paid.count()
    return {
        'demo_orgs': total,
        'converted': converted,
        'converted_last_30_days': recent.count(),
        'rate_percent': round(100 * converted / total, 1) if total else None,
    }


def compute_funnel(
    scope: Scope, *, days: int = 30, cohort_days: int = 120, now: datetime | None = None
) -> dict:
    today = timezone.localtime(now or timezone.now()).date()
    window_start = today - timedelta(days=days - 1)
    cohort_start = today - timedelta(days=cohort_days)
    week_start = today - timedelta(days=today.weekday())
    first_week = week_start - timedelta(weeks=WEEKS - 1)
    active30_start = today - timedelta(days=ACTIVE_WINDOW_DAYS - 1)
    since = min(window_start, cohort_start, first_week, active30_start)

    by_user: dict = defaultdict(set)
    by_day: dict = defaultdict(set)
    for uid, d in activity_pairs(scope, since):
        by_user[uid].add(d)
        by_day[d].add(uid)

    new_users = signups(scope, min(window_start, cohort_start))
    cohort = {u: s for u, s in new_users.items() if s >= cohort_start}
    new_in_window = sum(1 for s in new_users.values() if s >= window_start)

    dau = []
    for i in range(days):
        d = window_start + timedelta(days=i)
        dau.append({'date': d.isoformat(), 'users': len(by_day.get(d, ()))})

    weekly = []
    for i in range(WEEKS):
        ws = first_week + timedelta(weeks=i)
        users: set = set()
        for k in range(7):
            users |= by_day.get(ws + timedelta(days=k), set())
        weekly.append(
            {
                'week_start': ws.isoformat(),
                'active_students': len(users),
                'partial': ws == week_start,
            }
        )

    active30: set = set()
    for k in range(ACTIVE_WINDOW_DAYS):
        active30 |= by_day.get(active30_start + timedelta(days=k), set())
    exam_n = _activity_qs(ExamAttempt, scope, active30_start).count()
    session_n = _activity_qs(PracticeSession, scope, active30_start).count()

    return {
        'scope': 'platform' if scope.org is None else 'organization',
        'organization_id': str(scope.org.id) if scope.org else None,
        'as_of': today.isoformat(),
        'days': days,
        'cohort_days': cohort_days,
        'new_users': new_in_window,
        'dau': dau,
        'dau_today': len(by_day.get(today, ())),
        'retention': retention(cohort, by_user, today),
        'weekly_active_students': weekly,
        'attempts_per_active_student': {
            'window_days': ACTIVE_WINDOW_DAYS,
            'active_students': len(active30),
            'exam_attempts': exam_n,
            'practice_sessions': session_n,
            'value': round((exam_n + session_n) / len(active30), 2) if active30 else None,
        },
        'demo_conversion': demo_conversion(today) if scope.org is None else None,
    }


def get_funnel(scope: Scope, *, days: int = 30, cohort_days: int = 120) -> dict:
    key = f'yz:analytics:funnel:{scope.key}:{days}:{cohort_days}'
    hit = cache.get(key)
    if hit is not None:
        return hit
    data = compute_funnel(scope, days=days, cohort_days=cohort_days)
    cache.set(key, data, CACHE_TTL)
    return data
