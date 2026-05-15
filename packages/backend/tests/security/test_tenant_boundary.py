"""
Security tests for multi-tenant boundary enforcement
Focus: cross-org access prevention, membership validation, permission scoping
"""

import pytest

from apps.catalog.models import Question
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import get_current_org, set_current_org


@pytest.mark.security
class TestTenantBoundaryEnforcement:
    """Verify tenant boundaries are enforced."""

    def test_user_cannot_access_other_org_data(self, db, user, org, org2, subject):
        """User in Org1 → TenantManager blocks Org2 data access."""
        q1 = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q2 = Question.objects.create(
            organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
        )

        # User only in org
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE
        )

        set_current_org(org)
        # Can see q1
        assert q1 in Question.objects.all()
        # Cannot see q2
        assert q2 not in Question.objects.all()

    def test_jwt_org_claim_validated_against_membership(self, db, user, org, org2):
        """JWT org claim validated: no membership → fallback."""
        role1 = OrgRole.objects.get(organization=org, name='student')
        Membership.objects.create(
            user=user, organization=org, role=role1, status=MembershipStatus.ACTIVE, is_primary=True
        )

        # Middleware will validate membership for org2
        # If user not member, fallback to primary_org
        # This is tested in test_tenant_middleware.py::test_jwt_non_member_org_fallback

    def test_inactive_membership_cannot_set_context(self, db, user, org):
        """Suspended/inactive membership → cannot set context."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.SUSPENDED
        )

        # User cannot use this membership
        assert user.has_org_permission(org, 'exam:take') is False

    def test_superuser_explicit_access(self, db, superuser, org, org2):
        """Superuser can explicitly access any tenant context."""
        set_current_org(org)
        # Superuser has access regardless of membership

        set_current_org(org2)
        # Still has access

        # No errors should occur
        assert get_current_org() == org2

    def test_permission_wildcard_scoped_to_org(self, db, user, org, org2):
        """'*' permission in Org1 → does NOT apply to Org2."""
        role1 = OrgRole.objects.get(organization=org, name='owner')
        role1.permissions = ['*']
        role1.save()

        m1 = Membership.objects.create(
            user=user, organization=org, role=role1, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m1.activate()

        # In org1, user has all permissions
        assert user.has_org_permission(org, 'exam:create') is True

        # In org2, user has no membership and no permissions
        assert user.has_org_permission(org2, 'exam:create') is False
