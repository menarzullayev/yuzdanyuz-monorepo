"""Status page: public payload, staff publishing, IP rejection, 90-day uptime, probe alerts."""

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.ops.models import AlertState, ProbeResult, StatusIncident
from apps.ops.services import probes
from apps.ops.services.status import TASHKENT, contains_ip_address, uptime_days
from tests.integration._ops import client_for, jpatch, jpost, staff_user

pytestmark = pytest.mark.integration

STATUS = '/api/v1/status/'
INCIDENTS = '/api/v1/ops/incidents/'


@pytest.fixture
def staff(db):
    return staff_user()


class TestPublicStatus:
    def test_anonymous_can_read(self, db):
        from django.test import Client

        resp = Client().get(STATUS)
        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'operational'
        assert body['timezone'] == 'Asia/Tashkent'
        assert len(body['uptime']['days']) == 90

    def test_read_only(self, db):
        from django.test import Client

        assert Client().post(STATUS, data={}).status_code in (401, 403, 405)

    def test_major_incident_sets_major_outage_and_resolved_does_not(self, db):
        inc = StatusIncident.objects.create(title='DB slow', impact='major')
        from django.test import Client

        assert Client().get(STATUS).json()['status'] == 'major_outage'
        inc.resolved_at = timezone.now()
        inc.status = 'resolved'
        inc.save()
        body = Client().get(STATUS).json()
        assert body['status'] == 'operational'
        assert [i['title'] for i in body['recent_incidents']] == ['DB slow']

    def test_partial_incident(self, db):
        StatusIncident.objects.create(title='x', impact='partial')
        from django.test import Client

        assert Client().get(STATUS).json()['status'] == 'partial_outage'

    def test_active_maintenance_vs_future_maintenance(self, db):
        from django.test import Client

        now = timezone.now()
        StatusIncident.objects.create(
            kind='maintenance',
            title='Future',
            started_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=1, hours=1),
        )
        body = Client().get(STATUS).json()
        assert body['status'] == 'operational'
        assert [m['title'] for m in body['maintenance']] == ['Future']
        StatusIncident.objects.create(
            kind='maintenance', title='Now', started_at=now - timedelta(minutes=5)
        )
        assert Client().get(STATUS).json()['status'] == 'maintenance'

    def test_times_are_tashkent(self, db):
        from django.test import Client

        StatusIncident.objects.create(title='tz')
        started = Client().get(STATUS).json()['incidents'][0]['started_at']
        assert started.endswith('+05:00')

    def test_failed_latest_probe_means_degraded(self, db):
        from django.test import Client

        ProbeResult.objects.create(ok=False, db_ok=False, redis_ok=True)
        assert Client().get(STATUS).json()['status'] == 'degraded'

    def test_no_internal_details_in_public_payload(self, db):
        from django.test import Client

        ProbeResult.objects.create(ok=True, db_ok=True, redis_ok=True, db_ms=12, redis_ms=3)
        text = Client().get(STATUS).content.decode()
        for leaked in ('db_ms', 'redis', 'latency', 'database'):
            assert leaked not in text


