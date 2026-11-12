"""Security events: recording from existing paths (behaviour unchanged), alerting, dashboard."""

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from apps.ops.models import AlertState, SecurityEvent
from apps.ops.services import security as sec
from tests.integration._ops import client_for, jpost, staff_user

pytestmark = pytest.mark.integration
User = get_user_model()
T = SecurityEvent.Type

LOGIN = '/api/auth/login/'


@pytest.fixture
def alert_spy():
    with patch('apps.ops.services.alerts.alert_admin') as spy:
        yield spy


@pytest.fixture(autouse=True)
def clean_cache():
    from django.core.cache import cache

    cache.clear()


def _login(username, password='wrong-password-1', ip='203.0.113.9'):
    return Client().post(
        LOGIN,
        data=json.dumps({'username': username, 'password': password}),
        content_type='application/json',
        REMOTE_ADDR=ip,
    )


class TestRecordingFromExistingPaths:
    def test_failed_login_recorded_without_changing_response(self, db):
        User.objects.create_user(username='victim', password='Correct-Horse-9')
        resp = _login('victim')
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'invalid_credentials'
        ev = SecurityEvent.objects.get(event_type=T.LOGIN_FAILED)
        assert ev.source == 'ip:203.0.113.9' and ev.user.username == 'victim'
        assert ev.path == LOGIN
        assert 'wrong-password' not in ev.reason and 'wrong-password' not in ev.path

    def test_unknown_user_failure_recorded_without_target(self, db):
        _login('nobody-here')
        ev = SecurityEvent.objects.get(event_type=T.LOGIN_FAILED)
        assert ev.user is None

    def test_successful_login_records_nothing(self, db):
        User.objects.create_user(username='ok', password='Correct-Horse-9')
        assert _login('ok', 'Correct-Horse-9').status_code == 200
        assert SecurityEvent.objects.count() == 0

    def test_lockout_recorded(self, db):
        User.objects.create_user(username='lockme', password='Correct-Horse-9')
        for _ in range(5):
            _login('lockme')
        assert SecurityEvent.objects.filter(event_type=T.LOGIN_FAILED).count() == 5
        assert SecurityEvent.objects.filter(event_type=T.ACCOUNT_LOCKED).count() == 1

    def test_rate_limit_429_recorded_and_sampled(self, db):
        codes = [_login('nobody', ip='198.51.100.7').status_code for _ in range(12)]
        assert 429 in codes  # behaviour unchanged
        rows = SecurityEvent.objects.filter(event_type=T.RATE_LIMITED)
        assert rows.count() == 1  # sampled: one row per source+path per window
        assert rows[0].source == 'ip:198.51.100.7' and rows[0].reason.startswith('tier=')

    def test_staff_bypassing_rate_limit_records_nothing(self, db):
        staff = staff_user()
        c = client_for(staff)
        for _ in range(8):
            c.get('/api/v1/status/')
        assert not SecurityEvent.objects.filter(event_type=T.RATE_LIMITED).exists()

    def test_recovery_requests_recorded(self, db):
        c = Client()
        for url, reason in (
            ('/api/auth/recovery/telegram/', 'telegram'),
            ('/api/auth/recovery/request/', 'admin_queue'),
        ):
            r = c.post(
                url,
                data=json.dumps({'username': 'someone', 'contact': 'x'}),
                content_type='application/json',
            )
            assert r.status_code == 202
            assert (
                SecurityEvent.objects.filter(event_type=T.RECOVERY_REQUEST, reason=reason).count()
                == 1
            )

    def test_password_reset_recorded(self, db):
        from apps.accounts.services import recovery

        u = User.objects.create_user(username='pw', password='Correct-Horse-9')
        recovery.set_temporary_password(u)
        ev = SecurityEvent.objects.get(event_type=T.PASSWORD_RESET)
        assert ev.user_id == u.pk and ev.reason == 'admin_temporary_password'

    def test_payment_mismatch_recorded(self, db, user):
        from django.urls import reverse

        from apps.commerce.models import PaymentIntent

        PaymentIntent.objects.create(
            user=user,
            provider='click',
            amount_uzs=Decimal('10000'),
            coins_to_credit=10,
            provider_tx_id='tx-mm',
            status=PaymentIntent.Status.PROCESSING,
        )
        resp = Client().post(
            reverse('commerce:click-webhook'),
            data={'payment_id': 'tx-mm', 'status': 1, 'amount': 5},
            content_type='application/json',
            HTTP_X_SIGNATURE='sig',
        )
        assert resp.status_code == 400
        ev = SecurityEvent.objects.get(event_type=T.PAYMENT_MISMATCH)
        assert ev.user_id == user.pk and 'click' in ev.reason

    def test_recording_failure_never_breaks_the_caller(self, db):
        with patch.object(SecurityEvent.objects, 'create', side_effect=RuntimeError('db down')):
            User.objects.create_user(username='v', password='Correct-Horse-9')
            resp = _login('v')
        assert resp.status_code == 400  # login flow unaffected
        assert SecurityEvent.objects.count() == 0


