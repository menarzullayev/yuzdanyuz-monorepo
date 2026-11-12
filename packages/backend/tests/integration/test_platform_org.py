"""Platform organization: every B2C user is a STUDENT member of it (middleware 403 regression)."""

import importlib
import json
import uuid

import jwt as pyjwt
import pytest
from django.apps import apps as django_apps
from django.conf import settings as dj_settings
from django.db import connection
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.adapters import SocialAccountAdapter
from apps.accounts.models import CustomUser
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.telegram_auth import find_or_create_user
from apps.accounts.services.token_service import create_token_pair, max_devices_for
from apps.organizations.group_models import Group, Lesson
from apps.organizations.models import (
    JoinedVia,
    Membership,
    MembershipStatus,
    Organization,
    OrgRole,
    OrgType,
)
from apps.organizations.services import ensure_platform_membership, get_platform_org
from core.middleware.tenant import TenantMiddleware
from core.tenant import tenant_context

from .test_linking_google import google_login, make_request

pytestmark = pytest.mark.integration

STRONG = 'Tr0ub4dor&horse'
# One real student endpoint per domain app.
STUDENT_ENDPOINTS = [
    '/api/v1/exams/mocks/',
    '/api/v1/intelligence/mistakes/summary/',
    '/api/v1/wallet/',
    '/api/v1/engagement/typing/texts/',
]


def authed(user, device='device-bbbbbbbbbbbbbbbb'):
    c = Client()
    c.cookies['access_token'] = create_token_pair(user, device)[0]
    return c


def assert_student_endpoints_open(client):
    for url in STUDENT_ENDPOINTS:
        resp = client.get(url)
        assert resp.status_code == 200, (url, resp.status_code, resp.content[:200])


def join_real_org(user, org, role='student'):
    return Membership.objects.create(
        user=user,
        organization=org,
        role=OrgRole.objects.get(organization=org, name=role),
        status=MembershipStatus.ACTIVE,
        joined_via=JoinedVia.INVITE,
        is_primary=True,
        joined_at=timezone.now(),
    )


def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type='application/json')


class TestPlatformOrgService:
    def test_data_migration_created_it(self, db, settings):
        org = Organization.objects.get(slug=settings.PLATFORM_ORG_SLUG)
        assert org.org_type == OrgType.PLATFORM and org.status == 'active'
        assert org.name == settings.PROJECT_BRAND_NAME
        assert org.single_device_policy is False
        assert set(org.roles.values_list('name', flat=True)) >= {'student', 'owner'}

    def test_get_platform_org_is_idempotent_and_recreates_when_missing(self, db, settings):
        first = get_platform_org()
        assert get_platform_org().pk == first.pk
        Organization.objects.filter(pk=first.pk).delete()
        again = get_platform_org()
        assert again.org_type == OrgType.PLATFORM
        assert again.roles.filter(name='student', is_system_role=True).exists()
        assert Organization.objects.filter(slug=settings.PLATFORM_ORG_SLUG).count() == 1

    def test_ensure_is_idempotent(self, db):
        u = CustomUser.objects.create_user(username='idem', password=STRONG)
        a = ensure_platform_membership(u)
        b = ensure_platform_membership(u)
        assert a.pk == b.pk and u.memberships.count() == 1
        assert a.status == 'active' and a.role.name == 'student' and a.joined_via == 'auto'
        assert a.is_primary is False

    def test_staff_and_superusers_get_nothing(self, db):
        staff = CustomUser.objects.create_user(username='st', password=STRONG, is_staff=True)
        root = CustomUser.objects.create_superuser(username='rt', password=STRONG)
        assert ensure_platform_membership(staff) is None
        assert ensure_platform_membership(root) is None
        assert not Membership.objects.filter(user__in=[staff, root]).exists()

    def test_blocked_platform_membership_is_not_revived(self, db):
        u = CustomUser.objects.create_user(username='blk', password=STRONG)
        m = ensure_platform_membership(u)
        Membership.objects.filter(pk=m.pk).update(status=MembershipStatus.SUSPENDED)
        ensure_platform_membership(u)
        m.refresh_from_db()
        assert m.status == MembershipStatus.SUSPENDED