class TestStaffPublishing:
    def test_anonymous_and_non_staff_refused(self, db, user):
        from django.test import Client

        assert Client().post(INCIDENTS, {}).status_code in (401, 403)
        resp = jpost(client_for(user), INCIDENTS, {'title': 't', 'message': 'm'})
        assert resp.status_code == 403
        assert StatusIncident.objects.count() == 0

    def test_create_update_resolve_flow(self, staff):
        c = client_for(staff)
        resp = jpost(c, INCIDENTS, {'title': 'Slow API', 'impact': 'partial', 'message': 'Looking'})
        assert resp.status_code == 201, resp.content
        iid = resp.json()['id']
        url = reverse('ops:incident-update', args=[iid])
        r = jpost(c, url, {'status': 'identified', 'message': 'Found it'})
        assert r.status_code == 201 and r.json()['status'] == 'identified'
        assert r.json()['resolved_at'] is None
        r = jpost(c, url, {'status': 'resolved', 'message': 'Fixed'})
        body = r.json()
        assert body['resolved_at'] is not None
        assert [u['status'] for u in body['updates']] == ['investigating', 'identified', 'resolved']
        # reopening clears the resolution
        r = jpost(c, url, {'status': 'monitoring', 'message': 'Back'})
        assert r.json()['resolved_at'] is None

    @pytest.mark.parametrize(
        'text',
        [
            'Server 10.0.0.5 is down',
            'host 192.168.1.1 unreachable',
            'v6 2001:db8::1 failed',
            'loopback ::1 refused',
        ],
    )
    def test_ip_addresses_rejected_in_title_and_messages(self, staff, text):
        c = client_for(staff)
        assert jpost(c, INCIDENTS, {'title': text, 'message': 'ok'}).status_code == 400
        assert jpost(c, INCIDENTS, {'title': 'ok', 'message': text}).status_code == 400
        assert StatusIncident.objects.count() == 0
        iid = jpost(c, INCIDENTS, {'title': 'ok', 'message': 'ok'}).json()['id']
        r = jpost(
            c, reverse('ops:incident-update', args=[iid]), {'status': 'identified', 'message': text}
        )
        assert r.status_code == 400
        assert (
            jpatch(c, reverse('ops:incident-detail', args=[iid]), {'title': text}).status_code
            == 400
        )

    @pytest.mark.parametrize(
        'text', ['Maintenance at 12:30:45 local', 'Version 1.2.3 rollout', 'Slow API']
    )
    def test_normal_text_is_accepted(self, staff, text):
        assert not contains_ip_address(text)
        r = jpost(client_for(staff), INCIDENTS, {'title': text, 'message': 'm'})
        assert r.status_code == 201

    def test_validation_errors(self, staff):
        c = client_for(staff)
        base = {'title': 't', 'message': 'm'}
        for bad in (
            {'impact': 'huge'},
            {'status': 'weird'},
            {'kind': 'x'},
            {'title': ' '},
            {'started_at': '2026-01-01T00:00:00'},
        ):
            assert jpost(c, INCIDENTS, {**base, **bad}).status_code == 400, bad
        now = timezone.now()
        r = jpost(
            c,
            INCIDENTS,
            {
                **base,
                'kind': 'maintenance',
                'started_at': now.isoformat(),
                'ends_at': (now - timedelta(hours=1)).isoformat(),
            },
        )
        assert r.status_code == 400

    def test_unknown_incident_404(self, staff):
        import uuid

        c = client_for(staff)
        r = jpost(
            c,
            reverse('ops:incident-update', args=[uuid.uuid4()]),
            {'status': 'resolved', 'message': 'm'},
        )
        assert r.status_code == 404

    def test_probe_list_is_staff_only(self, staff, user):
        ProbeResult.objects.create(ok=True, db_ok=True, redis_ok=True, db_ms=5, redis_ms=2)
        assert client_for(user).get('/api/v1/ops/probes/').status_code == 403
        rows = client_for(staff).get('/api/v1/ops/probes/').json()
        assert rows[0]['db_ms'] == 5


# ── 90-day uptime ───────────────────────────────────────────

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=TASHKENT)


def _probe(at, ok=True):
    ProbeResult.objects.create(checked_at=at, ok=ok, db_ok=ok, redis_ok=True)


