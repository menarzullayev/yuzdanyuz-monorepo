"""Shared permission helpers for the arena, rating and monitoring APIs."""

from rest_framework.permissions import BasePermission

# Org roles that count as "org admin" (owner via '*', manager via members:manage).
ORG_ADMIN_PERMS = ('arena:approve', 'members:manage')


def is_platform_staff(user) -> bool:
    return bool(user.is_authenticated and (user.is_superuser or user.is_staff))


def is_org_admin(user, org) -> bool:
    if is_platform_staff(user):
        return True
    if org is None:
        return False
    return any(user.has_org_permission(org, p) for p in ORG_ADMIN_PERMS)


def has_org_perm(user, org, perm: str) -> bool:
    if is_platform_staff(user):
        return True
    return org is not None and user.has_org_permission(org, perm)


class HasOrgPermission(BasePermission):
    """Subclass and set `perm`. Platform staff always pass."""

    perm = ''

    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated:
            return False
        return has_org_perm(user, getattr(request, 'org', None), self.perm)


class CanEditExams(HasOrgPermission):
    perm = 'exam:edit'


class CanViewExams(HasOrgPermission):
    perm = 'exam:view'
