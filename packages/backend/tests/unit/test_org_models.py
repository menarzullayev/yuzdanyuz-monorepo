"""
Unit tests for apps/organizations/models.py
Focus: Organization, OrgRole, Membership, OrgInvite
"""

import pytest
from django.utils import timezone
from datetime import timedelta
from apps.organizations.models import (
    Organization, OrgRole, OrgStatus, Membership, MembershipStatus, OrgInvite
)


@pytest.mark.unit
class TestOrganizationModel:
    """Organization model tests."""

    def test_org_created(self, org):
        """Organization creation."""
        assert org.name == 'Test Org'
        assert org.slug == 'test-org'
        assert org.status == OrgStatus.PENDING

    def test_system_roles_created_on_org_save(self, db):
        """Org creation → auto-creates 5 system roles (owner, manager, teacher, student, observer)."""
        org = Organization.objects.create(name='New Org', slug='new-org')

        roles = OrgRole.objects.filter(organization=org)
        assert roles.count() == 5

        role_names = set(roles.values_list('name', flat=True))
        assert role_names == {'owner', 'manager', 'teacher', 'student', 'observer'}

    def test_org_effective_settings_merge(self, org):
        """effective_settings merges DEFAULT_ORG_SETTINGS + org.settings."""
        org.settings = {'max_students': 100}
        org.save()

        settings = org.effective_settings
        assert settings['max_students'] == 100  # Custom override
        assert settings['ai_enabled'] is True  # Default
        assert settings['proctoring'] == 'basic'  # Default

    def test_org_effective_settings_defaults(self, org):
        """Empty settings → full defaults."""
        org.settings = {}
        org.save()

        settings = org.effective_settings
        assert 'max_students' in settings
        assert 'ai_enabled' in settings

    def test_org_is_active(self, db):
        """is_active property."""
        org = Organization.objects.create(
            name='Active', slug='active', status=OrgStatus.ACTIVE
        )
        assert org.is_active is True

        org.status = OrgStatus.SUSPENDED
        assert org.is_active is False

    def test_org_is_trial(self, db):
        """is_trial property."""
        org = Organization.objects.create(
            name='Trial', slug='trial', status=OrgStatus.TRIAL
        )
        assert org.is_trial is True

    def test_org_trial_expired_no_date(self, org):
        """trial_expires_at=None → trial_expired=False."""
        org.trial_ends_at = None
        assert org.trial_expired is False

    def test_org_trial_expired_past_date(self, org):
        """trial_ends_at in past → trial_expired=True."""
        org.trial_ends_at = timezone.now() - timedelta(days=1)
        assert org.trial_expired is True

    def test_org_trial_expired_future_date(self, org):
        """trial_ends_at in future → trial_expired=False."""
        org.trial_ends_at = timezone.now() + timedelta(days=1)
        assert org.trial_expired is False

    def test_get_setting(self, org):
        """get_setting(key) helper."""
        org.settings = {'max_students': 50}
        org.save()

        assert org.get_setting('max_students') == 50
        assert org.get_setting('ai_enabled') is True  # Default


