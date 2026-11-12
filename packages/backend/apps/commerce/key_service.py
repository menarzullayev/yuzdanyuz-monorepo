"""
B2B exam keys.

  - grant_keys(org, exam_type, quantity, *, source, ...) -> ExamKeyBatch
  - distribute_keys(org, exam_type, user_ids, per_user, group_label) -> list[ExamKey]
  - redeem_key(user, org, code) -> ExamKey
  - revoke_keys(org, key_ids) -> int
  - summary(org) -> list[dict]   issued / distributed / consumed / remaining per exam type

All functions take the organization explicitly and filter on it, so they are safe to
call outside a request tenant context (webhook, staff grant).
"""

import uuid
from contextlib import contextmanager

from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import ExamKey, ExamKeyBatch, ExamType

MAX_KEYS_PER_GRANT = 1000


class KeyError_(Exception):  # noqa: N818 - domain error with a machine-readable code
    def __init__(self, code: str, message: str, status: int = 400, extra: dict | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra or {}
        super().__init__(message)


@contextmanager
def org_rls(org):
    """Tenant context + transaction-local RLS variable for writes outside a request.

    Webhooks and staff grants have no request org; under a non-owner DB role the RLS
    WITH CHECK would reject the insert without this.
    """
    from core.tenant import tenant_context

    with transaction.atomic(), tenant_context(org):
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_org_id', %s, true)", [str(org.id)])
        yield


def grant_keys(
    org,
    exam_type: ExamType,
    quantity: int,
    *,
    source,
    payment_intent=None,
    granted_by=None,
    note='',
) -> ExamKeyBatch:
    if not (1 <= quantity <= MAX_KEYS_PER_GRANT):
        raise KeyError_(
            'invalid_quantity', f"Kalit soni 1..{MAX_KEYS_PER_GRANT} oralig'ida bo'lishi kerak."
        )
    with org_rls(org):
        batch = ExamKeyBatch.objects.create(
            organization=org,
            exam_type=exam_type,
            quantity=quantity,
            source=source,
            payment_intent=payment_intent,
            granted_by=granted_by,
            note=note[:255],
        )
        ExamKey.objects.bulk_create(
            [ExamKey(organization=org, batch=batch, exam_type=exam_type) for _ in range(quantity)]
        )
    return batch


def distribute_keys(org, exam_type, user_ids, per_user: int = 1, group_label: str = '') -> list:
    """Assign `per_user` available keys to each listed org member. All-or-nothing."""
    from apps.organizations.models import Membership

    user_ids = list(dict.fromkeys(uuid.UUID(str(u)) for u in user_ids))  # ValueError -> caller
    if not user_ids or per_user < 1:
        raise KeyError_('invalid_request', "user_ids bo'sh yoki per_user < 1.")
    member_ids = set(
        Membership.objects.filter(
            organization=org, status='active', user_id__in=user_ids
        ).values_list('user_id', flat=True)
    )
    missing = [str(u) for u in user_ids if u not in member_ids]
    if missing:
        raise KeyError_(
            'not_a_member',
            "Ba'zi foydalanuvchilar bu tashkilotning faol a'zosi emas.",
            extra={'user_ids': missing},
        )
    needed = len(user_ids) * per_user
    now = timezone.now()
    with org_rls(org):
        keys = list(
            ExamKey.objects.select_for_update()
            .filter(organization=org, exam_type=exam_type, status=ExamKey.Status.AVAILABLE)
            .order_by('created_at', 'id')[:needed]
        )
        if len(keys) < needed:
            raise KeyError_(
                'insufficient_keys',
                f"Yetarli kalit yo'q: kerak {needed}, mavjud {len(keys)}.",
                extra={'needed': needed, 'available': len(keys)},
            )
        it = iter(keys)
        for uid in user_ids:
            for _ in range(per_user):
                k = next(it)
                k.status = ExamKey.Status.ASSIGNED
                k.assigned_to_id = uid
                k.assigned_at = now
                k.group_label = group_label[:64]
        ExamKey.objects.bulk_update(
            keys, ['status', 'assigned_to', 'assigned_at', 'group_label', 'updated_at']
        )
    return keys


def redeem_key(user, org, code: str) -> ExamKey:
    """A member claims an unassigned key by code. Wrong org / unknown code look identical."""
    from apps.organizations.models import Membership

    generic = KeyError_('invalid_key', 'Kalit topilmadi.', status=404)
    if org is None:
        raise generic
    if not Membership.objects.filter(user=user, organization=org, status='active').exists():
        raise generic
    with org_rls(org):
        key = (
            ExamKey.objects.select_for_update()
            .filter(organization=org, code=(code or '').strip().upper())
            .first()
        )
        if key is None:
            raise generic
        if key.status == ExamKey.Status.ASSIGNED and key.assigned_to_id == user.id:
            return key
        if key.status == ExamKey.Status.CONSUMED:
            raise KeyError_('key_consumed', 'Bu kalit allaqachon ishlatilgan.', status=409)
        if key.status != ExamKey.Status.AVAILABLE:
            raise KeyError_('key_unavailable', "Bu kalitdan foydalanib bo'lmaydi.", status=409)
        key.status = ExamKey.Status.ASSIGNED
        key.assigned_to = user
        key.assigned_at = timezone.now()
        key.save(update_fields=['status', 'assigned_to', 'assigned_at', 'updated_at'])
    return key


def revoke_keys(org, key_ids) -> int:
    """Cancel unused keys (available or assigned). Consumed keys are never touched."""
    with org_rls(org):
        return ExamKey.objects.filter(
            organization=org,
            pk__in=key_ids,
            status__in=[ExamKey.Status.AVAILABLE, ExamKey.Status.ASSIGNED],
        ).update(status=ExamKey.Status.REVOKED, updated_at=timezone.now())


def summary(org) -> list[dict]:
    rows = (
        ExamKey.objects.filter(organization=org)
        .values('exam_type__code', 'exam_type__name')
        .annotate(
            issued=Count('id'),
            available=Count('id', filter=Q(status=ExamKey.Status.AVAILABLE)),
            assigned=Count('id', filter=Q(status=ExamKey.Status.ASSIGNED)),
            consumed=Count('id', filter=Q(status=ExamKey.Status.CONSUMED)),
            revoked=Count('id', filter=Q(status=ExamKey.Status.REVOKED)),
        )
        .order_by('exam_type__code')
    )
    return [
        {
            'exam_type': r['exam_type__code'],
            'exam_type_name': r['exam_type__name'],
            'issued': r['issued'],
            'distributed': r['assigned'] + r['consumed'],
            'consumed': r['consumed'],
            'remaining': r['available'] + r['assigned'],
            'unassigned': r['available'],
            'revoked': r['revoked'],
        }
        for r in rows
    ]
