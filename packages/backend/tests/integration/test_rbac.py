"""
Integration tests for RBAC end-to-end
Focus: permission derivation across roles, cross-org denial, membership status
"""

import pytest

from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import set_current_org


@pytest.mark.integration
class TestRBACEndToEnd:
    """RBAC user type and permission resolution."""

    def test_owner_has_all_permissions(self, db, user, org):
        """Owner role with '*' → any permission True."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='owner')
        role.permissions = ['*']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        # user_type should be 'owner'
        assert user.user_type == 'owner'

        # Any permission should work
        assert user.has_org_permission(org, 'exam:create') is True
        assert user.has_org_permission(org, 'billing:delete') is True
        assert user.has_org_permission(org, 'user:invite') is True

    def test_student_restricted_permissions(self, db, user, org):
        """Student role limited → exam:create blocked."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='student')
        role.permissions = ['exam:take', 'catalog:view']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        # user_type should be 'student'
        assert user.user_type == 'student'

        # Allowed
        assert user.has_org_permission(org, 'exam:take') is True
        assert user.has_org_permission(org, 'catalog:view') is True

        # Denied
        assert user.has_org_permission(org, 'exam:create') is False

    def test_teacher_can_create_exam(self, db, user, org):
        """Teacher role → exam:create True."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='teacher')
        role.permissions = ['exam:create', 'exam:edit', 'exam:view', 'catalog:view']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        assert user.user_type == 'teacher'
        assert user.has_org_permission(org, 'exam:create') is True

    def test_resource_wildcard_in_manager(self, db, user, org):
        """Manager role with 'exam:*' → all exam actions True."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='manager')
        role.permissions = ['exam:*', 'user:view']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        # exam:* covers all exam actions
        assert user.has_org_permission(org, 'exam:create') is True
        assert user.has_org_permission(org, 'exam:edit') is True
        assert user.has_org_permission(org, 'exam:delete') is True
        assert user.has_org_permission(org, 'exam:view') is True

        # Other resources blocked
        assert user.has_org_permission(org, 'billing:view') is False

    def test_cross_org_permission_denied(self, db, user, org, org2):
        """User in Org1 → has_org_permission(Org2) = False."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='student')
        role.permissions = ['exam:take']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        # No membership in org2
        assert user.has_org_permission(org2, 'exam:take') is False

    def test_permission_with_suspended_membership(self, db, user, org):
        """Suspended membership → no permissions."""
        set_current_org(org)

        role = OrgRole.objects.get(organization=org, name='student')
        role.permissions = ['exam:take']
        role.save()

        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.SUSPENDED
        )

        # Suspended = no access
        assert user.has_org_permission(org, 'exam:take') is False
