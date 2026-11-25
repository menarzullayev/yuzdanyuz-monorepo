"""Audit B11: views ask for permission codes, not role-name lists.

Covers the three call sites that used ``role__name__in``: the analytics dashboard
(``analytics:view``), the business reports (``analytics:business``) and the org plan
purchase/cancel (``billing:manage``). A custom role is the case the old lists got
wrong: its name is not in any list, so only its codes may decide.
"""

import importlib

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model

from apps.analytics.views import _is_dashboard_user
from apps.commerce.views import _is_org_admin
from apps.organizations.models import Membership, MembershipStatus, OrgRole

User = get_user_model()


def _member(org, role_name, *, perms=None):
    if perms is not None:
        role = OrgRole.objects.create(
            organization=org, name=role_name, display_name=role_name, permissions=perms
        )
    else:
        role = OrgRole.objects.get(organization=org, name=role_name)
    u = User.objects.create_user(
        username=f'u-{role_name}', email=f'{role_name}@x.uz', password='pw12345!'
    )
    m = Membership.objects.create(
        user=u, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return u


def _has_business(user, org):
    from apps.analytics.business_views import BUSINESS_PERMISSION

    return user.has_org_permission(org, BUSINESS_PERMISSION)


@pytest.mark.integration
@pytest.mark.django_db
class TestDashboardAccess:
    @pytest.mark.parametrize('role', ['owner', 'manager', 'teacher', 'observer'])
    def test_analytics_view_roles_allowed(self, org, role):
        assert _is_dashboard_user(_member(org, role), org)

    def test_student_denied(self, org):
        assert not _is_dashboard_user(_member(org, 'student'), org)

    def test_custom_role_decided_by_codes(self, org):
        assert _is_dashboard_user(_member(org, 'analyst', perms=['analytics:view']), org)

    def test_custom_admin_named_role_without_code_denied(self, org):
        assert not _is_dashboard_user(_member(org, 'admin', perms=['exam:view']), org)

    def test_other_org_membership_does_not_count(self, org, org2):
        u = _member(org, 'owner')
        assert not _is_dashboard_user(u, org2)


@pytest.mark.integration
@pytest.mark.django_db
class TestBusinessReports:
    @pytest.mark.parametrize('role', ['owner', 'manager'])
    def test_owner_and_manager_allowed(self, org, role):
        assert _has_business(_member(org, role), org)

    @pytest.mark.parametrize('role', ['teacher', 'observer', 'student'])
    def test_other_system_roles_denied(self, org, role):
        assert not _has_business(_member(org, role), org)


@pytest.mark.integration
@pytest.mark.django_db
class TestBillingManage:
    def test_owner_allowed(self, org):
        assert _is_org_admin(_member(org, 'owner'), org)

    @pytest.mark.parametrize('role', ['manager', 'teacher', 'observer', 'student'])
    def test_non_owner_system_roles_denied(self, org, role):
        assert not _is_org_admin(_member(org, role), org)

    def test_custom_role_with_code_allowed(self, org):
        assert _is_org_admin(_member(org, 'bursar', perms=['billing:manage']), org)

    def test_admin_named_role_needs_the_code(self, org):
        assert not _is_org_admin(_member(org, 'admin', perms=['members:manage']), org)

    def test_platform_staff_allowed(self, org):
        staff = User.objects.create_user(
            username='staff', email='s@x.uz', password='pw12345!', is_staff=True
        )
        assert _is_org_admin(staff, org)


@pytest.mark.integration
@pytest.mark.django_db
class TestManagerBackfillMigration:
    def _migration(self):
        return importlib.import_module(
            'apps.organizations.migrations.0009_manager_business_analytics'
        )

    def test_adds_code_once_and_keeps_customisations(self, org):
        role = OrgRole.objects.get(organization=org, name='manager')
        role.permissions = ['exam:view', 'custom:thing']
        role.save(update_fields=['permissions'])

        mig = self._migration()
        mig.add_code(django_apps, None)
        mig.add_code(django_apps, None)

        role.refresh_from_db()
        assert role.permissions == ['exam:view', 'custom:thing', 'analytics:business']

    def test_reverse_removes_only_the_code(self, org):
        role = OrgRole.objects.get(organization=org, name='manager')
        mig = self._migration()
        mig.add_code(django_apps, None)
        mig.remove_code(django_apps, None)
        role.refresh_from_db()
        assert 'analytics:business' not in role.permissions
        assert 'analytics:view' in role.permissions

    def test_teacher_roles_untouched(self, org):
        self._migration().add_code(django_apps, None)
        teacher = OrgRole.objects.get(organization=org, name='teacher')
        assert 'analytics:business' not in teacher.permissions
