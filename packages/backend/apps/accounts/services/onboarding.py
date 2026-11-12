"""
Onboarding questions asked before the user's first RESULT (not at signup).

Required: exam goals (>=1, exactly one primary), DTM direction or subject pair (only for
goals flagged ``requires_direction``), test language, target score or grade, school
graduation YEAR, region AND district, full birth date.
Optional: exam date, existing certificates, parent Telegram, organization invite code
(with explicit consent), acquisition answer.
"""

from __future__ import annotations

import re
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    CustomUser,
    District,
    DTMDirection,
    ExamCertificate,
    ExamGoal,
    OnboardingProfile,
    Region,
    TestLanguage,
)

TARGET_GRADES = ('A+', 'A', 'B+', 'B', 'C+', 'C')
ACQUISITION_ANSWERS = ('instagram', 'telegram', 'youtube', 'friend', 'school', 'search', 'other')
ACQUISITION_KEYS = (
    'utm_source',
    'utm_medium',
    'utm_campaign',
    'utm_content',
    'utm_term',
    'start_param',
    'referrer',
)
_PARENT_TG = re.compile(
    r'^(?:https?://)?(?:t\.me/|telegram\.me/|@)?([A-Za-z][A-Za-z0-9_]{4,31})/?$'
)
MAX_TARGET_SCORE = 189


def get_profile(user: CustomUser) -> OnboardingProfile:
    profile, _ = OnboardingProfile.objects.get_or_create(user=user)
    return profile


# ── Completeness ────────────────────────────────────────────


def missing_fields(user: CustomUser) -> list[str]:
    """Required onboarding fields still unanswered (empty list = complete)."""
    p = get_profile(user)
    missing = []
    goals = list(p.goals.all())
    if not goals:
        missing.append('goals')
    if not p.primary_goal_id or p.primary_goal_id not in {g.pk for g in goals}:
        missing.append('primary_goal')
    if any(g.requires_direction for g in goals) and not (
        p.dtm_direction_id or (p.subject_1 and p.subject_2)
    ):
        missing.append('dtm_direction_or_subjects')
    if not p.test_language:
        missing.append('test_language')
    if not (user.target_score or p.target_grade):
        missing.append('target')
    if not p.graduation_year:
        missing.append('graduation_year')
    if not user.region_id:
        missing.append('region')
    if not user.district_id:
        missing.append('district')
    if not p.birth_date:
        missing.append('birth_date')
    return missing


def is_complete(user: CustomUser) -> bool:
    return not missing_fields(user)


def state(user: CustomUser) -> dict:
    p = get_profile(user)
    missing = missing_fields(user)
    return {
        'is_complete': not missing,
        'missing': missing,
        'goals': [g.code for g in p.goals.all()],
        'primary_goal': p.primary_goal.code if p.primary_goal_id else None,
        'dtm_direction': p.dtm_direction.code if p.dtm_direction_id else None,
        'subject_1': p.subject_1 or None,
        'subject_2': p.subject_2 or None,
        'test_language': p.test_language or None,
        'target_score': user.target_score,
        'target_grade': p.target_grade or None,
        'exam_date': p.exam_date,
        'graduation_year': p.graduation_year,
        'region': user.region.code if user.region_id else None,
        'district': user.district_id,
        'birth_date': p.birth_date,
        'parent_telegram': p.parent_telegram or None,
        'acquisition_answer': p.acquisition_answer or None,
        'acquisition_note': p.acquisition_note or None,
        'certificates': [
            {
                'id': c.pk,
                'cert_type': c.cert_type,
                'subject': c.subject or None,
                'level': c.level,
                'date': c.date,
            }
            for c in user.external_certificates.all()
        ],
        'org_consents': [
            {
                'organization_id': str(c.organization_id),
                'organization_name': c.organization.name,
                'consented_at': c.consented_at,
            }
            for c in user.org_consents.filter(revoked_at__isnull=True).select_related(
                'organization'
            )
        ],
    }


