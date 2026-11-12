"""Audit journal over django-simple-history tables.

Every model that declares ``HistoricalRecords`` is discovered automatically. Platform staff see
all of them; an org admin (``members:manage``) sees only rows of their own organization, and
only for models that carry an organization (users, wallets and plans stay platform-only).
"""

import csv
import io
from datetime import datetime, time, timedelta

from django.apps import apps
from django.utils import timezone

from apps.ops.models import SecurityEvent
from apps.ops.services.status import TASHKENT

ACTIONS = {'+': 'created', '~': 'changed', '-': 'deleted'}
ACTION_TO_TYPE = {v: k for k, v in ACTIONS.items()}
SENSITIVE_PARTS = ('password', 'token', 'secret', 'hash', 'key')
MAX_VALUE_LEN = 200
EXPORT_LIMIT = 10000
# Org-scoped lookup per model label: the historical-table field that holds the organization id.
ORG_FIELD = {
    'organizations.organization': 'id',
    'organizations.orgrole': 'organization_id',
    'organizations.membership': 'organization_id',
    'catalog.question': 'organization_id',
    'exams.examattempt': 'organization_id',
    'commerce.organizationsubscription': 'organization_id',
}


def audited_models() -> dict:
    """label ('app.model') -> (model, historical model)."""
    found = {}
    for model in apps.get_models():
        hist = getattr(getattr(model, 'history', None), 'model', None)
        if hist is not None and hasattr(hist, 'history_type'):
            found[model._meta.label_lower] = (model, hist)
    return found


def _label(rec) -> str:
    for attr in ('username', 'name', 'title', 'slug', 'number'):
        value = getattr(rec, attr, None)
        if value:
            return str(value)[:100]
    return _object_id(rec)


def _object_id(rec) -> str:
    # ``rec.pk`` of a historical row is the history id; the audited object's key is its own field.
    return str(getattr(rec, rec.instance_type._meta.pk.attname))


def _clean(value):
    text = '' if value is None else str(value)
    return text[:MAX_VALUE_LEN]


def _diff(rec) -> list[dict]:
    if rec.history_type != '~':
        return []
    prev = rec.prev_record
    if prev is None:
        return []
    out = []
    for change in rec.diff_against(prev).changes:
        if any(p in change.field.lower() for p in SENSITIVE_PARTS):
            out.append({'field': change.field, 'old': '[hidden]', 'new': '[hidden]'})
        else:
            out.append(
                {'field': change.field, 'old': _clean(change.old), 'new': _clean(change.new)}
            )
    return out


def serialize(label: str, rec) -> dict:
    user = rec.history_user
    return {
        'id': f'{label}:{rec.history_id}',
        'when': rec.history_date.astimezone(TASHKENT).isoformat(),
        'user': {'id': str(user.pk), 'username': user.username} if user else None,
        'action': ACTIONS.get(rec.history_type, rec.history_type),
        'model': label,
        'object_id': _object_id(rec),
        'object': _label(rec),
        'changes': _diff(rec),
        'ip': None,  # history rows do not store the client IP
        'reason': rec.history_change_reason or '',
    }


def _day_start(day: str):
    return datetime.combine(datetime.strptime(day, '%Y-%m-%d').date(), time.min, tzinfo=TASHKENT)


def scoped_querysets(
    *,
    org_id=None,
    platform: bool,
    model=None,
    user_id=None,
    action=None,
    date_from=None,
    date_to=None,
):
    """Yield (label, queryset) for each audited model after scope + filters."""
    for label, (_, hist) in audited_models().items():
        if model and model != label:
            continue
        qs = hist.objects.all()
        if platform:
            if org_id:
                field = ORG_FIELD.get(label)
                if field is None:
                    continue
                qs = qs.filter(**{field: org_id})
        else:
            field = ORG_FIELD.get(label)
            if field is None or org_id is None:
                continue
            qs = qs.filter(**{field: org_id})
        if user_id:
            qs = qs.filter(history_user_id=user_id)
        if action:
            qs = qs.filter(history_type=ACTION_TO_TYPE[action])
        if date_from:
            qs = qs.filter(history_date__gte=_day_start(date_from))
        if date_to:
            qs = qs.filter(history_date__lt=_day_start(date_to) + timedelta(days=1))
        yield label, qs.select_related('history_user')


def page(querysets, offset: int, limit: int) -> tuple[list[dict], int]:
    """Merge per-model querysets newest-first; returns (rows, total)."""
    total, rows = 0, []
    for label, qs in querysets:
        total += qs.count()
        for rec in qs.order_by('-history_date')[: offset + limit]:
            rows.append((rec.history_date, label, rec))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [serialize(label, rec) for _, label, rec in rows[offset : offset + limit]], total


def dangerous_today(*, org_id=None, platform: bool) -> dict:
    """Counter of risky actions since local midnight."""
    today = timezone.now().astimezone(TASHKENT).date().isoformat()
    kw = {'org_id': org_id, 'platform': platform, 'date_from': today}
    deletes = role_changes = payment_changes = 0
    for label, qs in scoped_querysets(**kw):
        deletes += qs.filter(history_type='-').count()
        if label == 'organizations.membership':
            for rec in qs.filter(history_type='~'):
                if any(c['field'] == 'role' for c in _diff(rec)):
                    role_changes += 1
        if label == 'commerce.organizationsubscription':
            for rec in qs.filter(history_type='~'):
                if any(c['field'] == 'status' for c in _diff(rec)):
                    payment_changes += 1
    resets = SecurityEvent.objects.filter(
        event_type=SecurityEvent.Type.PASSWORD_RESET, created_at__gte=_day_start(today)
    )
    if not platform:
        from apps.organizations.models import Membership

        members = Membership.objects.filter(organization_id=org_id).values('user_id')
        resets = resets.filter(user_id__in=members)
    password_resets = resets.count()
    return {
        'date': today,
        'deletes': deletes,
        'role_changes': role_changes,
        'password_resets': password_resets,
        'payment_state_changes': payment_changes,
        'total': deletes + role_changes + password_resets + payment_changes,
    }


def csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR prefixes)."""
    text = '' if value is None else str(value)
    if text and text[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + text
    return text


def to_csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['when', 'user', 'action', 'model', 'object', 'object_id', 'changes', 'ip'])
    for r in rows:
        changes = '; '.join(f'{c["field"]}: {c["old"]} -> {c["new"]}' for c in r['changes'])
        writer.writerow(
            [
                csv_safe(r['when']),
                csv_safe(r['user']['username'] if r['user'] else ''),
                csv_safe(r['action']),
                csv_safe(r['model']),
                csv_safe(r['object']),
                csv_safe(r['object_id']),
                csv_safe(changes),
                csv_safe(r['ip']),
            ]
        )
    return b'\xef\xbb\xbf' + buf.getvalue().encode('utf-8')
