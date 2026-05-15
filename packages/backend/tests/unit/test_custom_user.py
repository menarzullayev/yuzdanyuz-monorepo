"""
Unit tests for apps/accounts/models.py — CustomUser
Focus: user_type, has_org_permission, display_name, profile
"""

import pytest
from django.contrib.auth import get_user_model
from apps.organizations.models import MembershipStatus

User = get_user_model()


@pytest.mark.unit
class TestUserType:
    """CustomUser.user_type property tests — CRITICAL for RBAC."""

    def test_user_type_platform_admin(self, db):
        """is_superuser=True → user_type='platform_admin'."""
        user = User.objects.create_superuser(
            username='admin', email='admin@test.com', password='pass'
        )
        assert user.user_type == 'platform_admin'

    def test_user_type_platform_staff(self, db):
        """is_staff=True (not superuser) → user_type='platform_staff'."""
        user = User.objects.create_user(
            username='staff', email='staff@test.com', password='pass'
        )
        user.is_staff = True
        user.save()
        assert user.user_type == 'platform_staff'

    def test_user_type_b2c(self, user):
        """No active membership → user_type='b2c'."""
        assert user.user_type == 'b2c'

    def test_user_type_from_membership_role(self, member):
        """Active membership → user_type=role.name."""
        assert member.user.user_type == 'student'

        # Change role
        from apps.organizations.models import OrgRole
        member.role = OrgRole.objects.get(organization=member.organization, name='teacher')
        member.save()
        assert member.user.user_type == 'teacher'

    def test_user_type_primary_membership_priority(self, db, user, org, org2):
        """is_primary=True membership has priority."""
        from apps.organizations.models import Membership, OrgRole

        # Secondary membership (student in org)
        role1 = OrgRole.objects.get(organization=org, name='student')
        m1 = Membership.objects.create(
            user=user, organization=org, role=role1,
            status=MembershipStatus.ACTIVE, is_primary=False
        )

        # Primary membership (teacher in org2)
        role2 = OrgRole.objects.get(organization=org2, name='teacher')
        m2 = Membership.objects.create(
            user=user, organization=org2, role=role2,
            status=MembershipStatus.ACTIVE, is_primary=True
        )
        m2.activate()

        # Primary should win
        assert user.user_type == 'teacher'

    def test_user_type_suspended_membership_ignored(self, db, user, org):
        """Suspended membership doesn't count → b2c."""
        from apps.organizations.models import Membership, OrgRole

        role = OrgRole.objects.get(organization=org, name='student')
        Membership.objects.create(
            user=user, organization=org, role=role,
            status=MembershipStatus.SUSPENDED
        )

        assert user.user_type == 'b2c'


@pytest.mark.unit
class TestDisplayName:
    """CustomUser.display_name property tests."""

    def test_display_name_full_name(self, db):
        """first_name + last_name → full name."""
        user = User.objects.create_user(
            username='john', email='john@test.com', password='pass',
            first_name='John', last_name='Doe'
        )
        assert user.display_name == 'John Doe'

    def test_display_name_fallback_telegram(self, db):
        """No full name → telegram_username."""
        user = User.objects.create_user(
            username='john', email='john@test.com', password='pass',
            telegram_username='@johndoe'
        )
        assert user.display_name == '@johndoe'

    def test_display_name_fallback_phone(self, db):
        """No name or telegram → phone_number."""
        user = User.objects.create_user(
            username='john', email='john@test.com', password='pass',
            phone_number='+998901234567'
        )
        assert user.display_name == '+998901234567'

    def test_display_name_fallback_username(self, db):
        """Fallback to username if nothing else."""
        user = User.objects.create_user(
            username='john', email='john@test.com', password='pass'
        )
        assert user.display_name == 'john'


@pytest.mark.unit
class TestPrimaryOrganization:
    """CustomUser.primary_organization property."""

    def test_primary_organization(self, member):
        """primary_membership.organization → primary_organization."""
        assert member.user.primary_organization == member.organization

    def test_primary_organization_none(self, user):
        """No membership → None."""
        assert user.primary_organization is None

    def test_primary_organization_is_primary_flag(self, db, user, org, org2):
        """is_primary flag determines primary."""
        from apps.organizations.models import Membership, OrgRole

        role1 = OrgRole.objects.get(organization=org, name='student')
        m1 = Membership.objects.create(
            user=user, organization=org, role=role1,
            status=MembershipStatus.ACTIVE, is_primary=True
        )
        m1.activate()

        role2 = OrgRole.objects.get(organization=org2, name='teacher')
        m2 = Membership.objects.create(
            user=user, organization=org2, role=role2,
            status=MembershipStatus.ACTIVE, is_primary=False
        )
        m2.activate()

        assert user.primary_organization == org


@pytest.mark.unit
class TestHasOrgPermission:
    """CustomUser.has_org_permission(org, perm) tests — CRITICAL for RBAC."""

    def test_has_org_permission_superuser_bypass(self, superuser, org):
        """Superuser → always True regardless of membership."""
        assert superuser.has_org_permission(org, 'anything:here') is True

    def test_has_org_permission_no_membership(self, user, org):
        """User not in org → False."""
        assert user.has_org_permission(org, 'exam:create') is False

    def test_has_org_permission_via_role(self, member):
        """Membership role has permission → True."""
        member.role.permissions = ['exam:take']
        member.role.save()

        assert member.user.has_org_permission(member.organization, 'exam:take') is True

    def test_has_org_permission_wildcard(self, owner_member):
        """Owner role with '*' → any permission True."""
        assert owner_member.user.has_org_permission(
            owner_member.organization, 'anything:goes'
        ) is True

    def test_has_org_permission_suspended_member(self, suspended_member):
        """Suspended membership → False."""
        assert suspended_member.user.has_org_permission(
            suspended_member.organization, 'exam:take'
        ) is False


@pytest.mark.unit
class TestProfileComplete:
    """CustomUser.is_profile_complete property."""

    def test_is_profile_complete_true(self, db):
        """Name + contact → True."""
        user = User.objects.create_user(
            username='complete', email='complete@test.com', password='pass',
            first_name='John', phone_number='+998901234567'
        )
        assert user.is_profile_complete is True

    def test_is_profile_complete_no_name(self, db):
        """No name → False."""
        user = User.objects.create_user(
            username='notype', email='notype@test.com', password='pass',
            phone_number='+998901234567'
        )
        assert user.is_profile_complete is False

    def test_is_profile_complete_no_contact(self, db):
        """Name but no contact (phone/email/telegram) → False."""
        user = User.objects.create_user(
            username='nocontact', password='pass',
            first_name='John'
        )
        # No email, phone_number, or telegram_id set
        user.email = ''  # Clear the auto-set email
        user.save()
        assert user.is_profile_complete is False


@pytest.mark.unit
class TestIsB2C:
    """CustomUser.is_b2c property."""

    def test_is_b2c_true(self, user):
        """user_type='b2c' → is_b2c=True."""
        assert user.is_b2c is True

    def test_is_b2c_false_with_membership(self, member):
        """With membership → is_b2c=False."""
        assert member.user.is_b2c is False
