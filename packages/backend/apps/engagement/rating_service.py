"""School / learning-centre rating.

Ranks organizations by the average score of their students' SUBMITTED exam attempts in a
period. An org qualifies only with >= RATING_MIN_STUDENTS distinct students AND
>= RATING_MIN_ATTEMPTS attempts. The whole stats table is cached per period; changing an
org's profile invalidates it.

The aggregation is cross-tenant by nature, so it runs inside unscoped_context() (same
convention as the public certificate verification).
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db.models import Avg, Count
from django.utils import timezone

from apps.exams.models import ExamAttempt
from apps.organizations.models import Organization, OrgStatus, OrgType
from core.tenant import unscoped_context

from .rating_models import OrgRatingProfile

RATING_MIN_STUDENTS = 5
RATING_MIN_ATTEMPTS = 10
RATING_PERIODS = {'week': 7, 'month': 30, 'quarter': 90, 'year': 365}
RATING_DEFAULT_PERIOD = 'month'
RATING_CACHE_TTL = 600
_CACHE_KEY = 'yz:engagement:org-rating:{period}'


def parse_period(value) -> str:
    value = value or RATING_DEFAULT_PERIOD
    if value not in RATING_PERIODS:
        raise ValueError(f'period must be one of {sorted(RATING_PERIODS)}')
    return value


def invalidate_cache() -> None:
    cache.delete_many([_CACHE_KEY.format(period=p) for p in RATING_PERIODS])


def _compute_stats(period: str) -> list[dict]:
    since = timezone.now() - timedelta(days=RATING_PERIODS[period])
    with unscoped_context():
        rows = list(
            ExamAttempt.objects.filter(
                status=ExamAttempt.Status.SUBMITTED,
                is_deleted=False,
                score__isnull=False,
                user__isnull=False,
                submitted_at__gte=since,
                organization__org_type__in=(OrgType.TENANT, OrgType.BRANCH),
                organization__status__in=(OrgStatus.ACTIVE, OrgStatus.TRIAL),
            )
            .order_by()
            .values('organization_id')
            .annotate(
                avg=Avg('score'), students=Count('user_id', distinct=True), attempts=Count('id')
            )
        )
        ids = [r['organization_id'] for r in rows]
        orgs = {o.id: o for o in Organization.objects.filter(pk__in=ids)}
        profiles = {
            p.organization_id: p
            for p in OrgRatingProfile.global_objects.filter(organization_id__in=ids)
        }

    out = []
    for r in rows:
        org = orgs[r['organization_id']]
        prof = profiles.get(org.id)
        out.append(
            {
                'organization_id': str(org.id),
                'name': org.name,
                'slug': org.slug,
                'kind': prof.kind if prof else OrgRatingProfile.DEFAULT_KIND.value,
                'visibility': prof.visibility
                if prof
                else OrgRatingProfile.DEFAULT_VISIBILITY.value,
                'avg_score': str(Decimal(r['avg']).quantize(Decimal('0.01'), ROUND_HALF_UP)),
                'students': r['students'],
                'attempts': r['attempts'],
                'qualified': r['students'] >= RATING_MIN_STUDENTS
                and r['attempts'] >= RATING_MIN_ATTEMPTS,
            }
        )
    # best average first; more attempts then name break ties deterministically
    out.sort(key=lambda e: (-Decimal(e['avg_score']), -e['attempts'], e['name']))
    return out


def get_stats(period: str) -> list[dict]:
    key = _CACHE_KEY.format(period=period)
    stats = cache.get(key)
    if stats is None:
        stats = _compute_stats(period)
        cache.set(key, stats, RATING_CACHE_TTL)
    return stats


def public_ranking(period: str, kind: str | None = None) -> list[dict]:
    """Qualified + public orgs, ranked 1..n within the (optionally kind-filtered) list."""
    rows = [
        e
        for e in get_stats(period)
        if e['qualified']
        and e['visibility'] == OrgRatingProfile.Visibility.PUBLIC
        and (kind is None or e['kind'] == kind)
    ]
    return [
        {
            'rank': i + 1,
            'name': e['name'],
            'slug': e['slug'],
            'kind': e['kind'],
            'avg_score': e['avg_score'],
            'students': e['students'],
            'attempts': e['attempts'],
        }
        for i, e in enumerate(rows)
    ]


def own_position(org, period: str) -> dict:
    """The org's position among PUBLIC orgs of its kind, as if it were listed.

    Hidden orgs never take a slot in the public ranks; their position is the one they
    would have (1 + public orgs that beat them).
    """
    stats = get_stats(period)
    mine = next((e for e in stats if e['organization_id'] == str(org.id)), None)
    prof = OrgRatingProfile.global_objects.filter(organization=org).first()
    kind = prof.kind if prof else OrgRatingProfile.DEFAULT_KIND.value
    visibility = prof.visibility if prof else OrgRatingProfile.DEFAULT_VISIBILITY.value
    result = {
        'organization_id': str(org.id),
        'kind': kind,
        'visibility': visibility,
        'period': period,
        'min_students': RATING_MIN_STUDENTS,
        'min_attempts': RATING_MIN_ATTEMPTS,
        'students': mine['students'] if mine else 0,
        'attempts': mine['attempts'] if mine else 0,
        'avg_score': mine['avg_score'] if mine else None,
        'qualified': bool(mine and mine['qualified']),
        'rank': None,
        'listed_publicly': False,
    }
    if not result['qualified']:
        return result
    mine_key = (-Decimal(mine['avg_score']), -mine['attempts'], mine['name'])
    ahead = sum(
        1
        for e in stats
        if e['qualified']
        and e['organization_id'] != mine['organization_id']
        and e['kind'] == kind
        and e['visibility'] == OrgRatingProfile.Visibility.PUBLIC
        and (-Decimal(e['avg_score']), -e['attempts'], e['name']) < mine_key
    )
    result['rank'] = ahead + 1
    result['listed_publicly'] = visibility == OrgRatingProfile.Visibility.PUBLIC
    return result