# ── Validation + save ───────────────────────────────────────


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def apply(user: CustomUser, data: dict) -> dict[str, list[tuple[str, str]]]:
    """Validate and save the supplied fields (partial update). Returns errors; empty = saved."""
    errors: dict[str, list[tuple[str, str]]] = {}
    p = get_profile(user)
    today = timezone.localdate()

    def err(field, code, msg):
        errors.setdefault(field, []).append((code, msg))

    profile_updates: dict = {}
    user_updates: dict = {}
    goals_obj: list[ExamGoal] | None = None
    certs: list[dict] | None = None

    if 'goals' in data:
        codes = data['goals']
        if not isinstance(codes, list) or not codes or len(codes) > 10:
            err('goals', 'invalid', 'Pick between 1 and 10 goals.')
        else:
            found = list(ExamGoal.objects.filter(code__in=codes, is_active=True))
            if len(found) != len(set(codes)):
                err('goals', 'unknown_goal', 'Unknown or inactive goal.')
            else:
                goals_obj = found

    effective_goals = goals_obj if goals_obj is not None else list(p.goals.all())
    if 'primary_goal' in data or goals_obj is not None:
        code = data.get('primary_goal')
        if code is None and goals_obj is not None and len(goals_obj) == 1:
            code = goals_obj[0].code
        if code is None:
            if 'primary_goal' in data:
                err('primary_goal', 'invalid', 'Choose a primary goal.')
            elif goals_obj is not None and p.primary_goal_id not in {g.pk for g in goals_obj}:
                err('primary_goal', 'required', 'Choose a primary goal.')
        else:
            match = next((g for g in effective_goals if g.code == code), None)
            if match is None:
                err('primary_goal', 'not_in_goals', 'The primary goal must be one of the goals.')
            else:
                profile_updates['primary_goal'] = match

    if 'dtm_direction' in data:
        code = data['dtm_direction']
        if code in (None, ''):
            profile_updates['dtm_direction'] = None
        else:
            direction = DTMDirection.objects.filter(code=code, is_active=True).first()
            if direction is None:
                err('dtm_direction', 'unknown_direction', 'Unknown direction.')
            else:
                profile_updates['dtm_direction'] = direction
                profile_updates['subject_1'] = direction.subject_1
                profile_updates['subject_2'] = direction.subject_2
    for key in ('subject_1', 'subject_2'):
        if key in data and 'dtm_direction' not in profile_updates:
            val = data[key]
            if not isinstance(val, str) or len(val.strip()) > 60:
                err(key, 'invalid', 'Invalid subject.')
            else:
                profile_updates[key] = val.strip()
    if ('subject_1' in data or 'subject_2' in data) and 'dtm_direction' not in data:
        # a hand-picked pair replaces any earlier direction
        profile_updates['dtm_direction'] = None

    if 'test_language' in data:
        if data['test_language'] not in TestLanguage.values:
            err('test_language', 'invalid_choice', 'Choose uz, ru or kaa.')
        else:
            profile_updates['test_language'] = data['test_language']

    if 'target_score' in data:
        val = data['target_score']
        if val is None:
            user_updates['target_score'] = None
        elif isinstance(val, bool) or not isinstance(val, int) or not 1 <= val <= MAX_TARGET_SCORE:
            err('target_score', 'out_of_range', f'Between 1 and {MAX_TARGET_SCORE}.')
        else:
            user_updates['target_score'] = val
    if 'target_grade' in data:
        val = data['target_grade']
        if val in (None, ''):
            profile_updates['target_grade'] = ''
        elif val not in TARGET_GRADES:
            err('target_grade', 'invalid_choice', f'One of {", ".join(TARGET_GRADES)}.')
        else:
            profile_updates['target_grade'] = val

    if 'exam_date' in data:
        if data['exam_date'] in (None, ''):
            profile_updates['exam_date'] = None
        else:
            d = _parse_date(data['exam_date'])
            if d is None or d < today or d > today.replace(year=today.year + 3):
                err('exam_date', 'invalid_date', 'Pick a date in the next 3 years.')
            else:
                profile_updates['exam_date'] = d

    if 'graduation_year' in data:
        val = data['graduation_year']
        if (
            isinstance(val, bool)
            or not isinstance(val, int)
            or not (today.year - 60 <= val <= today.year + 8)
        ):
            err('graduation_year', 'out_of_range', 'Enter the school graduation year.')
        else:
            profile_updates['graduation_year'] = val

    if 'birth_date' in data:
        d = _parse_date(data['birth_date'])
        age = None if d is None else (today - d).days / 365.25
        if d is None or not 10 <= age <= 80:
            err('birth_date', 'invalid_date', 'Enter your full birth date (age 10-80).')
        else:
            profile_updates['birth_date'] = d
            user_updates['birth_year'] = d.year

    # Region + district: both required together; the district must belong to the region.
    if 'region' in data or 'district' in data:
        region = user.region
        if 'region' in data:
            region = (
                Region.objects.filter(code=data['region']).first()
                if isinstance(data['region'], int)
                else None
            )
            if region is None:
                err('region', 'unknown_region', 'Unknown region.')
        district = None
        if 'district' in data and region is not None:
            district = (
                District.objects.filter(pk=data['district'], region=region).first()
                if isinstance(data['district'], int) and not isinstance(data['district'], bool)
                else None
            )
            if district is None:
                err('district', 'district_not_in_region', 'The district is not in this region.')
        elif 'region' in data and region is not None and 'district' not in data:
            err('district', 'required', 'Choose the district as well.')
        if 'region' not in errors and 'district' not in errors and region is not None:
            user_updates['region'] = region
            user_updates['district'] = district

    if 'parent_telegram' in data:
        val = data['parent_telegram']
        if val in (None, ''):
            profile_updates['parent_telegram'] = ''
        else:
            m = _PARENT_TG.match(str(val).strip())
            if not m:
                err('parent_telegram', 'invalid', 'Enter a Telegram username or t.me link.')
            else:
                profile_updates['parent_telegram'] = '@' + m.group(1)

    if 'acquisition_answer' in data:
        val = data['acquisition_answer']
        if val in (None, ''):
            profile_updates['acquisition_answer'] = ''
        elif val not in ACQUISITION_ANSWERS:
            err('acquisition_answer', 'invalid_choice', 'Unknown option.')
        else:
            profile_updates['acquisition_answer'] = val
    if 'acquisition_note' in data:
        val = data['acquisition_note']
        if not isinstance(val, str) or len(val) > 200:
            err('acquisition_note', 'invalid', 'At most 200 characters.')
        else:
            profile_updates['acquisition_note'] = val.strip()

    if 'certificates' in data:
        raw = data['certificates']
        parsed = []
        if not isinstance(raw, list) or len(raw) > 20:
            err('certificates', 'invalid', 'At most 20 certificates.')
        else:
            for i, c in enumerate(raw):
                d = _parse_date(c.get('date')) if isinstance(c, dict) else None
                level = str(c.get('level', '')).strip() if isinstance(c, dict) else ''
                ctype = c.get('cert_type') if isinstance(c, dict) else None
                if (
                    d is None
                    or d > today
                    or not 1 <= len(level) <= 20
                    or ctype not in ExamCertificate.CertType.values
                ):
                    err('certificates', 'invalid_item', f'Certificate #{i + 1} is not valid.')
                    continue
                parsed.append(
                    {
                        'cert_type': ctype,
                        'subject': str(c.get('subject', '')).strip()[:60],
                        'level': level,
                        'date': d,
                    }
                )
            if 'certificates' not in errors:
                certs = parsed

    if errors:
        return errors

    with transaction.atomic():
        if goals_obj is not None:
            p.goals.set(goals_obj)
            if p.primary_goal_id and p.primary_goal_id not in {g.pk for g in goals_obj}:
                profile_updates.setdefault('primary_goal', None)
        for k, v in profile_updates.items():
            setattr(p, k, v)
        p.save()
        if user_updates:
            for k, v in user_updates.items():
                setattr(user, k, v)
            user.save(update_fields=list(user_updates))
        if certs is not None:
            ExamCertificate.objects.filter(user=user).delete()
            ExamCertificate.objects.bulk_create([ExamCertificate(user=user, **c) for c in certs])
        if is_complete(user) and p.completed_at is None:
            p.completed_at = timezone.now()
            p.save(update_fields=['completed_at'])
    return {}


