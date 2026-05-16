"""
Unit tests for `apps.engagement.notifications_service`.

Coverage:
  - queue(): creates Notification, schedules fan_out task
  - fan_out_notification: in-app default, Telegram when telegram_id present,
    SMS only on important+ priority + TG failure + phone_number present
  - already-sent / already-read short circuits
  - non-existent notification id handling
  - provider stubs are NOT called externally (mocked)
"""

from unittest.mock import patch

import pytest

from apps.engagement import notifications_service
from apps.engagement.models import Notification


@pytest.mark.unit
class TestQueue:
    def test_creates_notification_with_pending_status(self, db, user):
        # CELERY_TASK_ALWAYS_EAGER=True → fan_out runs synchronously and marks
        # status SENT. We test the queue() side effects: a Notification row exists.
        with (
            patch.object(notifications_service, '_send_telegram'),
            patch.object(notifications_service, '_send_sms'),
        ):
            n = notifications_service.queue(user=user, title='Hi', body='Body')
        assert isinstance(n, Notification)
        n.refresh_from_db()
        # Eager Celery executes fan_out → status becomes SENT
        assert n.status == Notification.Status.SENT
        assert n.user_id == user.id
        assert n.title == 'Hi'
        assert n.body == 'Body'
        assert n.channel == Notification.Channel.IN_APP

    def test_metadata_stored(self, db, user):
        with (
            patch.object(notifications_service, '_send_telegram'),
            patch.object(notifications_service, '_send_sms'),
        ):
            n = notifications_service.queue(
                user=user, title='X', body='Y', metadata={'key': 'val', 'count': 3}
            )
        n.refresh_from_db()
        assert n.metadata == {'key': 'val', 'count': 3}

    def test_default_metadata_empty_dict(self, db, user):
        with (
            patch.object(notifications_service, '_send_telegram'),
            patch.object(notifications_service, '_send_sms'),
        ):
            n = notifications_service.queue(user=user, title='X', body='Y')
        n.refresh_from_db()
        assert n.metadata == {}

    def test_priority_stored(self, db, user):
        with (
            patch.object(notifications_service, '_send_telegram'),
            patch.object(notifications_service, '_send_sms'),
        ):
            n = notifications_service.queue(user=user, title='X', body='Y', priority='urgent')
        n.refresh_from_db()
        assert n.priority == Notification.Priority.URGENT


