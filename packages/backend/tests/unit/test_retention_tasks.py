"""ISSUE-113 — retention Celery tasks tests."""

from datetime import timedelta

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.accounts import tasks as accounts_tasks
from apps.accounts.models import OTPCode

pytestmark = pytest.mark.unit


class TestCleanupExpiredSessions:
    def test_deletes_old_sessions(self, db):
        # Eski expired session
        old = SessionStore()
        old['x'] = 1
        old.create()
        Session.objects.filter(session_key=old.session_key).update(
            expire_date=timezone.now() - timedelta(days=30)
        )
        # Yangi session (saqlanishi kerak)
        fresh = SessionStore()
        fresh['x'] = 2
        fresh.create()

        result = accounts_tasks.cleanup_expired_sessions(days=14)
        assert result['deleted'] == 1
        assert Session.objects.filter(session_key=fresh.session_key).exists()
        assert not Session.objects.filter(session_key=old.session_key).exists()

    def test_lock_skips_concurrent(self, db, monkeypatch):
        # Lock'ni acquired=False qilamiz
        from contextlib import contextmanager

        @contextmanager
        def busy_lock(*args, **kwargs):
            yield False

        monkeypatch.setattr(accounts_tasks, 'single_runner_lock', busy_lock)
        result = accounts_tasks.cleanup_expired_sessions()
        assert result == {'status': 'skipped_lock_busy'}


class TestCleanupUsedOTPCodes:
    def test_deletes_verified_and_expired(self, db):
        # Used
        used = OTPCode.objects.create(
            phone='+998901234567',
            code='111111',
            is_verified=True,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        # Expired beyond grace
        old_expired = OTPCode.objects.create(
            phone='+998901234568',
            code='222222',
            expires_at=timezone.now() - timedelta(hours=48),
        )
        # Fresh, not verified — saqlanadi
        fresh = OTPCode.objects.create(
            phone='+998901234569',
            code='333333',
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        result = accounts_tasks.cleanup_used_otp_codes(grace_hours=24)
        assert result['deleted'] >= 2
        assert OTPCode.objects.filter(pk=fresh.pk).exists()
        assert not OTPCode.objects.filter(pk=used.pk).exists()
        assert not OTPCode.objects.filter(pk=old_expired.pk).exists()


class TestCleanupOldWebhookDeliveries:
    def test_deletes_old_terminal_deliveries(self, db, org):
        from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
        from core.tenant import unscoped_context

        with unscoped_context():
            ep = WebhookEndpoint.objects.create(organization=org, name='X', url='https://x.example')
            old_sent = WebhookDelivery.objects.create(
                endpoint=ep, event='exam.submitted', payload={}, status='sent'
            )
            WebhookDelivery.objects.filter(pk=old_sent.pk).update(
                completed_at=timezone.now() - timedelta(days=200)
            )
            recent_sent = WebhookDelivery.objects.create(
                endpoint=ep,
                event='exam.submitted',
                payload={},
                status='sent',
                completed_at=timezone.now(),
            )
            pending = WebhookDelivery.objects.create(
                endpoint=ep, event='exam.submitted', payload={}, status='pending'
            )

        result = accounts_tasks.cleanup_old_webhook_deliveries(days=90)
        assert result['deleted'] == 1
        with unscoped_context():
            assert not WebhookDelivery.objects.filter(pk=old_sent.pk).exists()
            assert WebhookDelivery.objects.filter(pk=recent_sent.pk).exists()
            assert WebhookDelivery.objects.filter(pk=pending.pk).exists()
