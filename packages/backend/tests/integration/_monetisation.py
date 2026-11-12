"""Shared factories for the monetisation tests (not collected: no test_ prefix)."""

from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.commerce.models import ExamType, SubscriptionPlan
from apps.organizations.models import Membership, MembershipStatus, OrgRole

User = get_user_model()


def exam_type(code, *, enabled=True):
    et, _ = ExamType.objects.get_or_create(code=code, defaults={'name': code.upper()})
    if et.is_enabled != enabled:
        et.is_enabled = enabled
        et.save(update_fields=['is_enabled'])
    return et


def student_plan(slug, mode, *, price='100000', et=None, unlimited=(), weekly=0, attempts=1):
    plan = SubscriptionPlan.objects.create(
        name=slug,
        slug=slug,
        audience='student',
        access_mode=mode,
        exam_type=et,
        billing_period='one_time' if mode == 'exam_key' else 'monthly',
        pricing_model='flat',
        price_uzs=Decimal(price),
        weekly_limit=weekly,
        attempts_included=attempts,
    )
    if unlimited:
        plan.unlimited_exam_types.set(unlimited)
    return plan


def make_user(name):
    return User.objects.create_user(username=name, email=f'{name}@example.com', password='pass123')


def join(user, org, role='student', status=MembershipStatus.ACTIVE):
    m = Membership.objects.create(
        user=user,
        organization=org,
        role=OrgRole.objects.get(organization=org, name=role),
        status=status,
        is_primary=True,
    )
    if status == MembershipStatus.ACTIVE:
        m.activate()
    return m