class TestSignupPathsGetPlatformMembership:
    """Reproduce the bug: a fresh user used to get 403 on every student endpoint."""

    def test_register_endpoint_user_can_use_student_endpoints(self, db):
        c = Client()
        resp = post_json(
            c,
            '/api/auth/register/',
            {
                'first_name': 'Ali',
                'last_name': 'Valiyev',
                'username': 'ali.platform',
                'password': STRONG,
            },
        )
        assert resp.status_code == 201, resp.content
        user = CustomUser.objects.get(username='ali.platform')
        m = user.memberships.get()
        assert m.organization == get_platform_org() and m.role.name == 'student'
        assert resp.json()['user']['user_type'] == 'b2c'
        assert resp.json()['user']['primary_organization']['slug'] == 'platform'
        assert_student_endpoints_open(c)  # cookies from the register response

    def test_telegram_miniapp_user(self, db):
        user, created = find_or_create_user(
            {'id': 777001, 'first_name': 'Tg', 'last_name': 'User', 'username': 'tgu'}
        )
        assert created
        assert user.memberships.get().organization == get_platform_org()
        assert_student_endpoints_open(authed(user))

    def test_telegram_deeplink_first_signin_user(self, db):
        pk = dl._find_or_create_user(888002, '+998901112233', 'Deep', 'Link')
        user = CustomUser.objects.get(pk=pk)
        assert user.memberships.get().organization == get_platform_org()
        assert_student_endpoints_open(authed(user))

    def test_telegram_existing_user_is_not_duplicated(self, db):
        user, _ = find_or_create_user({'id': 777003, 'first_name': 'T'})
        find_or_create_user({'id': 777003, 'first_name': 'T'})
        assert user.memberships.count() == 1

    def test_google_first_signin_user(self, db):
        adapter, request, sl = SocialAccountAdapter(), make_request(), google_login()
        adapter.pre_social_login(request, sl)
        adapter.populate_user(
            request, sl, {'email': 'g@example.com', 'first_name': 'Gu', 'last_name': 'Le'}
        )
        adapter.save_user(request, sl)
        user = CustomUser.objects.get(email='g@example.com')
        assert user.memberships.get().organization == get_platform_org()
        assert_student_endpoints_open(authed(user))

    def test_admin_created_b2c_user_and_staff_promotion_drops_it(self, db, rf):
        from django.contrib import admin

        from apps.accounts.admin import CustomUserAdmin

        ma = CustomUserAdmin(CustomUser, admin.site)
        u = CustomUser(username='adm.made')
        u.set_password(STRONG)
        ma.save_model(rf.get('/'), u, form=None, change=False)
        assert u.memberships.get().organization == get_platform_org()
        # promoting to staff drops the (auto) platform membership so they stay unscoped
        u.is_staff = True
        ma.save_model(rf.get('/'), u, form=None, change=True)
        assert not u.memberships.exists()

    def test_registration_rolls_back_the_user_when_membership_fails(self, db, monkeypatch):
        import apps.accounts.views.credentials as cred

        def boom(user):
            raise RuntimeError('membership failed')

        monkeypatch.setattr(cred, 'ensure_platform_membership', boom)
        c = Client(raise_request_exception=False)
        resp = post_json(
            c,
            '/api/auth/register/',
            {'first_name': 'Al', 'last_name': 'Va', 'username': 'rollback.me', 'password': STRONG},
        )
        assert resp.status_code == 500
        assert not CustomUser.objects.filter(username='rollback.me').exists()


