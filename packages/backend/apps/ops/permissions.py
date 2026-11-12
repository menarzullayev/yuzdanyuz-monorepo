from rest_framework.permissions import BasePermission


def is_platform_staff(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


class IsPlatformStaff(BasePermission):
    message = 'Platform staff only.'

    def has_permission(self, request, view):
        return is_platform_staff(request.user)


def admin_org_ids(user) -> list:
    """Organizations where ``user`` holds members:manage (owner/manager style roles)."""
    from apps.organizations.models import Membership, MembershipStatus

    ids = []
    for m in Membership.objects.filter(user=user, status=MembershipStatus.ACTIVE).select_related(
        'role'
    ):
        if m.has_permission('members:manage'):
            ids.append(m.organization_id)
    return ids


class IsStaffOrOrgAdmin(BasePermission):
    message = 'Platform staff or organization admin only.'

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return is_platform_staff(user) or bool(admin_org_ids(user))
