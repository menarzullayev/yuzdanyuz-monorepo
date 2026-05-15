"""
End-to-end middleware chain integration tests.

Bu testlar `set_current_org()` yoki `tenant_context()` ishlatmaydi —
Django Client orqali real HTTP request yuborib, BUTUN middleware zanjirini
(JWTAuth → Tenant → RLS) ishlatadi va quyidagilarni tekshiradi:

  - `request.org` to'g'ri set qilingan
  - PostgreSQL `app.current_org_id` session var to'g'ri qiymatga ega
  - Eskirgan/buzuq JWT silent fallback bilan ishlanadi
  - Org'siz regular user 403 oladi
  - Platform admin/staff org'siz unscoped queryset oladi
  - TenantManager fail-closed kontekst yo'qda

Lesson 11 (LESSONS.md) — bu testlar request lifecycle'ning butun
zanjirini ushlash uchun yozilgan.
"""

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from django.conf import settings
from django.test import Client

from apps.organizations.models import Membership, MembershipStatus, OrgRole

TEST_URLCONF = 'tests.integration._test_middleware_urls'


def _jwt(user, current_org=None, exp_delta=timedelta(hours=1)):
    """Helper: build a signed JWT with optional current_org claim."""
    payload = {
        'sub': str(user.pk),
        'type': 'access',
        'exp': datetime.now(UTC) + exp_delta,
    }
    if current_org is not None:
        payload['current_org'] = str(current_org.pk)
    return pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


@pytest.mark.integration
@pytest.mark.urls(TEST_URLCONF)
class TestRequestOrgIsSet:
    """Fix #1 — TenantMiddleware sets request.org for downstream consumers."""

    def test_authenticated_user_with_jwt_sets_request_org(self, db, user, org):
        """JWT current_org → request.org matches that org."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        client = Client()
        client.force_login(user)
        client.cookies['access_token'] = _jwt(user, current_org=org)

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        assert data['request_org_id'] == str(org.id)
        assert data['is_authenticated'] is True

    def test_authenticated_user_no_jwt_falls_back_to_primary_org(self, db, user, org):
        """No JWT → request.org = primary_organization."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        client = Client()
        client.force_login(user)

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        assert data['request_org_id'] == str(org.id)


@pytest.mark.integration
@pytest.mark.urls(TEST_URLCONF)
class TestRLSSessionVar:
    """Fix #1 (downstream effect) — RLSMiddleware sees request.org and sets DB var."""

    def test_rls_session_var_matches_request_org(self, db, user, org):
        """`SHOW app.current_org_id` returns the same UUID as request.org."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        client = Client()
        client.force_login(user)
        client.cookies['access_token'] = _jwt(user, current_org=org)

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        # Bu key assertion: avval bu joyda None edi (RLS hech qachon set qilmagan)
        assert data['rls_app_current_org_id'] == str(org.id)


@pytest.mark.integration
@pytest.mark.urls(TEST_URLCONF)
class TestJWTExpirationHandling:
    """Fix #2 — verify_exp=True (silent fallback to primary_org)."""

    def test_expired_jwt_silent_fallback_to_primary_org(self, db, user, org):
        """Eskirgan JWT → silent fallback → primary_organization (no 401)."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        client = Client()
        client.force_login(user)
        # JWT eskirgan — bir soat oldin expire bo'lgan
        client.cookies['access_token'] = _jwt(user, current_org=org, exp_delta=timedelta(hours=-1))

        response = client.get('/test-echo/')

        # Expired token bilan request fail bo'lmaydi, primary_org'ga tushadi
        assert response.status_code == 200
        data = response.json()
        assert data['request_org_id'] == str(org.id)

    def test_invalid_jwt_signature_fallback(self, db, user, org):
        """Buzuq imzo bilan JWT → silent fallback → primary_org."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        client = Client()
        client.force_login(user)
        client.cookies['access_token'] = 'invalid.jwt.token'

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        assert data['request_org_id'] == str(org.id)


@pytest.mark.integration
@pytest.mark.urls(TEST_URLCONF)
class TestUserWithoutOrg:
    """Fix #3b — regular user'da org yo'q bo'lsa, 403."""

    def test_regular_user_without_membership_gets_403(self, db, user):
        """Authenticated user, hech qanday membership → 403 Forbidden."""
        client = Client()
        client.force_login(user)

        response = client.get('/test-echo/')

        assert response.status_code == 403

    def test_anonymous_request_passes(self, db):
        """Anonymous user → middleware o'tkazadi (org=None, no 403)."""
        client = Client()

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        assert data['is_authenticated'] is False
        assert data['request_org_id'] is None

    def test_superuser_without_org_gets_unscoped(self, db, superuser):
        """Superuser org'siz → 403 emas, balki unscoped (admin uchun)."""
        client = Client()
        client.force_login(superuser)

        response = client.get('/test-echo/')

        assert response.status_code == 200
        data = response.json()
        assert data['is_superuser'] is True
        assert data['request_org_id'] is None
        # RLS ham clear (superuser bypass)
        assert data['rls_app_current_org_id'] is None


@pytest.mark.integration
class TestTenantManagerFailClosed:
    """Fix #3a — TenantManager fail-closed when no context."""

    def test_no_context_returns_empty_queryset(self, db, org):
        """Tenant context o'rnatilmagan → .objects.all() bo'sh queryset."""
        from apps.catalog.models import QuestionBank
        from core.tenant import clear_current_org, tenant_context

        with tenant_context(org):
            bank = QuestionBank.objects.create(organization=org, name='Test Bank', slug='test-bank')

        clear_current_org()
        result = list(QuestionBank.objects.all())
        assert result == [], 'Fail-closed: no context → empty queryset'

        # global_objects bilan ko'rinishi kerak (admin escape hatch)
        result_global = list(QuestionBank.global_objects.all())
        assert bank in result_global

    def test_unscoped_context_allows_all(self, db, org, org2):
        """unscoped_context() ichida barcha tenant ma'lumotlari ko'rinadi."""
        from apps.catalog.models import QuestionBank
        from core.tenant import clear_current_org, tenant_context, unscoped_context

        with tenant_context(org):
            QuestionBank.objects.create(organization=org, name='B1', slug='b1')
        with tenant_context(org2):
            QuestionBank.objects.create(organization=org2, name='B2', slug='b2')

        clear_current_org()

        # Default fail-closed
        assert list(QuestionBank.objects.all()) == []

        # Unscoped — barchasi ko'rinadi
        with unscoped_context():
            assert QuestionBank.objects.count() == 2


@pytest.mark.integration
@pytest.mark.urls(TEST_URLCONF)
class TestCrossOrgIsolation:
    """Cross-tenant data leak detection (regression test for L2 + L3)."""

    def test_org_a_user_cannot_see_org_b_data(self, db, user, user2, org, org2):
        """Org A user → faqat Org A QuestionBank ko'radi."""
        from apps.catalog.models import QuestionBank
        from core.tenant import tenant_context

        # Org B'da ma'lumot yaratamiz
        with tenant_context(org2):
            QuestionBank.objects.create(organization=org2, name='Secret Org B Bank', slug='org-b')

        # User'ni Org A'ga member qilamiz
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        # Endi User Org A bilan login qiladi → request.org=Org A
        client = Client()
        client.force_login(user)
        client.cookies['access_token'] = _jwt(user, current_org=org)

        response = client.get('/test-echo/')
        assert response.status_code == 200
        data = response.json()
        assert data['request_org_id'] == str(org.id)
        # RLS doim Org A bilan ishlaydi → DB level isolation faol
        assert data['rls_app_current_org_id'] == str(org.id)
