"""
Exam access service: "can this user start this exam type now, and what is consumed".

API:
  - check_access(user, exam_type_code, *, now=None) -> AccessDecision   (no side effects)
  - consume_access(user, exam_type_code, *, reference='', now=None) -> AccessDecision
      Atomic: re-evaluates under a per-user lock and records the consumption.
  - week_window(now) -> (start, end)   Monday 00:00 Asia/Tashkent .. next Monday
  - free_attempts_left(user)

Source priority (first match wins):
  1. current subscription that covers the type without a limit
       all_types | single_type(type) | student(type in unlimited_exam_types)
  2. student plan weekly limit (non-unlimited types), counted per week
  3. B2B exam key assigned to the user in an organization where they are an active member
  4. free attempts (settings.BILLING_FREE_ATTEMPTS per user, all types together)
  5. B2C one-time pass (exam_key plan, attempts_remaining > 0)
  otherwise denied (`weekly_limit_reached` if a student plan hit its limit, else
  `payment_required`). Unknown / disabled exam types are always denied.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from . import wallet_service
from .models import (
    ExamAccessUse,
    ExamKey,
    ExamType,
    SubscriptionPlan,
    UserSubscription,
)

# Denial reason codes
UNKNOWN_EXAM_TYPE = 'exam_type_unknown'
EXAM_TYPE_DISABLED = 'exam_type_disabled'
WEEKLY_LIMIT_REACHED = 'weekly_limit_reached'
PAYMENT_REQUIRED = 'payment_required'

# Allow reason codes
SUB_ALL_TYPES = 'subscription_all_types'
SUB_SINGLE_TYPE = 'subscription_single_type'
SUB_STUDENT_UNLIMITED = 'subscription_student_unlimited_type'
SUB_STUDENT_WEEKLY = 'subscription_student_weekly'
ORG_KEY = 'org_key'
FREE_ATTEMPT = 'free_attempt'
EXAM_PASS = 'exam_pass'

MESSAGES = {
    UNKNOWN_EXAM_TYPE: "Noma'lum imtihon turi.",
    EXAM_TYPE_DISABLED: 'Bu imtihon turi hozircha mavjud emas.',
    WEEKLY_LIMIT_REACHED: 'Haftalik limit tugadi. Limit dushanba kuni yangilanadi.',
    PAYMENT_REQUIRED: 'Bepul urinishlar tugagan. Davom etish uchun tarif yoki kalit sotib oling.',
}


@dataclass
class AccessDecision:
    allowed: bool
    reason: str
    message: str = ''
    # what would be / was consumed: {'kind': 'free'|'org_key'|'exam_pass'|'subscription_weekly'|..., 'id': str|None}
    consumes: dict | None = None
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            'allowed': self.allowed,
            'reason': self.reason,
            'message': self.message,
            'consumes': self.consumes,
            'details': self.details,
        }


def _week_tz() -> ZoneInfo:
    return ZoneInfo(getattr(settings, 'BILLING_WEEK_TZ', 'Asia/Tashkent'))


def week_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[Monday 00:00, next Monday 00:00) in the billing timezone, as aware datetimes."""
    now = now or timezone.now()
    local = now.astimezone(_week_tz())
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(days=7)


def free_attempts_total() -> int:
    return max(int(getattr(settings, 'BILLING_FREE_ATTEMPTS', 0)), 0)


def free_attempts_used(user) -> int:
    return ExamAccessUse.objects.filter(user=user, source=ExamAccessUse.Source.FREE).count()


def free_attempts_left(user) -> int:
    return max(free_attempts_total() - free_attempts_used(user), 0)


def _current_subscriptions(user, now):
    return (
        UserSubscription.objects.filter(
            user=user, status=UserSubscription.Status.ACTIVE, plan__audience='student'
        )
        .filter(Q(current_period_ends_at__isnull=True) | Q(current_period_ends_at__gt=now))
        .select_related('plan', 'plan__exam_type')
        .prefetch_related('plan__unlimited_exam_types')
        .order_by('created_at')
    )


