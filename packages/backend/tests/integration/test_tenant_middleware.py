"""
Integration tests for core/middleware/tenant.py
Focus: JWT-aware tenant resolution, membership validation, fallback logic
"""

import pytest
import jwt as pyjwt
from datetime import datetime, timezone, timedelta
from django.conf import settings
from django.test import RequestFactory
from core.middleware.tenant import TenantMiddleware
from apps.organizations.models import Membership, MembershipStatus, OrgRole


def dummy_view(request):
    """Dummy response callable for middleware."""
    return type('Response', (), {'status_code': 200})()


@pytest.mark.integration
class TestTenantMiddlewareResolution:
    """TenantMiddleware._resolve_org logic tests."""

    def test_resolve_org_anonymous_returns_none(self, db):
        """Anonymous user → _resolve_org returns None."""
        factory = RequestFactory()
        request = factory.get('/')

        middleware = TenantMiddleware(dummy_view)
        org = middleware._resolve_org(request)

        assert org is None

    def test_resolve_org_authenticated_no_jwt_returns_primary(self, db, user, org):
        """Authenticated, no JWT → _resolve_org returns primary_organization."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role,
            status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        factory = RequestFactory()
        request = factory.get('/')
        request.user = user

        middleware = TenantMiddleware(dummy_view)
        resolved_org = middleware._resolve_org(request)

        assert resolved_org == org

    def test_resolve_org_jwt_with_current_org_validates_membership(self, db, user, org, org2):
        """JWT with current_org + membership → resolve to that org."""
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

        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'current_org': str(org2.pk),
            'exp': datetime.now(timezone.utc) + timedelta(hours=1)
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        factory = RequestFactory()
        request = factory.get('/')
        request.user = user
        request.COOKIES['access_token'] = token

        middleware = TenantMiddleware(dummy_view)
        resolved_org = middleware._resolve_org(request)

        assert resolved_org == org2

    def test_resolve_org_jwt_non_existent_org_fallback(self, db, user, org):
        """JWT has non-existent org → fallback to primary."""
        import uuid

        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role,
            status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        bad_org_id = str(uuid.uuid4())
        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'current_org': bad_org_id,
            'exp': datetime.now(timezone.utc) + timedelta(hours=1)
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        factory = RequestFactory()
        request = factory.get('/')
        request.user = user
        request.COOKIES['access_token'] = token

        middleware = TenantMiddleware(dummy_view)
        resolved_org = middleware._resolve_org(request)

        assert resolved_org == org

    def test_resolve_org_jwt_non_member_org_fallback(self, db, user, org, org2):
        """JWT has org but user not member → fallback to primary_org."""
        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user, organization=org, role=role,
            status=MembershipStatus.ACTIVE, is_primary=True
        )
        m.activate()

        payload = {
            'sub': str(user.pk),
            'type': 'access',
            'current_org': str(org2.pk),
            'exp': datetime.now(timezone.utc) + timedelta(hours=1)
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        factory = RequestFactory()
        request = factory.get('/')
        request.user = user
        request.COOKIES['access_token'] = token

        middleware = TenantMiddleware(dummy_view)
        resolved_org = middleware._resolve_org(request)

        assert resolved_org == org

    def test_resolve_org_superuser_bypasses_membership(self, db, superuser, org):
        """Superuser + any org in JWT → resolve to that org (no membership required)."""
        payload = {
            'sub': str(superuser.pk),
            'type': 'access',
            'current_org': str(org.pk),
            'exp': datetime.now(timezone.utc) + timedelta(hours=1)
        }
        token = pyjwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

        factory = RequestFactory()
        request = factory.get('/')
        request.user = superuser
        request.COOKIES['access_token'] = token

        middleware = TenantMiddleware(dummy_view)
        resolved_org = middleware._resolve_org(request)

        assert resolved_org == org

    def test_org_id_from_jwt_invalid_token_returns_none(self, db):
        """Invalid/tampered JWT → _org_id_from_jwt returns None."""
        factory = RequestFactory()
        request = factory.get('/')
        request.COOKIES['access_token'] = 'invalid.jwt.token'

        middleware = TenantMiddleware(dummy_view)
        org_id = middleware._org_id_from_jwt(request)

        assert org_id is None

    def test_org_id_from_jwt_no_cookie_returns_none(self, db):
        """No access_token cookie → _org_id_from_jwt returns None."""
        factory = RequestFactory()
        request = factory.get('/')
        # No COOKIES at all

        middleware = TenantMiddleware(dummy_view)
        org_id = middleware._org_id_from_jwt(request)

        assert org_id is None
