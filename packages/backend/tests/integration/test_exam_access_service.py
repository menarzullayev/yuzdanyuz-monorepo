"""Monetisation: exam access service — every branch, weekly reset, key isolation."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from apps.commerce import access_service as acc
from apps.commerce.models import ExamAccessUse, ExamKey, ExamKeyBatch, UserSubscription
from tests.integration._monetisation import exam_type, join, make_user, student_plan

TZ = ZoneInfo('Asia/Tashkent')


def _sub(user, plan, *, days=30, **kw):
    now = timezone.now()
    return UserSubscription.objects.create(
        user=user,
        plan=plan,
        started_at=now,
        current_period_ends_at=None
        if plan.access_mode == 'exam_key'
        else now + timedelta(days=days),
        attempts_remaining=plan.attempts_included if plan.access_mode == 'exam_key' else None,
        **kw,
    )


@pytest.fixture
def dtm(db):
    return exam_type('dtm')


@pytest.fixture
def ielts(db):
    return exam_type('ielts')


@pytest.fixture(autouse=True)
def three_free(settings):
    settings.BILLING_FREE_ATTEMPTS = 3


@pytest.mark.integration
class TestTypesAndFree:
    def test_unknown_type_denied(self, user):
        d = acc.check_access(user, 'nope')
        assert not d.allowed and d.reason == acc.UNKNOWN_EXAM_TYPE

    def test_disabled_type_denied_even_with_subscription(self, user, dtm):
        _sub(user, student_plan('all', 'all_types'))
        exam_type('dtm', enabled=False)
        d = acc.check_access(user, 'dtm')
        assert not d.allowed and d.reason == acc.EXAM_TYPE_DISABLED

    def test_free_attempts_then_payment_required(self, user, dtm):
        for i in range(3):
            d = acc.consume_access(user, 'dtm', reference=f'a{i}')
            assert d.allowed and d.reason == acc.FREE_ATTEMPT
        assert acc.free_attempts_left(user) == 0
        d = acc.consume_access(user, 'dtm')
        assert not d.allowed and d.reason == acc.PAYMENT_REQUIRED
        assert ExamAccessUse.objects.filter(user=user).count() == 3  # denial writes nothing

    def test_free_attempts_are_per_user_and_configurable(self, user, user2, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 1
        assert acc.consume_access(user, 'dtm').allowed
        assert not acc.consume_access(user, 'dtm').allowed
        assert acc.consume_access(user2, 'dtm').allowed

    def test_zero_free_attempts(self, user, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        assert acc.check_access(user, 'dtm').reason == acc.PAYMENT_REQUIRED

    def test_check_access_never_consumes(self, user, dtm):
        for _ in range(5):
            assert acc.check_access(user, 'dtm').allowed
        assert acc.free_attempts_left(user) == 3


@pytest.mark.integration
class TestSubscriptions:
    def test_all_types_unlimited_does_not_burn_free(self, user, dtm, ielts):
        _sub(user, student_plan('all', 'all_types'))
        for code in ('dtm', 'ielts', 'dtm'):
            d = acc.consume_access(user, code)
            assert d.allowed and d.reason == acc.SUB_ALL_TYPES
        assert acc.free_attempts_left(user) == 3

    def test_single_type_covers_only_its_type(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('only-ielts', 'single_type', et=ielts))
        assert acc.check_access(user, 'ielts').reason == acc.SUB_SINGLE_TYPE
        assert acc.check_access(user, 'dtm').reason == acc.PAYMENT_REQUIRED

    def test_student_plan_dtm_unlimited_other_weekly(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('stu', 'student', unlimited=[dtm], weekly=2))
        for _ in range(10):
            assert acc.consume_access(user, 'dtm').reason == acc.SUB_STUDENT_UNLIMITED
        assert acc.consume_access(user, 'ielts').reason == acc.SUB_STUDENT_WEEKLY
        assert acc.consume_access(user, 'ielts').reason == acc.SUB_STUDENT_WEEKLY
        d = acc.consume_access(user, 'ielts')
        assert not d.allowed and d.reason == acc.WEEKLY_LIMIT_REACHED
        assert d.details['weekly_limit'] == 2 and d.details['weekly_used'] == 2
        # unlimited type is still open at the limit
        assert acc.check_access(user, 'dtm').allowed

    def test_weekly_limit_is_configurable_per_plan(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('stu5', 'student', unlimited=[dtm], weekly=5))
        for _ in range(5):
            assert acc.consume_access(user, 'ielts').allowed
        assert not acc.consume_access(user, 'ielts').allowed

    def test_weekly_limit_zero_blocks_other_types(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('stu0', 'student', unlimited=[dtm], weekly=0))
        assert acc.check_access(user, 'ielts').reason == acc.WEEKLY_LIMIT_REACHED

    def test_weekly_limit_falls_through_to_free_attempts(self, user, dtm, ielts):
        _sub(user, student_plan('stu1', 'student', unlimited=[dtm], weekly=1))
        assert acc.consume_access(user, 'ielts').reason == acc.SUB_STUDENT_WEEKLY
        assert acc.consume_access(user, 'ielts').reason == acc.FREE_ATTEMPT

    def test_expired_and_cancelled_subscriptions_ignored(self, user, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        plan = student_plan('all', 'all_types')
        _sub(user, plan, days=-1)
        _sub(user, plan, status=UserSubscription.Status.CANCELLED)
        assert acc.check_access(user, 'dtm').reason == acc.PAYMENT_REQUIRED

    def test_other_users_subscription_does_not_apply(self, user, user2, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user2, student_plan('all', 'all_types'))
        assert not acc.check_access(user, 'dtm').allowed


@pytest.mark.integration
class TestWeeklyReset:
    def test_week_window_starts_monday_midnight_tashkent(self):
        sun = datetime(2026, 11, 15, 23, 59, tzinfo=TZ)  # Sunday
        mon = datetime(2026, 11, 16, 0, 0, tzinfo=TZ)  # Monday 00:00
        s1, e1 = acc.week_window(sun)
        s2, e2 = acc.week_window(mon)
        assert s1 == datetime(2026, 11, 9, 0, 0, tzinfo=TZ) and e1 == mon
        assert s2 == mon and e2 == datetime(2026, 11, 23, 0, 0, tzinfo=TZ)

    def test_week_boundary_uses_tashkent_not_utc(self):
        # Sunday 20:00 UTC is already Monday 01:00 in Tashkent (UTC+5)
        utc_sun = datetime(2026, 11, 15, 20, 0, tzinfo=ZoneInfo('UTC'))
        start, _ = acc.week_window(utc_sun)
        assert start == datetime(2026, 11, 16, 0, 0, tzinfo=TZ)

    def test_usage_resets_on_monday(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('stu', 'student', unlimited=[dtm], weekly=1))
        sun = datetime(2026, 11, 15, 23, 59, tzinfo=TZ)
        mon = datetime(2026, 11, 16, 0, 0, tzinfo=TZ)
        assert acc.consume_access(user, 'ielts', now=sun).allowed
        ExamAccessUse.objects.filter(user=user).update(created_at=sun)
        assert acc.check_access(user, 'ielts', now=sun).reason == acc.WEEKLY_LIMIT_REACHED
        assert acc.check_access(user, 'ielts', now=mon).allowed  # new week

    def test_usage_before_monday_counts_in_same_week(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('stu', 'student', unlimited=[dtm], weekly=1))
        tue = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        sat = datetime(2026, 11, 14, 9, 0, tzinfo=TZ)
        assert acc.consume_access(user, 'ielts', now=tue).allowed
        ExamAccessUse.objects.filter(user=user).update(created_at=tue)
        assert not acc.check_access(user, 'ielts', now=sat).allowed


def _org_key(org, et, user=None, status=ExamKey.Status.ASSIGNED):
    batch = ExamKeyBatch.global_objects.create(
        organization=org, exam_type=et, quantity=1, source='platform_grant'
    )
    return ExamKey.global_objects.create(
        organization=org,
        batch=batch,
        exam_type=et,
        status=status,
        assigned_to=user,
        assigned_at=timezone.now() if user else None,
    )


@pytest.mark.integration
class TestOrgKeysAndPass:
    def test_org_key_used_before_free_and_consumed_once(self, user, org, member, dtm):
        key = _org_key(org, dtm, user)
        d = acc.consume_access(user, 'dtm', reference='att-1')
        assert d.reason == acc.ORG_KEY
        key.refresh_from_db()
        assert key.status == ExamKey.Status.CONSUMED and key.consumed_by_id == user.id
        assert key.consumed_reference == 'att-1'
        assert acc.free_attempts_left(user) == 3
        # the consumed key is gone: next start falls to free attempts
        assert acc.consume_access(user, 'dtm').reason == acc.FREE_ATTEMPT

    def test_key_is_type_specific(self, user, org, member, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _org_key(org, ielts, user)
        assert acc.check_access(user, 'dtm').reason == acc.PAYMENT_REQUIRED
        assert acc.check_access(user, 'ielts').reason == acc.ORG_KEY

    def test_key_of_other_org_unusable_by_non_member(self, user, org, org2, member, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _org_key(org2, dtm, user)  # assigned to `user`, who is only a member of `org`
        assert acc.check_access(user, 'dtm').reason == acc.PAYMENT_REQUIRED

    def test_key_unusable_after_membership_suspended(self, user, org, dtm, settings):
        from apps.organizations.models import MembershipStatus

        settings.BILLING_FREE_ATTEMPTS = 0
        join(user, org, status=MembershipStatus.SUSPENDED)
        _org_key(org, dtm, user)
        assert not acc.check_access(user, 'dtm').allowed

    def test_unassigned_and_revoked_keys_unusable(self, user, org, member, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _org_key(org, dtm, None, status=ExamKey.Status.AVAILABLE)
        _org_key(org, dtm, user, status=ExamKey.Status.REVOKED)
        assert not acc.check_access(user, 'dtm').allowed

    def test_exam_pass_consumed_then_expires(self, user, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        sub = _sub(user, student_plan('pass-dtm', 'exam_key', et=dtm))
        d = acc.consume_access(user, 'dtm')
        assert d.reason == acc.EXAM_PASS
        sub.refresh_from_db()
        assert sub.attempts_remaining == 0 and sub.status == UserSubscription.Status.EXPIRED
        assert not acc.check_access(user, 'dtm').allowed

    def test_exam_pass_is_type_specific(self, user, dtm, ielts, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('pass-ielts', 'exam_key', et=ielts))
        assert not acc.check_access(user, 'dtm').allowed

    def test_multi_attempt_pass(self, user, dtm, settings):
        settings.BILLING_FREE_ATTEMPTS = 0
        _sub(user, student_plan('pass3', 'exam_key', et=dtm, attempts=3))
        assert [acc.consume_access(user, 'dtm').allowed for _ in range(4)] == [True] * 3 + [False]
