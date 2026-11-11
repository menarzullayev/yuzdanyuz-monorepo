"""
Business analytics: finance (revenue/expenses/net), funnel + retention, monthly org report.

All numbers are exact, hand-computed from fixed data. "Today" is fixed to 2026-11-12
(Tashkent) through the `now` parameter of the services.
"""

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics import finance, funnel, org_report
from apps.analytics.models import Expense
from apps.commerce.models import PaymentIntent, SubscriptionPlan
from apps.exams.models import ExamAttempt, MockExam, PracticeSession
from apps.exams.sitting_models import (
    NATIONAL_BANDS,
    Certificate,
    ExamSitting,
    GradeScale,
    SittingResult,
)
from apps.organizations.models import (
    Membership,
    MembershipStatus,
    Organization,
    OrgRole,
    OrgStatus,
)
from core.tenant import tenant_context

User = get_user_model()
TZ = ZoneInfo('Asia/Tashkent')
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
T = date(2026, 11, 12)


def at(d, hour=12, minute=0):
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def ago(n):
    return T - timedelta(days=n)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _user(name, **kw):
    return User.objects.create_user(username=name, email=f'{name}@x.uz', password='pw12345!', **kw)


def _member(user, org, role, joined=None):
    r = OrgRole.objects.get(organization=org, name=role)
    m = Membership.objects.create(
        user=user, organization=org, role=r, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    if joined is not None:
        Membership.objects.filter(pk=m.pk).update(joined_at=joined)
    return m


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _exam(org, creator, title='E', public=False):
    with tenant_context(org):
        return MockExam.objects.create(
            organization=org,
            title=title,
            duration_minutes=60,
            scheduled_at=NOW - timedelta(days=100),
            closes_at=NOW + timedelta(days=100),
            status=MockExam.Status.PUBLISHED,
            is_public=public,
            created_by=creator,
        )


def _attempt(org, user, exam, started, *, score=None, status=None, submitted=None, deleted=False):
    with tenant_context(org):
        a = ExamAttempt.objects.create(organization=org, exam=exam, user=user)
    ExamAttempt.global_objects.filter(pk=a.pk).update(
        started_at=started,
        submitted_at=submitted,
        score=score,
        status=status or ExamAttempt.Status.IN_PROGRESS,
        is_deleted=deleted,
    )
    return a


def _session(org, user, started, correct=0, total=0):
    with tenant_context(org):
        s = PracticeSession.objects.create(
            organization=org, user=user, blueprint=[], correct_count=correct, total_count=total
        )
    PracticeSession.global_objects.filter(pk=s.pk).update(started_at=started)
    return s


# ═══════════════════════════════════════════════════════════════════════════
# Finance
# ═══════════════════════════════════════════════════════════════════════════


def _payment(
    amount,
    when,
    *,
    provider='payme',
    status='succeeded',
    org=None,
    plan=None,
    user=None,
    deleted=False,
):
    user = (
        user
        or User.objects.get_or_create(username='default_payer', defaults={'email': 'dp@x.uz'})[0]
    )
    p = PaymentIntent.objects.create(
        user=user,
        provider=provider,
        status=status,
        amount_uzs=Decimal(amount),
        coins_to_credit=0,
        target_organization=org,
        target_subscription_plan=plan,
        provider_tx_id=uuid.uuid4().hex,
    )
    PaymentIntent.objects.filter(pk=p.pk).update(updated_at=when, is_deleted=deleted)
    return p


def _expense(amount, d, type_='server', deleted=False):
    return Expense.objects.create(
        amount_uzs=Decimal(amount), date=d, type=type_, is_deleted=deleted
    )


@pytest.fixture
def money(db, org, org2):
    payer = _user('payer')
    pro = SubscriptionPlan.objects.create(
        name='Pro', slug='pro', billing_period='monthly', pricing_model='flat', price_uzs=250000
    )
    basic = SubscriptionPlan.objects.create(
        name='Basic', slug='basic', billing_period='monthly', pricing_model='flat', price_uzs=70000
    )
    kw = {'user': payer}
    _payment(100000, at(date(2026, 11, 3)), **kw)
    _payment(250000, at(date(2026, 11, 10)), provider='click', org=org, plan=pro, **kw)
    _payment(50000, at(date(2026, 10, 31), 23, 30), **kw)  # last day of last month
    _payment(
        400000, at(date(2026, 10, 1), 0, 0), provider='click', org=org, plan=pro, **kw
    )  # first instant
    _payment(70000, at(date(2026, 9, 30), 23, 59), org=org2, plan=basic, **kw)  # month before last
    _payment(999999, at(date(2026, 11, 5)), provider='stub', **kw)  # stub: excluded
    _payment(123456, at(date(2026, 11, 6)), status='failed', **kw)  # not confirmed: excluded
    _payment(30000, at(date(2026, 1, 15)), **kw)
    _payment(20000, at(date(2026, 11, 4)), deleted=True, **kw)  # soft-deleted stays in revenue
    _payment(10, at(date(2026, 11, 13), 0, 0), provider='click', **kw)  # after "now": excluded
    _expense(30000, date(2026, 11, 2), 'server')
    _expense(20000, date(2026, 11, 9), 'ai')
    _expense(100000, date(2026, 10, 15), 'salaries')
    _expense(5000, date(2026, 11, 20), 'advertising')  # later than today: excluded from MTD
    _expense(777, date(2026, 11, 3), 'other', deleted=True)  # soft-deleted: excluded
    _expense(1000, date(2026, 1, 5), 'content')


@pytest.mark.integration
class TestFinanceNumbers:
    def test_periods(self, money):
        p = finance.finance_summary(now=NOW)['periods']
        assert (
            p['this_month']['revenue'],
            p['this_month']['expenses'],
            p['this_month']['net'],
        ) == (
            Decimal('370000.00'),
            Decimal('50000.00'),
            Decimal('320000.00'),
        )
        assert (
            p['last_month']['revenue'],
            p['last_month']['expenses'],
            p['last_month']['net'],
        ) == (
            Decimal('450000.00'),
            Decimal('100000.00'),
            Decimal('350000.00'),
        )
        assert (p['this_year']['revenue'], p['this_year']['expenses'], p['this_year']['net']) == (
            Decimal('920000.00'),
            Decimal('151000.00'),
            Decimal('769000.00'),
        )
        assert (p['last_month']['from'], p['last_month']['to']) == (
            date(2026, 10, 1),
            date(2026, 10, 31),
        )

    def test_series_12_months(self, money):
        s = finance.finance_summary(now=NOW)['series']
        assert len(s) == 12
        assert s[0]['month'] == '2025-12' and s[-1]['month'] == '2026-11'
        by = {r['month']: r for r in s}
        assert by['2026-01']['revenue'] == Decimal('30000.00') and by['2026-01'][
            'expenses'
        ] == Decimal('1000.00')
        assert by['2026-09']['revenue'] == Decimal('70000.00')
        assert by['2026-10']['net'] == Decimal('350000.00')
        assert by['2026-11']['net'] == Decimal('320000.00')
        assert by['2026-05']['revenue'] == 0 and by['2026-05']['net'] == 0

    def test_breakdowns(self, money):
        b = finance.finance_summary('this_month', now=NOW)['breakdown']
        assert [(r['provider'], r['amount'], r['count']) for r in b['by_method']] == [
            ('click', Decimal('250000.00'), 1),
            ('payme', Decimal('120000.00'), 2),
        ]
        assert [(r['payer_type'], r['amount'], r['count']) for r in b['by_payer_type']] == [
            ('organization', Decimal('250000.00'), 1),
            ('student', Decimal('120000.00'), 2),
        ]
        assert [(r['plan_slug'], r['amount']) for r in b['by_plan']] == [
            ('pro', Decimal('250000.00')),
            (None, Decimal('120000.00')),
        ]
        assert [(r['type'], r['amount']) for r in b['expenses_by_type']] == [
            ('server', Decimal('30000.00')),
            ('ai', Decimal('20000.00')),
        ]

    def test_breakdown_other_period(self, money):
        b = finance.finance_summary('last_month', now=NOW)['breakdown']
        assert sum(r['amount'] for r in b['by_method']) == Decimal('450000.00')
        assert {r['payer_type']: r['amount'] for r in b['by_payer_type']} == {
            'organization': Decimal('400000.00'),
            'student': Decimal('50000.00'),
        }

    def test_empty_database(self, db):
        s = finance.finance_summary(now=NOW)
        assert s['periods']['this_year']['net'] == 0
        assert s['breakdown']['by_method'] == []

    def test_negative_stub_and_failed_never_count(self, db):
        _payment(5000, at(ago(1)), provider='stub')
        _payment(5000, at(ago(1)), status='processing')
        _payment(5000, at(ago(1)), status='cancelled')
        assert finance.finance_summary(now=NOW)['periods']['this_month']['revenue'] == 0


@pytest.mark.integration
class TestExpenseModel:
    def test_db_rejects_non_positive_amount(self, db):
        for bad in (0, -5):
            with pytest.raises(IntegrityError), transaction.atomic():
                Expense.objects.create(amount_uzs=Decimal(bad), date=T, type='other')


@pytest.mark.integration
class TestFinanceAPI:
    def _staff(self):
        return _client(_user('boss', is_staff=True))

    def test_summary_staff_ok(self, money):
        resp = self._staff().get(reverse('analytics:finance'))
        assert resp.status_code == 200
        body = resp.json()
        assert body['currency'] == 'UZS' and len(body['series']) == 12
        assert body['breakdown']['period'] == 'this_month'

    def test_invalid_period(self, db):
        assert (
            self._staff().get(reverse('analytics:finance'), {'period': 'forever'}).status_code
            == 400
        )

    def test_student_forbidden(self, db, org):
        u = _user('stud')
        _member(u, org, 'student')
        c = _client(u)
        assert c.get(reverse('analytics:finance')).status_code == 403
        assert c.get(reverse('analytics:expenses')).status_code == 403
        assert (
            c.post(
                reverse('analytics:expenses'),
                {'date': str(T), 'amount_uzs': '5', 'type': 'other'},
                content_type='application/json',
            ).status_code
            == 403
        )

    def test_org_owner_forbidden(self, db, org):
        u = _user('own')
        _member(u, org, 'owner')
        assert _client(u).get(reverse('analytics:finance')).status_code == 403

    def test_anonymous_rejected(self, db):
        assert Client().get(reverse('analytics:finance')).status_code in (401, 403)

    def _post(self, c, **data):
        payload = {
            'date': str(timezone.localdate()),
            'amount_uzs': '1500.50',
            'type': 'server',
            'note': 'x',
        }
        payload.update(data)
        return c.post(
            reverse('analytics:expenses'), json.dumps(payload), content_type='application/json'
        )

    def test_create_sets_created_by(self, db):
        staff = _user('boss2', is_staff=True)
        resp = self._post(_client(staff))
        assert resp.status_code == 201, resp.content
        assert Expense.objects.get().created_by_id == staff.id

    def test_created_by_cannot_be_forged(self, db):
        staff, other = _user('boss3', is_staff=True), _user('other')
        resp = self._post(_client(staff), created_by=str(other.id))
        assert resp.status_code == 201
        assert Expense.objects.get().created_by_id == staff.id

    @pytest.mark.parametrize(
        'override',
        [
            {'amount_uzs': '0'},
            {'amount_uzs': '-10'},
            {'amount_uzs': 'abc'},
            {'amount_uzs': '1' + '0' * 14},  # exceeds max_digits
            {'type': 'bogus'},
            {'date': 'not-a-date'},
            {'date': '2019-12-31'},
            {'note': 'n' * 501},
        ],
    )
    def test_validation_negatives(self, db, override):
        assert self._post(self._staff(), **override).status_code == 400
        assert Expense.objects.count() == 0

    def test_future_date_boundary(self, db):
        c = self._staff()
        today = timezone.localdate()
        assert self._post(c, date=str(today + timedelta(days=31))).status_code == 201
        assert self._post(c, date=str(today + timedelta(days=32))).status_code == 400

    def test_list_filters_and_soft_delete(self, db):
        c = self._staff()
        a = _expense(100, date(2026, 10, 1), 'ai')
        _expense(200, date(2026, 11, 1), 'server')
        res = c.get(reverse('analytics:expenses'), {'type': 'ai'}).json()['results']
        assert [r['id'] for r in res] == [str(a.id)]
        res = c.get(reverse('analytics:expenses'), {'date_from': '2026-10-15'}).json()['results']
        assert [r['amount_uzs'] for r in res] == ['200.00']
        assert c.get(reverse('analytics:expenses'), {'type': 'zzz'}).status_code == 400
        assert c.get(reverse('analytics:expenses'), {'date_to': 'x'}).status_code == 400

        assert c.delete(reverse('analytics:expense-detail', args=[a.id])).status_code == 204
        assert Expense.objects.get(pk=a.pk).is_deleted is True  # soft
        assert c.get(reverse('analytics:expense-detail', args=[a.id])).status_code == 404
        assert len(c.get(reverse('analytics:expenses')).json()['results']) == 1

    def test_patch_validates(self, db):
        c = self._staff()
        e = _expense(100, T)
        url = reverse('analytics:expense-detail', args=[e.id])
        ok = c.patch(url, json.dumps({'amount_uzs': '250'}), content_type='application/json')
        assert ok.status_code == 200 and ok.json()['amount_uzs'] == '250.00'
        bad = c.patch(url, json.dumps({'amount_uzs': '0'}), content_type='application/json')
        assert bad.status_code == 400
        assert Expense.objects.get(pk=e.pk).amount_uzs == Decimal('250.00')

    def test_student_cannot_delete_or_patch(self, db, org):
        e = _expense(100, T)
        u = _user('stud2')
        _member(u, org, 'student')
        c = _client(u)
        url = reverse('analytics:expense-detail', args=[e.id])
        assert c.delete(url).status_code == 403
        assert c.patch(url, '{}', content_type='application/json').status_code == 403
        assert Expense.objects.get(pk=e.pk).is_deleted is False


# ═══════════════════════════════════════════════════════════════════════════
# Funnel and retention
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def cohorts(db, org, org2):
    """Users A..N per the module's hand computation; signup/activity at local noon."""
    admin = _user('creator', is_staff=True)
    exam = _exam(org, admin)
    u = {}

    def make(name, signup_ago, *, attempts=(), sessions=()):
        user = _user(name, date_joined=at(ago(signup_ago)))
        User.objects.filter(pk=user.pk).update(date_joined=at(ago(signup_ago)))
        u[name] = user
        for d in attempts:
            _attempt(
                org,
                user,
                exam if not _has(org, user, exam) else _exam(org, admin, f'e{name}{d}'),
                at(ago(d)),
            )
        for d in sessions:
            _session(org, user, at(ago(d)))
        return user

    def _has(o, user, e):
        return ExamAttempt.global_objects.filter(user=user, exam=e).exists()

    make('A', 20, attempts=[13])  # S+7  -> D7 yes
    make('B', 20, attempts=[7])  # S+13 -> D7 yes
    make('C', 20, sessions=[6])  # S+14 -> D7 no
    make('D', 20, sessions=[14])  # S+6  -> D7 no
    make('E', 13, attempts=[6])  # D7 window not passed -> not eligible
    make('F', 14, attempts=[1])  # S+13 = yesterday -> eligible, retained
    make('G', 70, attempts=[40])  # S+30 -> D30 yes
    make('H', 70, attempts=[10])  # S+60 -> D30 no
    make('I', 60, attempts=[1])  # S+59 = yesterday -> D30 eligible, retained
    make('J', 59)  # D30 not eligible, no activity
    make('K', 5, attempts=[4])  # D1 yes
    make('L', 5, attempts=[5])  # same-day only -> D1 no
    make('M', 1, attempts=[0])  # D1 window = today -> not eligible
    make('N', 2, attempts=[1], sessions=[1])  # D1 yes (two activities same day)
    # excluded from the platform population: staff
    s = _user('staffer', is_staff=True, date_joined=at(ago(3)))
    _attempt(org, s, exam, at(ago(2)))
    return u


@pytest.mark.integration
class TestFunnelPlatform:
    def test_exact_numbers(self, cohorts):
        r = funnel.compute_funnel(funnel.Scope(None), now=NOW)
        ret = r['retention']
        assert (ret['D1']['eligible'], ret['D1']['retained'], ret['D1']['rate_percent']) == (
            13,
            2,
            15.4,
        )
        assert (ret['D7']['eligible'], ret['D7']['retained'], ret['D7']['rate_percent']) == (
            9,
            3,
            33.3,
        )
        assert (ret['D30']['eligible'], ret['D30']['retained'], ret['D30']['rate_percent']) == (
            3,
            2,
            66.7,
        )
        assert r['new_users'] == 10  # A-F, K-N inside the 30-day window; staff excluded
        assert r['dau_today'] == 1  # M
        assert len(r['dau']) == 30 and r['dau'][-1] == {'date': '2026-11-12', 'users': 1}
        assert r['dau'][-2] == {'date': '2026-11-11', 'users': 3}  # F, I, N
        a = r['attempts_per_active_student']
        assert (a['active_students'], a['exam_attempts'], a['practice_sessions'], a['value']) == (
            12,
            10,
            3,
            1.08,
        )

    def test_weekly_active_students(self, cohorts):
        w = funnel.compute_funnel(funnel.Scope(None), now=NOW)['weekly_active_students']
        assert [x['week_start'] for x in w][0] == '2026-09-21' and w[-1][
            'week_start'
        ] == '2026-11-09'
        assert [x['active_students'] for x in w] == [0, 1, 0, 0, 0, 2, 6, 4]
        assert [x['partial'] for x in w] == [False] * 7 + [True]

    def test_no_data(self, db):
        r = funnel.compute_funnel(funnel.Scope(None), now=NOW)
        assert r['retention']['D1']['rate_percent'] is None
        assert r['attempts_per_active_student']['value'] is None
        assert r['new_users'] == 0 and sum(x['users'] for x in r['dau']) == 0

    @pytest.mark.parametrize(
        ('metric', 'signup_ago', 'active_offset', 'eligible', 'retained'),
        [
            # D1: window is exactly S+1
            ('D1', 2, 1, 1, 1),
            ('D1', 2, 0, 1, 0),  # same-day activity never counts
            ('D1', 2, 2, 1, 0),  # S+2 is outside the D1 window
            ('D1', 1, 0, 0, 0),  # window (today) not fully passed
            # D7: window S+7..S+13
            ('D7', 14, 6, 1, 0),  # a user active only on day 6 is NOT retained
            ('D7', 14, 7, 1, 1),
            ('D7', 14, 13, 1, 1),
            ('D7', 20, 14, 1, 0),
            ('D7', 13, 7, 0, 0),  # signed up 13 days ago: window ends today -> not eligible
            ('D7', 6, 6, 0, 0),  # signed up 6 days ago: never counted in D7
            # D30: window S+30..S+59
            ('D30', 60, 29, 1, 0),
            ('D30', 60, 30, 1, 1),
            ('D30', 60, 59, 1, 1),
            ('D30', 70, 60, 1, 0),
            ('D30', 59, 30, 0, 0),
        ],
    )
    def test_window_boundaries(
        self, db, org, metric, signup_ago, active_offset, eligible, retained
    ):
        user = _user('solo', date_joined=at(ago(signup_ago)))
        User.objects.filter(pk=user.pk).update(date_joined=at(ago(signup_ago)))
        act_date = ago(signup_ago) + timedelta(days=active_offset)
        if act_date <= T:
            _session(org, user, at(act_date))
        r = funnel.compute_funnel(funnel.Scope(None), now=NOW)['retention'][metric]
        assert (r['eligible'], r['retained']) == (eligible, retained)

    def test_local_day_boundary(self, db, org):
        """23:30 UTC on Nov 11 is 04:30 on Nov 12 in Tashkent -> counts as today."""
        user = _user('night')
        _session(org, user, datetime(2026, 11, 11, 23, 30, tzinfo=ZoneInfo('UTC')))
        r = funnel.compute_funnel(funnel.Scope(None), now=NOW)
        assert r['dau_today'] == 1
        assert r['dau'][-2]['users'] == 0

    def test_demo_conversion(self, db, org, org2):
        payer = _user('p')
        o_trial = Organization.objects.create(name='t1', slug='t1', status=OrgStatus.TRIAL)
        o_end = Organization.objects.create(
            name='t2', slug='t2', status=OrgStatus.ACTIVE, trial_ends_at=NOW + timedelta(days=3)
        )
        o_stub = Organization.objects.create(name='t3', slug='t3', status=OrgStatus.TRIAL)
        o_plain = Organization.objects.create(name='t4', slug='t4', status=OrgStatus.ACTIVE)
        _payment(100, at(ago(2)), org=o_trial, user=payer)
        _payment(100, at(ago(40)), org=o_end, user=payer, provider='click')
        _payment(100, at(ago(41)), org=o_end, user=payer, provider='click')  # 2nd payment, same org
        _payment(100, at(ago(2)), org=o_stub, user=payer, provider='stub')  # stub: not converted
        _payment(100, at(ago(2)), org=o_plain, user=payer)  # never on demo: not in cohort
        d = funnel.compute_funnel(funnel.Scope(None), now=NOW)['demo_conversion']
        assert d == {
            'demo_orgs': 3,
            'converted': 2,
            'converted_last_30_days': 1,
            'rate_percent': 66.7,
        }


@pytest.mark.integration
class TestFunnelOrgScope:
    def test_org_scope_counts_only_own_students(self, db, org, org2):
        admin = _user('adm')
        exam1, exam2 = _exam(org, admin), _exam(org2, admin)
        mine = _user('mine')
        _member(mine, org, 'student', joined=at(ago(10)))
        Membership.objects.filter(user=mine).update(created_at=at(ago(10)))
        _session(org, mine, at(ago(9)))  # D1 yes
        teacher = _user('teach')
        _member(teacher, org, 'teacher', joined=at(ago(10)))
        _session(org, teacher, at(ago(9)))  # not a student
        foreign = _user('foreign')
        _member(foreign, org2, 'student', joined=at(ago(10)))
        _attempt(org2, foreign, exam2, at(ago(9)))  # other org
        # my student also active in the other org: that activity must not count for org A
        _attempt(org2, mine, exam2, at(ago(5)))
        r = funnel.compute_funnel(funnel.Scope(org), now=NOW)
        assert r['scope'] == 'organization' and r['demo_conversion'] is None
        assert r['new_users'] == 1
        assert (r['retention']['D1']['eligible'], r['retention']['D1']['retained']) == (1, 1)
        assert r['attempts_per_active_student']['active_students'] == 1
        assert r['attempts_per_active_student']['practice_sessions'] == 1
        assert r['attempts_per_active_student']['exam_attempts'] == 0
        assert exam1 is not None


@pytest.mark.integration
class TestFunnelAPI:
    URL = 'analytics:funnel'

    def test_staff_default_is_platform(self, db):
        body = _client(_user('st', is_staff=True)).get(reverse(self.URL)).json()
        assert body['scope'] == 'platform' and body['demo_conversion'] is not None

    def test_staff_can_pick_org(self, db, org):
        body = (
            _client(_user('st', is_staff=True)).get(reverse(self.URL), {'org': str(org.id)}).json()
        )
        assert body['scope'] == 'organization' and body['organization_id'] == str(org.id)

    def test_staff_bad_and_unknown_org(self, db):
        c = _client(_user('st', is_staff=True))
        assert c.get(reverse(self.URL), {'org': 'nope'}).status_code == 400
        assert c.get(reverse(self.URL), {'org': str(uuid.uuid4())}).status_code == 404

    def test_org_admin_sees_own_org_only(self, db, org, org2):
        u = _user('own')
        _member(u, org, 'owner')
        c = _client(u)
        body = c.get(reverse(self.URL)).json()
        assert body['scope'] == 'organization' and body['organization_id'] == str(org.id)
        assert c.get(reverse(self.URL), {'org': str(org.id)}).status_code == 200
        # NEGATIVE: another org is forbidden, even by explicit id
        assert c.get(reverse(self.URL), {'org': str(org2.id)}).status_code == 403

    @pytest.mark.parametrize('role', ['student', 'teacher', 'observer'])
    def test_non_admin_roles_forbidden(self, db, org, role):
        u = _user('x')
        _member(u, org, role)
        assert _client(u).get(reverse(self.URL)).status_code == 403

    def test_anonymous(self, db):
        assert Client().get(reverse(self.URL)).status_code in (401, 403)

    @pytest.mark.parametrize(
        'params', [{'days': 0}, {'days': 400}, {'days': 'x'}, {'cohort_days': 0}]
    )
    def test_param_validation(self, db, params):
        assert _client(_user('st', is_staff=True)).get(reverse(self.URL), params).status_code == 400

    def test_cached_for_15_minutes(self, db, org):
        c = _client(_user('st', is_staff=True))
        first = c.get(reverse(self.URL)).json()
        assert first['new_users'] == 0 and first['attempts_per_active_student']['value'] is None
        newbie = _user('newbie')
        _session(org, newbie, timezone.now())
        assert c.get(reverse(self.URL)).json() == first  # served from cache
        cache.clear()
        fresh = c.get(reverse(self.URL)).json()
        assert (
            fresh['attempts_per_active_student']['practice_sessions'] == 1
        )  # cache miss sees data
        assert funnel.CACHE_TTL == 900

    def test_cache_is_per_org(self, db, org, org2):
        a, b = _user('oa'), _user('ob')
        _member(a, org, 'owner')
        _member(b, org2, 'owner')
        assert _client(a).get(reverse(self.URL)).json()['organization_id'] == str(org.id)
        assert _client(b).get(reverse(self.URL)).json()['organization_id'] == str(org2.id)


# ═══════════════════════════════════════════════════════════════════════════
# Monthly organization report
# ═══════════════════════════════════════════════════════════════════════════

OCT = date(2026, 10, 1)


@pytest.fixture
def report_data(db, org, org2):
    admin = _user('rep_admin')
    s1 = _user('stu1', first_name='Zafar', last_name='K')
    s2 = _user('stu2', first_name='Aziza', last_name='M')
    s3 = _user('stu3')
    s4 = _user('stu4')
    for s in (s1, s2, s3, s4):
        _member(s, org, 'student')
    X = _exam(org, admin, 'X')
    Y = _exam(org, admin, 'Y', public=True)
    Z = _exam(org, admin, 'Z')
    W = _exam(org, admin, 'W')
    sub = ExamAttempt.Status.SUBMITTED

    def done(user, exam, when, score, status=sub, **kw):
        return _attempt(org, user, exam, when, score=score, status=status, submitted=when, **kw)

    # October (5 counted attempts)
    done(s1, X, at(date(2026, 10, 5)), '80.00')
    done(s1, Y, at(date(2026, 10, 6)), '50.00')
    done(s2, X, at(date(2026, 10, 7)), '60.00')  # == pass score: passes
    done(s3, X, at(date(2026, 10, 1), 0, 0), '59.99')  # first instant of October: included
    done(s2, Y, at(date(2026, 10, 8)), '70.00')
    # excluded: cancelled, deleted, submitted exactly at Nov 1 00:00, unscored in-progress
    done(s3, Z, at(date(2026, 10, 9)), '90.00', status=ExamAttempt.Status.CANCELLED)
    done(s1, Z, at(date(2026, 10, 9)), '100.00', deleted=True)
    done(s3, W, at(date(2026, 11, 1), 0, 0), '10.00')
    # September (2 attempts)
    done(s1, W, at(date(2026, 9, 30), 23, 59), '90.00')
    done(s2, W, at(date(2026, 9, 10)), '70.00')
    # practice (October): 70%, 50%, and one empty session that must be ignored
    _session(org, s4, at(date(2026, 10, 12)), 7, 10)
    _session(org, s1, at(date(2026, 10, 13)), 5, 10)
    _session(org, s1, at(date(2026, 10, 14)), 0, 0)

    # certificates
    with tenant_context(org):
        scale = GradeScale.objects.create(organization=org, name='n', bands=NATIONAL_BANDS)
        sitting = ExamSitting.objects.create(
            organization=org,
            mock_exam=X,
            grade_scale=scale,
            title='S',
            opens_at=NOW - timedelta(days=90),
            closes_at=NOW - timedelta(days=80),
            created_by=admin,
        )
    far = NOW + timedelta(days=900)

    def cert(n, issued, valid_until=far, revoke=False, organization=org):
        holder = _user(f'holder{n}')
        att = _attempt(organization, holder, X, at(date(2026, 8, 1)))
        with tenant_context(organization):
            res = SittingResult.objects.create(
                organization=organization,
                sitting=sitting,
                attempt=att,
                user=holder,
                raw_score=5,
                max_raw_score=6,
                ability=1.0,
                scaled_score=Decimal('60'),
                grade='B',
            )
            c = Certificate.objects.create(
                organization=organization,
                number=f'C-{n:05d}',
                user=holder,
                sitting=sitting,
                result=res,
                scaled_score=Decimal('60'),
                grade='B',
                issued_at=issued,
                valid_until=valid_until,
            )
        if revoke:
            Certificate.global_objects.filter(pk=c.pk).update(revoked_at=NOW, revoked_reason='r')

    cert(1, at(date(2026, 10, 3)))
    cert(2, at(date(2026, 10, 31), 23, 59))
    cert(3, at(date(2026, 10, 10)), revoke=True)
    cert(4, at(date(2026, 10, 11)), valid_until=at(date(2026, 10, 20)))  # expired by now
    cert(5, at(date(2026, 11, 1), 0, 0))  # November: not in October
    cert(6, at(date(2026, 9, 30), 23, 59))  # September
    return admin, (s1, s2, s3, s4)


@pytest.mark.integration
class TestOrgReportNumbers:
    def test_headline_and_deltas(self, report_data, org):
        r = org_report.build_report(org, OCT)
        assert r['month'] == '2026-10' and r['previous_month'] == '2026-09'
        assert r['attempts'] == 5
        assert r['active_students'] == 4  # stu1..stu3 attempts + stu4 practice-only
        assert r['avg_score'] == 64.0  # (80+50+60+59.99+70)/5 = 63.998
        assert r['pass_rate_percent'] == 60.0  # 80, 60, 70 pass
        assert r['previous']['attempts'] == 2 and r['previous']['avg_score'] == 80.0
        assert r['previous']['active_students'] == 2 and r['previous']['pass_rate_percent'] == 100.0
        assert r['deltas'] == {
            'attempts': 3,
            'active_students': 2,
            'avg_score': -16.0,
            'pass_rate_percent': -40.0,
        }

    def test_pass_score_boundary(self, report_data, org):
        assert org_report.build_report(org, OCT, pass_score=60)['pass_rate_percent'] == 60.0
        assert org_report.build_report(org, OCT, pass_score=60.01)['pass_rate_percent'] == 40.0
        assert org_report.build_report(org, OCT, pass_score=59.99)['pass_rate_percent'] == 80.0

    def test_by_exam_type(self, report_data, org):
        rows = {r['exam_type']: r for r in org_report.build_report(org, OCT)['by_exam_type']}
        assert rows['org_exam'] == {
            'exam_type': 'org_exam',
            'attempts': 3,
            'avg_score': 66.66,
            'pass_rate_percent': 66.7,
        }
        assert rows['platform_mock'] == {
            'exam_type': 'platform_mock',
            'attempts': 2,
            'avg_score': 60.0,
            'pass_rate_percent': 50.0,
        }
        assert rows['practice'] == {
            'exam_type': 'practice',
            'attempts': 2,
            'avg_score': 60.0,
            'pass_rate_percent': 50.0,
        }

    def test_top_students_order_and_ties(self, report_data, org):
        top = org_report.build_report(org, OCT)['top_students']
        # s1 and s2 tie on 65.0 / 2 attempts -> by name: "Aziza M" before "Zafar K"
        assert [(t['name'], t['attempts'], t['avg_score']) for t in top] == [
            ('Aziza M', 2, 65.0),
            ('Zafar K', 2, 65.0),
            ('stu3', 1, 59.99),
        ]

    def test_certificates(self, report_data, org):
        assert org_report.build_report(org, OCT)['certificates'] == {
            'issued': 4,
            'valid': 2,
            'revoked': 1,
            'expired': 1,
        }

    def test_empty_month_has_nulls_not_errors(self, report_data, org):
        r = org_report.build_report(org, date(2026, 1, 1))
        assert r['attempts'] == 0 and r['avg_score'] is None and r['pass_rate_percent'] is None
        assert r['deltas']['avg_score'] is None  # no previous attempts to compare
        assert r['top_students'] == []

    def test_delta_null_without_previous_data(self, report_data, org):
        r = org_report.build_report(org, date(2026, 9, 1))  # previous month (Aug) empty
        assert r['attempts'] == 2
        assert r['deltas']['avg_score'] is None and r['deltas']['pass_rate_percent'] is None
        assert r['deltas']['attempts'] == 2

    def test_cross_tenant_isolation(self, report_data, org, org2):
        admin2 = _user('adm2')
        s = _user('other_stu')
        _member(s, org2, 'student')
        ex = _exam(org2, admin2)
        _attempt(
            org2,
            s,
            ex,
            at(date(2026, 10, 5)),
            score='99.00',
            status='submitted',
            submitted=at(date(2026, 10, 5)),
        )
        a, b = org_report.build_report(org, OCT), org_report.build_report(org2, OCT)
        assert a['attempts'] == 5 and a['avg_score'] == 64.0
        assert b['attempts'] == 1 and b['avg_score'] == 99.0
        assert all(t['name'] != 'other_stu' for t in a['top_students'])
        assert b['certificates']['issued'] == 0


@pytest.mark.integration
class TestOrgReportExports:
    def _rows(self, raw: bytes):
        return list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))

    def test_csv_bom_and_content(self, report_data, org):
        raw = org_report.to_csv(org_report.build_report(org, OCT))
        assert raw.startswith(b'\xef\xbb\xbf')  # BOM so Excel detects UTF-8
        assert raw.count(b'\xef\xbb\xbf') == 1
        rows = self._rows(raw)
        assert rows[0] == ['Report', 'Test Org', '2026-10']
        assert ['attempts', '5', '2', '3'] in rows
        assert ['avg_score', '64.0', '80.0', '-16.0'] in rows
        assert ['org_exam', '3', '66.66', '66.7'] in rows
        assert ['1', 'Aziza M', '2', '65.0'] in rows
        assert ['issued', '4'] in rows and ['revoked', '1'] in rows

    def test_csv_keeps_non_ascii_names(self, report_data, org):
        u = _user('uz', first_name='Oʻtkir', last_name='Ғуломов')
        _member(u, org, 'student')
        _attempt(
            org,
            u,
            _exam(org, u, 'U'),
            at(date(2026, 10, 20)),
            score='100',
            status='submitted',
            submitted=at(date(2026, 10, 20)),
        )
        rows = self._rows(org_report.to_csv(org_report.build_report(org, OCT)))
        assert ['1', 'Oʻtkir Ғуломов', '1', '100.0'] in rows

    def test_csv_formula_injection_neutralised(self, report_data, org):
        u = _user('evil', first_name='=HYPERLINK("http://x")', last_name='')
        _member(u, org, 'student')
        _attempt(
            org,
            u,
            _exam(org, u, 'V'),
            at(date(2026, 10, 20)),
            score='100',
            status='submitted',
            submitted=at(date(2026, 10, 20)),
        )
        rows = self._rows(org_report.to_csv(org_report.build_report(org, OCT)))
        names = [r[1] for r in rows if len(r) == 4 and r[0].isdigit()]
        assert '\'=HYPERLINK("http://x")' in names
        assert not any(n.startswith('=') for n in names)

    def test_html_escapes(self, report_data, org):
        u = _user('xss', first_name='<script>alert(1)</script>', last_name='')
        _member(u, org, 'student')
        _attempt(
            org,
            u,
            _exam(org, u, 'V'),
            at(date(2026, 10, 20)),
            score='100',
            status='submitted',
            submitted=at(date(2026, 10, 20)),
        )
        html = org_report.to_html(org_report.build_report(org, OCT))
        assert '<script>alert' not in html and '&lt;script&gt;' in html
        assert 'Test Org' in html and '@media print' in html