@pytest.mark.unit
class TestFanOut:
    def _build_pending(self, user, *, priority='normal'):
        return Notification.objects.create(
            user=user,
            channel=Notification.Channel.IN_APP,
            priority=priority,
            title='T',
            body='B',
            status=Notification.Status.PENDING,
        )

    def test_returns_not_found_for_missing_id(self, db):
        import uuid

        result = notifications_service.fan_out_notification(str(uuid.uuid4()))
        assert result == {'status': 'not_found'}

    def test_already_sent_short_circuits(self, db, user):
        n = self._build_pending(user)
        n.status = Notification.Status.SENT
        n.save()
        result = notifications_service.fan_out_notification(str(n.id))
        assert result == {'status': 'already_sent'}

    def test_already_read_short_circuits(self, db, user):
        n = self._build_pending(user)
        n.status = Notification.Status.READ
        n.save()
        result = notifications_service.fan_out_notification(str(n.id))
        assert result == {'status': 'already_sent'}

    def test_in_app_only_when_no_external_handles(self, db, user):
        n = self._build_pending(user)
        with (
            patch.object(notifications_service, '_send_telegram') as tg,
            patch.object(notifications_service, '_send_sms') as sms,
        ):
            result = notifications_service.fan_out_notification(str(n.id))
        tg.assert_not_called()
        sms.assert_not_called()
        assert result['status'] == 'sent'
        assert result['channels'] == ['in_app']

    def test_telegram_sent_when_user_has_telegram_id(self, db, user):
        user.telegram_id = 123456789
        user.save()
        n = self._build_pending(user)
        with (
            patch.object(notifications_service, '_send_telegram') as tg,
            patch.object(notifications_service, '_send_sms') as sms,
        ):
            result = notifications_service.fan_out_notification(str(n.id))
        tg.assert_called_once()
        sms.assert_not_called()
        assert 'telegram' in result['channels']

    def test_sms_fallback_when_tg_fails_and_priority_important(self, db, user):
        user.telegram_id = 999
        user.phone_number = '+998901234567'
        user.save()
        n = self._build_pending(user, priority='important')
        with (
            patch.object(
                notifications_service, '_send_telegram', side_effect=RuntimeError('TG down')
            ) as tg,
            patch.object(notifications_service, '_send_sms') as sms,
        ):
            result = notifications_service.fan_out_notification(str(n.id))
        tg.assert_called_once()
        sms.assert_called_once()
        assert 'sms' in result['channels']
        assert 'telegram' not in result['channels']

    def test_no_sms_when_priority_normal(self, db, user):
        user.phone_number = '+998901234567'
        user.save()
        n = self._build_pending(user, priority='normal')
        with patch.object(notifications_service, '_send_sms') as sms:
            notifications_service.fan_out_notification(str(n.id))
        sms.assert_not_called()

    def test_no_sms_when_telegram_succeeded(self, db, user):
        """Important + TG success → SMS skipped (TG already delivered)."""
        user.telegram_id = 555
        user.phone_number = '+998901234567'
        user.save()
        n = self._build_pending(user, priority='urgent')
        with (
            patch.object(notifications_service, '_send_telegram'),
            patch.object(notifications_service, '_send_sms') as sms,
        ):
            result = notifications_service.fan_out_notification(str(n.id))
        sms.assert_not_called()
        assert 'sms' not in result['channels']

    def test_no_sms_when_no_phone_number(self, db, user):
        """Important priority but no phone → SMS skipped."""
        n = self._build_pending(user, priority='important')
        with patch.object(notifications_service, '_send_sms') as sms:
            notifications_service.fan_out_notification(str(n.id))
        sms.assert_not_called()

    def test_sms_failure_swallowed(self, db, user):
        user.phone_number = '+998901234567'
        user.save()
        n = self._build_pending(user, priority='urgent')
        with patch.object(notifications_service, '_send_sms', side_effect=RuntimeError('SMS down')):
            # Should not raise
            result = notifications_service.fan_out_notification(str(n.id))
        assert result['status'] == 'sent'

    def test_delivery_attempts_incremented(self, db, user):
        n = self._build_pending(user)
        assert n.delivery_attempts == 0
        notifications_service.fan_out_notification(str(n.id))
        n.refresh_from_db()
        assert n.delivery_attempts == 1
        assert n.sent_at is not None


@pytest.mark.unit
class TestProviderStubs:
    def test_send_telegram_stub_mode_no_raise(self, db, user, settings):
        """Stub mode (token contains 'test') just logs."""
        settings.TELEGRAM_BOT_TOKEN = 'test-bot-token'
        n = Notification.objects.create(
            user=user,
            channel=Notification.Channel.TELEGRAM,
            title='T',
            body='B',
        )
        # No exception
        notifications_service._send_telegram(n)

    def test_send_sms_stub_mode_no_raise(self, db, user, settings):
        settings.SMS_BACKEND = 'console'
        user.phone_number = '+998901234567'
        user.save()
        n = Notification.objects.create(
            user=user,
            channel=Notification.Channel.SMS,
            title='T',
            body='B',
        )
        notifications_service._send_sms(n)

    def test_send_telegram_real_raises_not_implemented(self, db, user, settings):
        settings.TELEGRAM_BOT_TOKEN = 'real-production-token'
        n = Notification.objects.create(
            user=user,
            channel=Notification.Channel.TELEGRAM,
            title='T',
            body='B',
        )
        with pytest.raises(NotImplementedError):
            notifications_service._send_telegram(n)

    def test_send_sms_real_raises_not_implemented(self, db, user, settings):
        settings.SMS_BACKEND = 'playmobile'
        user.phone_number = '+998901234567'
        user.save()
        n = Notification.objects.create(
            user=user,
            channel=Notification.Channel.SMS,
            title='T',
            body='B',
        )
        with pytest.raises(NotImplementedError):
            notifications_service._send_sms(n)