def _active_member_org_ids(user) -> list:
    from apps.organizations.models import Membership

    return list(
        Membership.objects.filter(user=user, status='active').values_list(
            'organization_id', flat=True
        )
    )


def _assigned_key_qs(user, exam_type):
    """Keys assigned to `user`, restricted to organizations where they are an active member.

    Uses `global_objects` (no request tenant context in a service) with an explicit
    organization filter: an org-B key can never match a user who is not an org-B member.
    """
    return ExamKey.global_objects.filter(
        assigned_to=user,
        exam_type=exam_type,
        status=ExamKey.Status.ASSIGNED,
        organization_id__in=_active_member_org_ids(user),
    ).order_by('assigned_at', 'id')


def _weekly_used(user, now) -> int:
    start, end = week_window(now)
    return ExamAccessUse.objects.filter(
        user=user,
        source=ExamAccessUse.Source.SUBSCRIPTION_WEEKLY,
        created_at__gte=start,
        created_at__lt=end,
    ).count()


def _evaluate(user, exam_type_code: str, now: datetime, *, lock: bool = False) -> AccessDecision:
    exam_type = ExamType.objects.filter(code=exam_type_code).first()
    if exam_type is None:
        return AccessDecision(False, UNKNOWN_EXAM_TYPE, MESSAGES[UNKNOWN_EXAM_TYPE])
    if not exam_type.is_enabled:
        return AccessDecision(False, EXAM_TYPE_DISABLED, MESSAGES[EXAM_TYPE_DISABLED])

    start, end = week_window(now)
    details = {
        'exam_type': exam_type.code,
        'free_attempts_left': free_attempts_left(user),
        'week_resets_at': end.isoformat(),
    }
    subs = list(_current_subscriptions(user, now))

    # 1. unlimited subscription coverage
    for sub in subs:
        plan = sub.plan
        if plan.access_mode == SubscriptionPlan.AccessMode.ALL_TYPES:
            return AccessDecision(
                True,
                SUB_ALL_TYPES,
                consumes={'kind': 'subscription_unlimited', 'id': str(sub.id)},
                details=details,
            )
        if (
            plan.access_mode == SubscriptionPlan.AccessMode.SINGLE_TYPE
            and plan.exam_type_id == exam_type.id
        ):
            return AccessDecision(
                True,
                SUB_SINGLE_TYPE,
                consumes={'kind': 'subscription_unlimited', 'id': str(sub.id)},
                details=details,
            )
        if plan.access_mode == SubscriptionPlan.AccessMode.STUDENT and any(
            t.id == exam_type.id for t in plan.unlimited_exam_types.all()
        ):
            return AccessDecision(
                True,
                SUB_STUDENT_UNLIMITED,
                consumes={'kind': 'subscription_unlimited', 'id': str(sub.id)},
                details=details,
            )

    # 2. student weekly limit (types that are not unlimited in that plan)
    weekly_blocked = False
    student_subs = [s for s in subs if s.plan.access_mode == SubscriptionPlan.AccessMode.STUDENT]
    if student_subs:
        limit = max(s.plan.weekly_limit for s in student_subs)
        used = _weekly_used(user, now)
        details.update({'weekly_limit': limit, 'weekly_used': used})
        if used < limit:
            best = max(student_subs, key=lambda s: s.plan.weekly_limit)
            return AccessDecision(
                True,
                SUB_STUDENT_WEEKLY,
                consumes={'kind': 'subscription_weekly', 'id': str(best.id)},
                details=details,
            )
        weekly_blocked = True

    # 3. B2B key
    key_qs = _assigned_key_qs(user, exam_type)
    if lock:
        key_qs = key_qs.select_for_update(of=('self',))
    key = key_qs.first()
    if key is not None:
        return AccessDecision(
            True, ORG_KEY, consumes={'kind': 'org_key', 'id': str(key.id)}, details=details
        )

    # 4. free attempts
    if details['free_attempts_left'] > 0:
        return AccessDecision(
            True, FREE_ATTEMPT, consumes={'kind': 'free', 'id': None}, details=details
        )

    # 5. one-time pass
    pass_qs = UserSubscription.objects.filter(
        user=user,
        status=UserSubscription.Status.ACTIVE,
        plan__access_mode=SubscriptionPlan.AccessMode.EXAM_KEY,
        plan__exam_type=exam_type,
        attempts_remaining__gt=0,
    ).order_by('created_at')
    if lock:
        pass_qs = pass_qs.select_for_update(of=('self',))
    pass_sub = pass_qs.first()
    if pass_sub is not None:
        return AccessDecision(
            True, EXAM_PASS, consumes={'kind': 'exam_pass', 'id': str(pass_sub.id)}, details=details
        )

    reason = WEEKLY_LIMIT_REACHED if weekly_blocked else PAYMENT_REQUIRED
    return AccessDecision(False, reason, MESSAGES[reason], details=details)