# ── Acquisition capture ─────────────────────────────────────


def capture_acquisition(user: CustomUser, raw) -> None:
    """Store UTM / start-param attribution once (first touch wins). Junk keys are dropped."""
    if not isinstance(raw, dict):
        return
    clean = {}
    for key in ACQUISITION_KEYS:
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            clean[key] = val.strip()[:200]
    if not clean:
        return
    p = get_profile(user)
    if not p.acquisition:
        p.acquisition = clean
        p.save(update_fields=['acquisition'])


# ── Joining an organization by invite code ──────────────────


class InviteError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def join_with_invite(user: CustomUser, code: str, consent: bool):
    """Accept an org invite (UUID token) with explicit consent that the org sees results."""
    from uuid import UUID

    from apps.accounts.models import OrgResultsConsent
    from apps.organizations.models import JoinedVia, Membership, MembershipStatus, OrgInvite

    if consent is not True:
        raise InviteError(
            'consent_required', 'You must agree that the organization sees your results.'
        )
    try:
        token = UUID(str(code).strip())
    except ValueError as e:
        raise InviteError('invalid_invite', 'The invite code is not valid.') from e
    invite = OrgInvite.objects.select_related('organization', 'role').filter(token=token).first()
    if (
        invite is None
        or not invite.is_valid
        or not (invite.organization.is_active or invite.organization.is_trial)
    ):
        raise InviteError('invalid_invite', 'The invite code is not valid.')

    org = invite.organization
    with transaction.atomic():
        membership = Membership.objects.filter(user=user, organization=org).first()
        if membership is None:
            has_primary = Membership.objects.filter(
                user=user, status=MembershipStatus.ACTIVE, is_primary=True
            ).exists()
            membership = Membership.objects.create(
                user=user,
                organization=org,
                role=invite.role,
                status=MembershipStatus.ACTIVE,
                joined_via=JoinedVia.INVITE,
                is_primary=not has_primary,
                invited_by=invite.created_by,
                joined_at=timezone.now(),
            )
            if not invite.use():
                raise InviteError('invalid_invite', 'The invite code is not valid.')
        elif membership.status != MembershipStatus.ACTIVE:
            raise InviteError('invalid_invite', 'The invite code is not valid.')
        OrgResultsConsent.objects.update_or_create(
            user=user,
            organization=org,
            defaults={'consented_at': timezone.now(), 'revoked_at': None},
        )
    return org