class TestUptime:
    def test_missing_probes_count_as_downtime(self, db):
        day = datetime.combine(datetime(2026, 11, 10).date(), time.min, tzinfo=TASHKENT)
        for i in range(144):  # half of the 288 expected
            _probe(day + timedelta(minutes=5 * i))
        days = {d['date']: d['uptime_pct'] for d in uptime_days(NOW)['days']}
        assert days['2026-11-10'] == 50.0
        assert days['2026-11-11'] == 0.0  # no probes at all after the first one: full downtime
        assert days['2026-11-09'] is None  # before the first probe: no data
        # today is only expected until "now" (12:00 -> 145 probes), none recorded yet
        assert days['2026-11-12'] == 0.0

    def test_failed_probes_do_not_count_as_up(self, db):
        day = datetime.combine(datetime(2026, 11, 11).date(), time.min, tzinfo=TASHKENT)
        for i in range(10):
            _probe(day + timedelta(minutes=5 * i), ok=True)
        for i in range(10, 20):
            _probe(day + timedelta(minutes=5 * i), ok=False)
        days = {d['date']: d['uptime_pct'] for d in uptime_days(NOW)['days']}
        assert days['2026-11-11'] == round(100 * 10 / 288, 2)

    def test_full_day_is_100_and_capped(self, db):
        day = datetime.combine(datetime(2026, 11, 11).date(), time.min, tzinfo=TASHKENT)
        for i in range(300):  # more than 288 (jitter) must not exceed 100%
            _probe(day + timedelta(seconds=280 * i))
        days = {d['date']: d['uptime_pct'] for d in uptime_days(NOW)['days']}
        assert days['2026-11-11'] <= 100.0

    def test_no_probes_at_all_is_no_data(self, db):
        res = uptime_days(NOW)
        assert res['overall_pct'] is None
        assert all(d['uptime_pct'] is None for d in res['days'])

    def test_days_use_tashkent_midnight(self, db):
        # 23:58 Tashkent on the 10th is 18:58 UTC the same day; 00:02 on the 11th is 19:02 UTC.
        _probe(datetime(2026, 11, 10, 23, 58, tzinfo=TASHKENT))
        _probe(datetime(2026, 11, 11, 0, 2, tzinfo=TASHKENT))
        days = {d['date']: d['uptime_pct'] for d in uptime_days(NOW)['days']}
        assert days['2026-11-10'] > 0 and days['2026-11-11'] > 0


# ── probe task + alert de-duplication ───────────────────────


@pytest.fixture
def alert_spy():
    with patch('apps.ops.services.alerts.alert_admin') as spy:
        yield spy


def _set_health(monkeypatch, db_ok=True, redis_ok=True):
    from core import health

    monkeypatch.setattr(health, '_check_database', lambda: db_ok)
    monkeypatch.setattr(health, '_check_redis', lambda: redis_ok)


class TestProbeAlerts:
    def test_first_healthy_probe_is_silent(self, db, monkeypatch, alert_spy):
        _set_health(monkeypatch)
        p = probes.run_probe()
        assert p.ok and p.db_ms is not None and p.ai_ok is None
        alert_spy.assert_not_called()

    def test_alert_once_per_state_change_not_every_probe(self, db, monkeypatch, alert_spy):
        _set_health(monkeypatch)
        probes.run_probe()
        _set_health(monkeypatch, db_ok=False)
        for _ in range(4):  # down for 20 minutes
            probes.run_probe()
        assert alert_spy.call_count == 1
        assert 'Muammo' in alert_spy.call_args[0][0]
        _set_health(monkeypatch)
        for _ in range(3):
            probes.run_probe()
        assert alert_spy.call_count == 2
        assert 'Tiklandi' in alert_spy.call_args[0][0]

    def test_first_ever_probe_failing_alerts(self, db, monkeypatch, alert_spy):
        _set_health(monkeypatch, redis_ok=False)
        probes.run_probe()
        probes.run_probe()
        assert alert_spy.call_count == 1

    def test_ai_failure_is_not_downtime(self, db, monkeypatch, alert_spy, settings):
        from core import health

        settings.OPS_PROBE_AI = True
        _set_health(monkeypatch)
        monkeypatch.setattr(health, '_check_anthropic', lambda: False)
        p = probes.run_probe()
        assert p.ok is True and p.ai_ok is False
        alert_spy.assert_not_called()

    def test_ai_not_probed_by_default(self, db, monkeypatch, settings):
        from core import health

        settings.OPS_PROBE_AI = False
        _set_health(monkeypatch)
        called = []
        monkeypatch.setattr(health, '_check_anthropic', lambda: called.append(1) or True)
        probes.run_probe()
        assert called == []

    def test_celery_task_records_a_row(self, db, monkeypatch, alert_spy):
        from apps.ops.tasks import run_health_probe

        _set_health(monkeypatch)
        assert run_health_probe() == {'ok': True}
        assert ProbeResult.objects.count() == 1
        assert AlertState.objects.get(key='probe:state').value == 'up'

    def test_cleanup_task_keeps_recent(self, db):
        from apps.ops.tasks import cleanup_old_records

        _probe(timezone.now() - timedelta(days=200))
        _probe(timezone.now())
        assert cleanup_old_records()['probes'] == 1
        assert ProbeResult.objects.count() == 1