class TestAlerting:
    def _events(self, n, source='ip:1.2.3.4', age=timedelta(0), kind=T.LOGIN_FAILED):
        for _ in range(n):
            sec.record(kind, ip=source.split(':', 1)[1], path='/x')
        if age:
            SecurityEvent.objects.update(created_at=timezone.now() - age)

    def test_below_threshold_no_alert(self, db, alert_spy):
        self._events(11)
        alert_spy.assert_not_called()

    def test_threshold_alerts_once_per_source_per_hour(self, db, alert_spy):
        self._events(12)
        assert alert_spy.call_count == 1
        assert '1.2.3.4' in alert_spy.call_args[0][0]
        self._events(10)  # keeps firing events, still one alert
        assert alert_spy.call_count == 1
        # after the cooldown passes the source may alert again
        AlertState.objects.filter(key='sec:ip:1.2.3.4').update(
            updated_at=timezone.now() - timedelta(hours=2)
        )
        sec.record(T.LOGIN_FAILED, ip='1.2.3.4', path='/x')
        assert alert_spy.call_count == 2

    def test_sources_are_independent(self, db, alert_spy):
        self._events(12, 'ip:1.2.3.4')
        self._events(12, 'ip:5.6.7.8')
        assert alert_spy.call_count == 2

    def test_events_spread_over_sources_do_not_alert(self, db, alert_spy):
        for i in range(30):
            sec.record(T.LOGIN_FAILED, ip=f'10.1.0.{i}', path='/x')
        alert_spy.assert_not_called()

    def test_old_events_outside_window_do_not_count(self, db, alert_spy):
        self._events(11, age=timedelta(minutes=11))
        sec.record(T.LOGIN_FAILED, ip='1.2.3.4', path='/x')
        alert_spy.assert_not_called()

    def test_targeted_account_alerts_even_from_many_ips(self, db, alert_spy):
        victim = User.objects.create_user(username='target', password='Correct-Horse-9')
        for i in range(12):
            sec.record(T.LOGIN_FAILED, ip=f'10.2.0.{i}', user=victim)
        assert alert_spy.call_count == 1
        assert f'user:{victim.pk}' in alert_spy.call_args[0][0]

    def test_invalid_ip_is_not_stored_or_trusted(self, db):
        ev = sec.record(T.LOGIN_FAILED, ip='<b>x</b>', path='/x')
        assert ev.ip is None and ev.source == 'ip:unknown'

    def test_alert_text_is_html_escaped(self):
        from apps.ops.services import alerts

        assert alerts.esc('<b>x</b>') == '&lt;b&gt;x&lt;/b&gt;'


class TestDeliveryHook:
    def test_alert_goes_to_admin_chats_through_the_bot(self, db, settings):
        from apps.ops.models import BotSettings
        from apps.ops.services import alerts, bot_config

        row = BotSettings.load()
        row.admin_chat_ids = [111, -222]
        row.save()
        bot_config.invalidate()
        with patch('apps.accounts.services.bot_sender.send_message', return_value=True) as send:
            alerts.alert_admin('hello')  # celery is eager in tests
        assert [c.args[0] for c in send.call_args_list] == [111, -222]

    def test_no_admin_chats_means_no_send(self, db, settings):
        from apps.ops.services import alerts

        settings.OPS_ADMIN_CHAT_IDS = []
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            alerts.alert_admin('hello')
        send.assert_not_called()


class TestDashboard:
    URL = '/api/v1/ops/security/'

    def test_permissions(self, db, user):
        assert Client().get(self.URL).status_code in (401, 403)
        assert client_for(user).get(self.URL).status_code == 403

    def test_counts_top_sources_latest_and_ranges(self, db):
        staff = staff_user()
        for _ in range(3):
            sec.record(
                T.LOGIN_FAILED, ip='9.9.9.9', path='/api/auth/login/', reason='bad_credentials'
            )
        sec.record(T.PAYMENT_MISMATCH, ip='8.8.8.8', path='/webhook/', reason='click')
        old = sec.record(T.BOT_HIT, ip='7.7.7.7', path='/scrape')
        SecurityEvent.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        c = client_for(staff)

        day = c.get(self.URL).json()
        assert day['range'] == '24h' and day['total'] == 4
        assert day['by_type']['login_failed'] == 3 and day['by_type']['bot_hit'] == 0
        assert day['top_sources'][0] == {
            'source': 'ip:9.9.9.9',
            'count': 3,
            'last_seen': day['top_sources'][0]['last_seen'],
        }
        mismatch = next(e for e in day['latest'] if e['type'] == 'payment_mismatch')
        assert mismatch['path'] == '/webhook/' and mismatch['reason'] == 'click'

        week = c.get(self.URL, {'range': '7d'}).json()
        assert week['total'] == 5 and week['by_type']['bot_hit'] == 1

    def test_invalid_range_rejected(self, db):
        assert client_for(staff_user()).get(self.URL, {'range': '1y'}).status_code == 400
