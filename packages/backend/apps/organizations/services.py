"""Platform organization: the tenant that holds every B2C user.

B2C users (register / Telegram / Google sign-ups) are STUDENT members of one built-in
organization (``org_type=platform``, slug from ``settings.PLATFORM_ORG_SLUG``), so the tenant
middleware and RLS treat them exactly like any other tenant member.

Decisions:
  * The platform membership is created with ``is_primary=False`` and
    ``CustomUser.primary_membership`` ranks platform memberships LAST. A user who joins a real
    organization therefore gets it as primary / ``current_org`` automatically, and falls back to
    the platform org when they leave it.
  * The platform membership is KEPT when a user joins a real organization, so the B2C history
    (attempts, mistake notebook, wallet) stays reachable. ``user_type`` still reports ``b2c``
    only while the platform org is the user's primary organization.
  * No cache: this runs once per sign-up (a single indexed ``slug`` lookup); the request hot path
    only uses the Redis-backed membership cache.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    SYSTEM_ROLES,
    JoinedVia,
    Membership,
    MembershipStatus,
    Organization,
    OrgRole,
    OrgStatus,
    OrgType,
)

STUDENT_ROLE = 'student'


def platform_org_slug() -> str:
    return getattr(settings, 'PLATFORM_ORG_SLUG', 'platform')


def get_platform_org() -> Organization:
    """The platform organization, created (with its system roles) when missing."""
    slug = platform_org_slug()
    org = Organization.objects.filter(slug=slug).first()
    if org is None:
        org, _ = Organization.objects.get_or_create(
            slug=slug,
            defaults={
                'name': settings.PROJECT_BRAND_NAME,
                'org_type': OrgType.PLATFORM,
                'status': OrgStatus.ACTIVE,
                # B2C users keep the multi-device default, not the org-wide single device lock.
                'single_device_policy': False,
            },
        )
    return org


def is_platform_org(org) -> bool:
    return org is not None and org.org_type == OrgType.PLATFORM


def _student_role(org: Organization) -> OrgRole:
    data = SYSTEM_ROLES[STUDENT_ROLE]
    role, _ = OrgRole.objects.get_or_create(
        organization=org,
        name=STUDENT_ROLE,
        defaults={
            'display_name': data['display_name'],
            'permissions': data['permissions'],
            'is_system_role': True,
        },
    )
    return role


def ensure_platform_membership(user) -> Membership | None:
    """Give a B2C user an ACTIVE STUDENT membership in the platform org (idempotent).

    Staff/superusers are skipped (they run unscoped). An existing membership row is never
    modified, so a blocked/left platform membership stays blocked.
    """
    if user.is_staff or user.is_superuser:
        return None
    with transaction.atomic():
        org = get_platform_org()
        membership, _ = Membership.objects.get_or_create(
            user=user,
            organization=org,
            defaults={
                'role': _student_role(org),
                'status': MembershipStatus.ACTIVE,
                'joined_via': JoinedVia.AUTO,
                'is_primary': False,
                'joined_at': timezone.now(),
            },
        )
    return membership


def drop_auto_platform_membership(user) -> int:
    """Remove the auto-created platform membership (used when a user is promoted to staff)."""
    deleted, _ = Membership.objects.filter(
        user=user, organization__org_type=OrgType.PLATFORM, joined_via=JoinedVia.AUTO
    ).delete()
    return deleted