class TestBackfillMigration:
    def _run(self):
        mig = importlib.import_module(
            'apps.organizations.migrations.0008_platform_org_and_backfill'
        )
        mig.forwards(django_apps, None)

    def test_backfill_only_orgless_active_non_staff(self, db, org):
        plain = CustomUser.objects.create_user(username='plain', password=STRONG)
        member = CustomUser.objects.create_user(username='member', password=STRONG)
        join_real_org(member, org)
        inactive = CustomUser.objects.create_user(username='gone', password=STRONG, is_active=False)
        staff = CustomUser.objects.create_user(username='stf', password=STRONG, is_staff=True)
        left = CustomUser.objects.create_user(username='left', password=STRONG)
        Membership.objects.create(
            user=left,
            organization=org,
            role=OrgRole.objects.get(organization=org, name='student'),
            status=MembershipStatus.LEFT,
        )

        self._run()
        platform = get_platform_org()
        assert plain.memberships.get().organization == platform
        assert left.memberships.filter(organization=platform, status='active').exists()
        assert not member.memberships.filter(organization=platform).exists()  # has a real org
        assert not inactive.memberships.exists()
        assert not staff.memberships.exists()

        self._run()  # idempotent
        assert plain.memberships.count() == 1
        assert Membership.objects.filter(organization=platform).count() == 2
        assert Organization.objects.filter(org_type=OrgType.PLATFORM).count() == 1

    def test_backfill_unblocks_a_pre_existing_orgless_user(self, db):
        u = CustomUser.objects.create_user(username='old', password=STRONG)
        assert u.is_b2c and u.primary_organization is None
        assert authed(u).get(STUDENT_ENDPOINTS[0]).status_code == 403  # the bug
        self._run()
        u = CustomUser.objects.get(pk=u.pk)
        assert u.primary_organization == get_platform_org() and u.user_type == 'b2c'
        assert_student_endpoints_open(authed(u))


class TestPrimaryOrganizationSemantics:
    def test_real_org_beats_platform_and_platform_is_kept(self, db, org):
        u = CustomUser.objects.create_user(username='both', password=STRONG)
        ensure_platform_membership(u)
        assert u.user_type == 'b2c'
        join_real_org(u, org, role='teacher')
        u = CustomUser.objects.get(pk=u.pk)
        assert u.primary_organization == org
        assert u.user_type == 'teacher'
        assert u.memberships.filter(organization=get_platform_org(), status='active').exists()

    def test_even_a_newer_platform_membership_does_not_win(self, db, org):
        u = CustomUser.objects.create_user(username='order', password=STRONG)
        join_real_org(u, org)
        ensure_platform_membership(u)  # newer than the real one
        assert CustomUser.objects.get(pk=u.pk).primary_organization == org

    def test_leaving_the_real_org_falls_back_to_platform(self, db, org):
        u = CustomUser.objects.create_user(username='leaver', password=STRONG)
        ensure_platform_membership(u)
        real = join_real_org(u, org)
        Membership.objects.filter(pk=real.pk).update(status=MembershipStatus.LEFT)
        u = CustomUser.objects.get(pk=u.pk)
        assert u.primary_organization == get_platform_org() and u.user_type == 'b2c'

    def test_jwt_current_org_can_select_either_membership(self, db, org):
        u = CustomUser.objects.create_user(username='switch', password=STRONG)
        ensure_platform_membership(u)
        join_real_org(u, org)
        mw = TenantMiddleware(lambda r: None)
        for target in (org, get_platform_org()):
            token = pyjwt.encode(
                {'sub': str(u.pk), 'current_org': str(target.pk), 'exp': 9999999999},
                dj_settings.SECRET_KEY,
                algorithm='HS256',
            )
            req = RequestFactory().get('/')
            req.user = u
            req.COOKIES['access_token'] = token
            assert mw._resolve_org(req) == target

    def test_platform_org_keeps_multi_device_policy_for_b2c(self, db):
        u = CustomUser.objects.create_user(username='dev', password=STRONG)
        ensure_platform_membership(u)
        assert max_devices_for(u) == 3


