"""Group membership services: eligibility, bulk add, CSV parsing, access scoping."""

import csv
import io
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.functions import Lower

from .group_models import Group, GroupMember, GroupRole
from .models import Membership, MembershipStatus

MAX_BULK_ROWS = 500
MAX_CSV_BYTES = 512 * 1024
USERNAME_RE = re.compile(r'^[\w.@+-]{1,150}$')

# Permission codes (see SYSTEM_ROLES in models.py)
PERM_VIEW_ALL = 'groups:view'
PERM_VIEW_OWN = 'groups:view_own'
PERM_LESSON_VIEW_OWN = 'lessons:view_own'
PERM_GROUP_EDIT = 'groups:edit'


class CsvFileError(Exception):
    """File-level problem (encoding, size, missing header): nothing can be processed."""


# ── Access scoping ──────────────────────────────────────────────────────────


def visible_groups(user, org):
    """Groups the user may see: all org groups for wide readers (groups:view),
    otherwise only groups they belong to as student or teacher."""
    if user.has_org_permission(org, PERM_VIEW_ALL):
        return Group.objects.all()
    if user.has_org_permission(org, PERM_VIEW_OWN) or user.has_org_permission(
        org, PERM_LESSON_VIEW_OWN
    ):
        return Group.objects.filter(members__user=user)
    return Group.objects.none()


def is_group_teacher(user, group) -> bool:
    return GroupMember.objects.filter(group=group, user=user, role=GroupRole.TEACHER).exists()


def can_manage_lessons_of(user, org, group, action: str) -> bool:
    """lessons:<action> AND (teaches this group OR org-wide groups:edit)."""
    if not user.has_org_permission(org, f'lessons:{action}'):
        return False
    return user.has_org_permission(org, PERM_GROUP_EDIT) or is_group_teacher(user, group)


# ── Eligibility ─────────────────────────────────────────────────────────────


def _active_memberships(org, users):
    return {
        m.user_id: m
        for m in Membership.objects.filter(
            organization=org, status=MembershipStatus.ACTIVE, user__in=users
        ).select_related('role')
    }


def _check_member(membership, role) -> str | None:
    """Return an error message, or None when the membership may take this group role."""
    if membership is None:
        return 'user is not an active member of this organization'
    if role == GroupRole.TEACHER and not membership.role.has_permission('lessons:create'):
        return 'role of this member cannot teach (needs lessons:create)'
    return None


# ── Bulk add ────────────────────────────────────────────────────────────────


def normalize_usernames(raw) -> list[tuple[int, str]]:
    return [(i, str(u).strip()) for i, u in enumerate(raw, start=1)]


def add_members(group, entries, *, role=GroupRole.STUDENT, added_by=None, all_or_nothing=False):
    """Add users to ``group`` by username.

    ``entries``: list of (row_number, username). Every row is validated; the report
    lists each row as added / already_member / error. With ``all_or_nothing`` a single
    error rolls everything back (committed=False).
    """
    User = get_user_model()
    org = group.organization
    rows = []
    seen: dict[str, int] = {}

    wanted = {u.lower() for _, u in entries if USERNAME_RE.match(u)}
    users_by_name: dict[str, list] = {}
    for u in User.objects.annotate(_l=Lower('username')).filter(_l__in=wanted):
        users_by_name.setdefault(u.username.lower(), []).append(u)
    memberships = _active_memberships(org, [u for us in users_by_name.values() for u in us])
    existing = set(GroupMember.objects.filter(group=group).values_list('user_id', flat=True))

    to_create = []
    for row, username in entries:
        entry = {'row': row, 'username': username, 'status': 'error', 'error': None}
        rows.append(entry)
        key = username.lower()
        if not username:
            entry['error'] = 'username is empty'
        elif not USERNAME_RE.match(username):
            entry['error'] = 'invalid username format'
        elif key in seen:
            entry['error'] = f'duplicate of row {seen[key]}'
        else:
            seen[key] = row
            matches = users_by_name.get(key, [])
            if not matches:
                entry['error'] = 'user not found'
            elif len(matches) > 1:
                entry['error'] = 'ambiguous username (differs only by case)'
            else:
                user = matches[0]
                err = _check_member(memberships.get(user.pk), role)
                if err:
                    entry['error'] = err
                elif user.pk in existing:
                    entry['status'] = 'already_member'
                else:
                    entry['status'] = 'added'
                    to_create.append(user)
        if entry['error'] is None:
            entry.pop('error')

    errors = sum(1 for r in rows if r['status'] == 'error')
    committed = not (all_or_nothing and errors)
    if committed and to_create:
        with transaction.atomic():
            GroupMember.objects.bulk_create(
                [
                    GroupMember(organization=org, group=group, user=u, role=role, added_by=added_by)
                    for u in to_create
                ],
                ignore_conflicts=True,
            )
    if not committed:
        for r in rows:
            if r['status'] == 'added':
                r['status'] = 'skipped'
    return {
        'committed': committed,
        'summary': {
            'total': len(rows),
            'added': sum(1 for r in rows if r['status'] == 'added'),
            'already_member': sum(1 for r in rows if r['status'] == 'already_member'),
            'errors': errors,
        },
        'rows': rows,
    }


# ── CSV ─────────────────────────────────────────────────────────────────────


def parse_username_csv(upload) -> list[tuple[int, str]]:
    """Parse an uploaded CSV with a ``username`` header into (line_number, username)."""
    if upload.size > MAX_CSV_BYTES:
        raise CsvFileError(f'file too large (max {MAX_CSV_BYTES // 1024} KB)')
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise CsvFileError('file must be UTF-8 encoded') from exc
    if not text.strip():
        raise CsvFileError('file is empty')
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    fields = [(f or '').strip().lower() for f in (reader.fieldnames or [])]
    if 'username' not in fields:
        raise CsvFileError("header row must contain a 'username' column")
    reader.fieldnames = fields
    entries = []
    for row in reader:
        username = (row.get('username') or '').strip()
        cells = [v if isinstance(v, str) else ''.join(v or []) for v in row.values()]
        if not any(c.strip() for c in cells):
            continue  # fully blank line
        entries.append((reader.line_num, username))
        if len(entries) > MAX_BULK_ROWS:
            raise CsvFileError(f'too many rows (max {MAX_BULK_ROWS})')
    if not entries:
        raise CsvFileError('no data rows found')
    return entries
