"""
Global pytest fixtures for Task 1 + Task 2 tests
"""

from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.catalog.models import Subject
from apps.organizations.models import Membership, MembershipStatus, Organization, OrgRole

User = get_user_model()


@pytest.fixture(autouse=True)
def reset_tenant_context():
    """
    Test isolation: ContextVar (current_org, unscoped_allowed) ni har test
    boshida tozalash. Aks holda set_current_org() ishlatuvchi testlar
    keyingi testlarga state leak qiladi (PR #27 task tests'da topilgan).
    """
    from core.tenant import clear_current_org, set_unscoped_allowed

    clear_current_org()
    set_unscoped_allowed(False)
    yield
    clear_current_org()
    set_unscoped_allowed(False)


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Mock Redis for all tests using fakeredis."""
    import fakeredis

    from apps.accounts.services import token_service

    # Create fakeredis client with decode_responses=True to match production behavior
    redis_client = fakeredis.FakeStrictRedis(decode_responses=True)

    # Patch the global _redis_client variable
    token_service._redis_client = redis_client

    # Also patch the _redis function to always return fakeredis
    def fake_redis_func():
        return redis_client

    monkeypatch.setattr(token_service, '_redis', fake_redis_func)
    monkeypatch.setattr('redis.Redis.from_url', lambda *a, **k: redis_client)

    yield redis_client


# ─── Organization Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def org(db):
    """Test organization with auto-created system roles."""
    return Organization.objects.create(name='Test Org', slug='test-org')


@pytest.fixture
def org2(db):
    """Second test organization for isolation tests."""
    return Organization.objects.create(name='Other Org', slug='other-org')


# ─── User Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def user(db):
    """Regular user with no organization membership."""
    return User.objects.create_user(
        username='testuser', email='test@example.com', password='pass123'
    )


@pytest.fixture
def user2(db):
    """Second user for isolation tests."""
    return User.objects.create_user(
        username='testuser2', email='test2@example.com', password='pass123'
    )


@pytest.fixture
def superuser(db):
    """Superuser with platform_admin privileges."""
    return User.objects.create_superuser(
        username='admin', email='admin@example.com', password='pass123'
    )


# ─── Membership Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def member(db, user, org):
    """User as student in org (active, primary)."""
    role = OrgRole.objects.get(organization=org, name='student')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


@pytest.fixture
def owner_member(db, user, org):
    """User as owner in org (active, primary)."""
    role = OrgRole.objects.get(organization=org, name='owner')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


@pytest.fixture
def teacher_member(db, user, org):
    """User as teacher in org (active, primary)."""
    role = OrgRole.objects.get(organization=org, name='teacher')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


@pytest.fixture
def manager_member(db, user, org):
    """User as manager in org (active, primary)."""
    role = OrgRole.objects.get(organization=org, name='manager')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


@pytest.fixture
def suspended_member(db, user, org):
    """User with suspended membership."""
    role = OrgRole.objects.get(organization=org, name='student')
    return Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.SUSPENDED, is_primary=False
    )


# ─── Catalog Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def subject(db):
    """Default subject for question tests."""
    return Subject.objects.create(name='Test Subject', slug='test-subject')


# ─── Multi-org Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def user_multi_org(db, org, org2):
    """User with membership in both orgs."""
    user = User.objects.create_user(
        username='multiuser', email='multi@example.com', password='pass123'
    )

    # Member in org
    role1 = OrgRole.objects.get(organization=org, name='student')
    m1 = Membership.objects.create(
        user=user, organization=org, role=role1, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m1.activate()

    # Member in org2
    role2 = OrgRole.objects.get(organization=org2, name='teacher')
    m2 = Membership.objects.create(
        user=user, organization=org2, role=role2, status=MembershipStatus.ACTIVE, is_primary=False
    )
    m2.activate()

    return user


# ─── Auth Fixtures (Task 2) ────────────────────────────────────────────────────


@pytest.fixture
def phone_number():
    """Standard test phone number."""
    return '+998901234567'


@pytest.fixture
def mock_request():
    """Django RequestFactory request with common headers."""
    factory = RequestFactory()
    request = factory.get('/')
    request.META['HTTP_USER_AGENT'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    request.META['REMOTE_ADDR'] = '192.168.1.100'
    return request


@pytest.fixture
def mock_request_different_ip():
    """Request with different IP for device change tests."""
    factory = RequestFactory()
    request = factory.get('/')
    request.META['HTTP_USER_AGENT'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    request.META['REMOTE_ADDR'] = '192.168.1.101'
    return request


@pytest.fixture
def mock_request_different_ua():
    """Request with different User-Agent for device change tests."""
    factory = RequestFactory()
    request = factory.get('/')
    request.META['HTTP_USER_AGENT'] = 'Safari/537.36'
    request.META['REMOTE_ADDR'] = '192.168.1.100'
    return request


@pytest.fixture
def user_with_phone(db, org):
    """User with phone number and active organization."""
    user = User.objects.create_user(
        username='phoneuser',
        email='phone@example.com',
        password='pass123',
        phone_number='+998901234567',
    )
    role = OrgRole.objects.get(organization=org, name='student')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return user


@pytest.fixture
def user_with_telegram(db, org):
    """User with Telegram ID linked."""
    user = User.objects.create_user(
        username='telegramuser', email='tg@example.com', password='pass123', telegram_id=123456789
    )
    role = OrgRole.objects.get(organization=org, name='student')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return user


@pytest.fixture
def mock_sms_backend(monkeypatch):
    """Mock SMS backend to avoid external calls."""
    sent_messages = []

    class MockSMSBackend:
        def send(self, phone, text):
            sent_messages.append({'phone': phone, 'message': text})
            return True

    # Patch get_sms_backend to return mock
    from apps.accounts.services import sms_backend

    monkeypatch.setattr(sms_backend, 'get_sms_backend', lambda: MockSMSBackend())
    return sent_messages


@pytest.fixture
def mock_otp_code(db, phone_number):
    """Create a valid OTP code for testing."""
    from datetime import datetime, timedelta, timezone

    from apps.accounts.models import OTPCode

    code = OTPCode.objects.create(
        phone=phone_number,
        code='123456',
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
        is_verified=False,
        attempts=0,
    )
    return code