class TestMiddleware:
    def test_orgless_user_still_gets_403(self, db):
        """Negative: no membership at all (e.g. removed) -> 403, the guard is intact."""
        u = CustomUser.objects.create_user(username='nomem', password=STRONG)
        for url in STUDENT_ENDPOINTS:
            assert authed(u).get(url).status_code == 403

    def test_blocked_platform_membership_gets_403(self, db):
        u = CustomUser.objects.create_user(username='blocked', password=STRONG)
        m = ensure_platform_membership(u)
        Membership.objects.filter(pk=m.pk).update(status=MembershipStatus.SUSPENDED)
        assert authed(u).get(STUDENT_ENDPOINTS[0]).status_code == 403

    def test_certificate_verify_is_reachable_by_orgless_logged_in_user(self, db):
        u = CustomUser.objects.create_user(username='nomem2', password=STRONG)
        for prefix in ('/api/exams', '/api/v1/exams'):
            resp = authed(u).get(f'{prefix}/certificates/verify/NOPE-123/')
            assert resp.status_code == 404, prefix  # reached the view, not the 403 guard
            assert resp.json()['status'] == 'not_found'
            assert Client().get(f'{prefix}/certificates/verify/NOPE-123/').status_code == 404

    def test_partnership_apply_is_reachable_by_orgless_logged_in_user(self, db):
        u = CustomUser.objects.create_user(username='nomem3', password=STRONG)
        assert post_json(authed(u), '/api/v1/partnerships/apply/', {}).status_code == 400
        assert post_json(Client(), '/api/v1/partnerships/apply/', {}).status_code == 400

    def test_public_org_rating_is_reachable_by_orgless_logged_in_user(self, db):
        u = CustomUser.objects.create_user(username='nomem4', password=STRONG)
        assert authed(u).get('/api/v1/engagement/ratings/organizations/').status_code == 200

    def test_exempt_list_is_explicit_not_a_wildcard(self, db):
        """Negative: sibling paths next to the exempt ones are still guarded."""
        u = CustomUser.objects.create_user(username='nomem5', password=STRONG)
        assert authed(u).get('/api/v1/engagement/ratings/mine/').status_code == 403
        assert authed(u).get('/api/exams/certificates/mine/').status_code == 403
        assert authed(u).get('/api/v1/partnerships/applications/').status_code == 403


class TestCrossTenantIsolation:
    """Platform org is a tenant like any other (manager + RLS)."""

    @pytest.fixture
    def rows(self, db, org):
        out = {}
        for key, o in (('platform', get_platform_org()), ('real', org)):
            with tenant_context(o):
                g = Group.objects.create(organization=o, name=f'G-{key}')
                Lesson.objects.create(
                    organization=o, group=g, title=f'L-{key}', scheduled_at=timezone.now()
                )
            out[key] = o
        return out

    def test_manager_isolation_both_directions(self, rows):
        with tenant_context(rows['platform']):
            assert list(Group.objects.values_list('name', flat=True)) == ['G-platform']
        with tenant_context(rows['real']):
            assert list(Group.objects.values_list('name', flat=True)) == ['G-real']

    def test_database_rls_isolation_both_directions(self, rows):
        role = f'rls_probe_{uuid.uuid4().hex[:8]}'
        with connection.cursor() as cur:
            cur.execute(f'CREATE ROLE {role} NOLOGIN')
            cur.execute(f'GRANT SELECT ON organizations_group, organizations_lesson TO {role}')
            cur.execute(f'SET LOCAL ROLE {role}')
            try:
                for key, title in (('platform', 'L-platform'), ('real', 'L-real')):
                    with tenant_context(rows[key]):  # core.db_rls sets the org per statement
                        cur.execute('SELECT title FROM organizations_lesson')
                        assert [r[0] for r in cur.fetchall()] == [title]
            finally:
                cur.execute('RESET ROLE')

    def test_platform_student_request_cannot_see_real_org_data(self, db, rows):
        u = CustomUser.objects.create_user(username='iso', password=STRONG)
        ensure_platform_membership(u)
        req_org = u.primary_organization
        assert req_org == rows['platform']
        with tenant_context(req_org):
            assert not Group.objects.filter(name='G-real').exists()