@pytest.mark.integration
class TestOrgReportAPI:
    URL = 'analytics:org-report'

    def _owner(self, org, name='o1'):
        u = _user(name)
        _member(u, org, 'owner')
        return _client(u)

    def test_json_default_and_month(self, report_data, org):
        body = self._owner(org).get(reverse(self.URL), {'month': '2026-10'}).json()
        assert body['attempts'] == 5 and body['organization_name'] == 'Test Org'

    def test_csv_download(self, report_data, org):
        resp = self._owner(org).get(reverse(self.URL), {'month': '2026-10', 'export': 'csv'})
        assert resp.status_code == 200
        assert resp['Content-Type'] == 'text/csv; charset=utf-8'
        assert 'report-test-org-2026-10.csv' in resp['Content-Disposition']
        assert resp.content.startswith(b'\xef\xbb\xbf')
        assert b'attempts,5,2,3' in resp.content

    def test_html_export(self, report_data, org):
        resp = self._owner(org).get(reverse(self.URL), {'month': '2026-10', 'export': 'html'})
        assert resp.status_code == 200 and resp['Content-Type'].startswith('text/html')
        assert b'Test Org' in resp.content

    @pytest.mark.parametrize(
        'params',
        [
            {'month': '2026-13'},
            {'month': 'oct'},
            {'month': '2999-01'},
            {'export': 'pdf'},
            {'pass_score': 101},
        ],
    )
    def test_validation(self, db, org, params):
        assert self._owner(org).get(reverse(self.URL), params).status_code == 400

    @pytest.mark.parametrize('role', ['student', 'teacher', 'observer'])
    def test_non_admin_forbidden(self, db, org, role):
        u = _user('x')
        _member(u, org, role)
        assert _client(u).get(reverse(self.URL)).status_code == 403

    def test_org_admin_cannot_see_other_org(self, report_data, org, org2):
        c = self._owner(org)
        # NEGATIVE: explicit foreign org id is rejected for json and exports alike
        for extra in ({}, {'export': 'csv'}, {'export': 'html'}):
            resp = c.get(reverse(self.URL), {'month': '2026-10', 'org': str(org2.id), **extra})
            assert resp.status_code == 403

    def test_staff_can_pick_any_org_and_needs_one(self, report_data, org, org2):
        c = _client(_user('st', is_staff=True))
        assert (
            c.get(reverse(self.URL), {'month': '2026-10', 'org': str(org.id)}).json()['attempts']
            == 5
        )
        assert (
            c.get(reverse(self.URL), {'month': '2026-10', 'org': str(org2.id)}).json()['attempts']
            == 0
        )
        assert c.get(reverse(self.URL), {'month': '2026-10'}).status_code == 400  # no org context
        assert c.get(reverse(self.URL), {'org': str(uuid.uuid4())}).status_code == 404

    def test_anonymous(self, db):
        assert Client().get(reverse(self.URL)).status_code in (401, 403)