@pytest.mark.unit
class TestOrgRolePermissions:
    """OrgRole.has_permission() tests — CRITICAL for RBAC."""

    def test_has_permission_exact_match(self, org):
        """'exam:create' in permissions → has_permission('exam:create')=True."""
        role = OrgRole.objects.get(organization=org, name='teacher')
        role.permissions = ['exam:create', 'exam:view']
        role.save()

        assert role.has_permission('exam:create') is True
        assert role.has_permission('exam:view') is True

    def test_has_permission_star_wildcard(self, org):
        """'*' in permissions → has_permission(anything)=True."""
        role = OrgRole.objects.get(organization=org, name='owner')
        role.permissions = ['*']
        role.save()

        assert role.has_permission('exam:create') is True
        assert role.has_permission('billing:delete') is True
        assert role.has_permission('any:thing') is True

    def test_has_permission_resource_wildcard(self, org):
        """'exam:*' in permissions → has_permission('exam:create')=True."""
        role = OrgRole.objects.get(organization=org, name='manager')
        role.permissions = ['exam:*', 'catalog:view']
        role.save()

        # exam:* covers all exam actions
        assert role.has_permission('exam:create') is True
        assert role.has_permission('exam:edit') is True
        assert role.has_permission('exam:delete') is True

    def test_has_permission_resource_wildcard_no_cross(self, org):
        """'exam:*' does NOT cover 'billing:view'."""
        role = OrgRole.objects.get(organization=org, name='teacher')
        role.permissions = ['exam:*']
        role.save()

        assert role.has_permission('exam:create') is True
        assert role.has_permission('billing:view') is False

    def test_has_permission_false_when_missing(self, org):
        """Permission not in list → False."""
        role = OrgRole.objects.get(organization=org, name='student')
        role.permissions = ['exam:take']
        role.save()

        assert role.has_permission('exam:take') is True
        assert role.has_permission('exam:create') is False

    def test_has_permission_empty_list(self, org):
        """Empty permissions list → always False."""
        role = OrgRole.objects.create(
            organization=org, name='guest', display_name='Guest', permissions=[]
        )
        assert role.has_permission('anything:here') is False


@pytest.mark.unit
class TestMembership:
    """Membership model tests."""

    def test_membership_has_permission_delegates_to_role(self, member):
        """Membership.has_permission() → OrgRole.has_permission()."""
        member.role.permissions = ['exam:take']
        member.role.save()

        assert member.has_permission('exam:take') is True
        assert member.has_permission('exam:create') is False

    def test_membership_activate(self, db, user, org):
        """activate() → status=ACTIVE, joined_at set."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role,
            status=MembershipStatus.INVITED
        )

        assert m.joined_at is None
        m.activate()
        assert m.status == MembershipStatus.ACTIVE
        assert m.joined_at is not None

    def test_membership_unique_together(self, db, user, org):
        """One user can't have 2 memberships in same org."""
        role = OrgRole.objects.get(organization=org, name='student')

        Membership.objects.create(user=user, organization=org, role=role)

        with pytest.raises(Exception):  # IntegrityError
            Membership.objects.create(user=user, organization=org, role=role)


@pytest.mark.unit
class TestOrgInvite:
    """OrgInvite model tests."""

    def test_orginvite_is_valid_true(self, db, org, user):
        """Fresh invite → is_valid=True."""
        role = OrgRole.objects.get(organization=org, name='student')
        invite = OrgInvite.objects.create(
            organization=org, role=role,
            created_by=user
        )
        assert invite.is_valid is True

    def test_orginvite_is_valid_inactive(self, db, org, user):
        """is_active=False → is_valid=False."""
        role = OrgRole.objects.get(organization=org, name='student')
        invite = OrgInvite.objects.create(
            organization=org, role=role, is_active=False,
            created_by=user
        )
        assert invite.is_valid is False

    def test_orginvite_is_valid_expired(self, db, org, user):
        """expires_at in past → is_valid=False."""
        role = OrgRole.objects.get(organization=org, name='student')
        invite = OrgInvite.objects.create(
            organization=org, role=role,
            expires_at=timezone.now() - timedelta(days=1),
            created_by=user
        )
        assert invite.is_valid is False

    def test_orginvite_is_valid_max_uses_exceeded(self, db, org, user):
        """used_count >= max_uses → is_valid=False."""
        role = OrgRole.objects.get(organization=org, name='student')
        invite = OrgInvite.objects.create(
            organization=org, role=role, max_uses=2, used_count=2,
            created_by=user
        )
        assert invite.is_valid is False

    def test_orginvite_use_increments_count(self, db, org, user):
        """use() → used_count += 1."""
        role = OrgRole.objects.get(organization=org, name='student')
        invite = OrgInvite.objects.create(
            organization=org, role=role,
            created_by=user
        )

        assert invite.used_count == 0
        invite.use()
        invite.refresh_from_db()
        assert invite.used_count == 1