def check_access(user, exam_type_code: str, *, now: datetime | None = None) -> AccessDecision:
    """Read-only preview. Never consumes anything."""
    return _evaluate(user, exam_type_code, now or timezone.now())


@transaction.atomic
def consume_access(
    user, exam_type_code: str, *, reference: str = '', now: datetime | None = None
) -> AccessDecision:
    """Decide and consume in one transaction.

    A per-user lock (the wallet row) serialises concurrent starts, so two parallel
    requests cannot both spend the last free attempt / weekly slot.
    """
    now = now or timezone.now()
    wallet = wallet_service.get_or_create_wallet(user)
    type(wallet).objects.select_for_update().get(pk=wallet.pk)

    decision = _evaluate(user, exam_type_code, now, lock=True)
    if not decision.allowed:
        return decision

    exam_type = ExamType.objects.get(code=exam_type_code)
    kind = decision.consumes['kind']
    obj_id = decision.consumes['id']
    use = {'user': user, 'exam_type': exam_type, 'reference': reference[:64]}

    if kind == 'org_key':
        # conditional UPDATE: a consumed/revoked key matches 0 rows and can't be reused
        updated = ExamKey.global_objects.filter(pk=obj_id, status=ExamKey.Status.ASSIGNED).update(
            status=ExamKey.Status.CONSUMED,
            consumed_by=user,
            consumed_at=now,
            consumed_reference=reference[:64],
            updated_at=now,
        )
        if updated != 1:  # pragma: no cover - guarded by the wallet lock + select_for_update
            return AccessDecision(False, PAYMENT_REQUIRED, MESSAGES[PAYMENT_REQUIRED])
        ExamAccessUse.objects.create(source=ExamAccessUse.Source.ORG_KEY, exam_key_id=obj_id, **use)
    elif kind == 'exam_pass':
        UserSubscription.objects.filter(pk=obj_id).update(
            attempts_remaining=F('attempts_remaining') - 1, updated_at=now
        )
        sub = UserSubscription.objects.get(pk=obj_id)
        if sub.attempts_remaining == 0:
            sub.status = UserSubscription.Status.EXPIRED
            sub.save(update_fields=['status', 'updated_at'])
        ExamAccessUse.objects.create(
            source=ExamAccessUse.Source.EXAM_PASS, subscription_id=obj_id, **use
        )
    elif kind == 'free':
        ExamAccessUse.objects.create(source=ExamAccessUse.Source.FREE, **use)
    elif kind == 'subscription_weekly':
        ExamAccessUse.objects.create(
            source=ExamAccessUse.Source.SUBSCRIPTION_WEEKLY, subscription_id=obj_id, **use
        )
        decision.details['weekly_used'] = decision.details.get('weekly_used', 0) + 1
    else:  # subscription_unlimited
        ExamAccessUse.objects.create(
            source=ExamAccessUse.Source.SUBSCRIPTION_UNLIMITED, subscription_id=obj_id, **use
        )
    return decision
