"""B2B tenant tables: manager isolation, RLS (state + real enforcement), role seeding."""

import importlib
import uuid

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.utils import timezone

from apps.organizations.group_models import Group, GroupMember, Lesson
from apps.organizations.models import OrgRole
from core.tenant import tenant_context

pytestmark = pytest.mark.security

TABLES = {
    'organizations_group': 'group',
    'organizations_groupmember': 'groupmember',
    'organizations_lesson': 'lesson',
}
ALL_CMDS = {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}


@pytest.fixture
def data(db, org, org2, user, user2):
    out = {}
    for key, o, u in (('a', org, user), ('b', org2, user2)):
        with tenant_context(o):
            g = Group.objects.create(organization=o, name=f'G-{key}')
            GroupMember.objects.create(organization=o, group=g, user=u)
            lsn = Lesson.objects.create(
                organization=o, group=g, title=f'L-{key}', scheduled_at=timezone.now()
            )
        out[key] = (g, lsn)
    return out


class TestManagerIsolation:
    @pytest.mark.parametrize('model', [Group, GroupMember, Lesson])
    def test_each_org_sees_only_its_rows(self, data, org, org2, model):
        for o in (org, org2):
            with tenant_context(o):
                rows = list(model.objects.all())
                assert len(rows) == 1 and rows[0].organization_id == o.id

    @pytest.mark.parametrize('model', [Group, GroupMember, Lesson])
    def test_no_context_means_empty(self, data, model):
        assert model.objects.count() == 0  # fail-closed

    def test_foreign_pk_lookup_fails(self, data, org):
        g_b, l_b = data['b']
        with tenant_context(org):
            assert not Group.objects.filter(pk=g_b.pk).exists()
            assert not Lesson.objects.filter(pk=l_b.pk).exists()


def _rls_state(table):
    with connection.cursor() as cur:
        cur.execute('SELECT rowsecurity FROM pg_tables WHERE tablename = %s', [table])
        row = cur.fetchone()
        cur.execute(
            "SELECT cmd FROM pg_policies WHERE tablename = %s AND policyname <> 'rls_bypass'",
            [table],
        )
        return (row[0] if row else None), {r[0] for r in cur.fetchall()}


class TestRls:
    @pytest.mark.parametrize('table', list(TABLES))
    def test_rls_enabled_with_four_policies(self, db, table):
        assert _rls_state(table) == (True, ALL_CMDS)

    def test_check_catches_table_without_rls(self, db):
        with connection.cursor() as cur:
            cur.execute('CREATE TEMP TABLE b2b_rls_probe (id int)')
        assert _rls_state('b2b_rls_probe') != (True, ALL_CMDS)

    def test_database_enforces_isolation_for_a_non_superuser_role(self, data, org, org2):
        """The test connection is a superuser (bypasses RLS), so switch to a plain role."""
        role = f'rls_probe_{uuid.uuid4().hex[:8]}'
        with connection.cursor() as cur:
            cur.execute(f'CREATE ROLE {role} NOLOGIN')
            cur.execute(f'GRANT SELECT ON {", ".join(TABLES)} TO {role}')
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                for current, expected_title in ((org, 'L-a'), (org2, 'L-b')):
                    with tenant_context(current):  # core.db_rls sets the org per statement
                        for table in TABLES:
                            cur.execute(f'SELECT DISTINCT organization_id FROM {table}')
                            assert {r[0] for r in cur.fetchall()} == {current.id}, table
                        cur.execute('SELECT title FROM organizations_lesson')
                        assert [r[0] for r in cur.fetchall()] == [expected_title]
            finally:
                cur.execute('RESET ROLE')


class TestRoleSeeding:
    def test_new_org_gets_group_permissions(self, org):
        perms = {r.name: set(r.permissions) for r in OrgRole.objects.filter(organization=org)}
        assert {'groups:*', 'lessons:*'} <= perms['manager']
        assert {'groups:view_own', 'lessons:*'} <= perms['teacher']
        assert {'groups:view_own', 'lessons:view_own'} <= perms['student']
        assert 'groups:create' not in perms['student'] | perms['teacher']

    def test_owner_wildcard_covers_everything(self, org):
        owner = OrgRole.objects.get(organization=org, name='owner')
        assert owner.has_permission('groups:create') and owner.has_permission('lessons:delete')

    def test_wildcard_semantics(self, org):
        teacher = OrgRole.objects.get(organization=org, name='teacher')
        assert teacher.has_permission('lessons:create')
        assert not teacher.has_permission('groups:edit')
        student = OrgRole.objects.get(organization=org, name='student')
        assert not student.has_permission('lessons:create')

    def test_data_migration_adds_missing_codes_only_to_system_roles(self, org):
        mig = importlib.import_module('apps.organizations.migrations.0006_b2b_role_permissions')
        # simulate an org seeded before this feature
        OrgRole.objects.filter(organization=org, name='manager').update(permissions=['exam:view'])
        custom = OrgRole.objects.create(
            organization=org, name='custom', display_name='C', permissions=['exam:view']
        )
        mig.add_codes(django_apps, None)
        manager = OrgRole.objects.get(organization=org, name='manager')
        assert set(manager.permissions) == {'exam:view', 'groups:*', 'lessons:*'}
        custom.refresh_from_db()
        assert custom.permissions == ['exam:view']  # negative: non-system role untouched
        mig.add_codes(django_apps, None)  # idempotent
        manager.refresh_from_db()
        assert manager.permissions.count('groups:*') == 1
        mig.remove_codes(django_apps, None)
        manager.refresh_from_db()
        assert manager.permissions == ['exam:view']
