"""Audit F08: the SMS gateway breaker fails fast during an outage and recovers."""

import pytest
from django.core.cache import cache

from core.circuit_breaker import CircuitBreaker


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _breaker(**kw):
    return CircuitBreaker('test', threshold=3, window=60, cooldown=30, **kw)


class TestCircuitBreaker:
    def test_closed_by_default(self):
        assert _breaker().allow()

    def test_opens_after_threshold_failures(self):
        b = _breaker()
        for _ in range(2):
            b.record_failure()
        assert b.allow()  # below threshold
        b.record_failure()
        assert not b.allow()

    def test_success_resets_the_count(self):
        b = _breaker()
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()
        b.record_failure()
        assert b.allow()

    def test_half_open_lets_exactly_one_probe(self):
        b = _breaker()
        for _ in range(3):
            b.record_failure()
        cache.delete('cb:test:open')  # cooldown elapsed
        assert b.allow()  # the probe
        assert not b.allow()  # everyone else waits for it

    def test_successful_probe_closes(self):
        b = _breaker()
        for _ in range(3):
            b.record_failure()
        cache.delete('cb:test:open')
        assert b.allow()
        b.record_success()
        assert b.allow()
        assert b.allow()

    def test_failed_probe_reopens(self):
        b = _breaker()
        for _ in range(3):
            b.record_failure()
        cache.delete('cb:test:open')
        assert b.allow()
        b.record_failure()
        assert not b.allow()

    def test_breakers_are_independent(self):
        a = CircuitBreaker('a', threshold=1)
        a.record_failure()
        assert not a.allow()
        assert CircuitBreaker('b', threshold=1).allow()


class TestSendOtpUsesBreaker:
    @pytest.fixture
    def failing_backend(self, monkeypatch):
        calls = []

        class Down:
            def send(self, phone, text):
                calls.append(phone)
                return False

        from apps.accounts.services import otp_service

        monkeypatch.setattr(otp_service, 'get_sms_backend', lambda: Down())
        return calls

    def test_outage_stops_calling_the_gateway(self, db, failing_backend, mock_redis):
        from apps.accounts.models import OTPCode
        from apps.accounts.services.otp_service import OTPError, send_otp

        phones = [f'+99890123450{i}' for i in range(7)]
        for p in phones[:5]:
            with pytest.raises(OTPError):
                send_otp(p, ip=f'10.0.0.{len(failing_backend) + 1}')
        assert len(failing_backend) == 5

        with pytest.raises(OTPError, match='vaqtincha'):
            send_otp(phones[5], ip='10.0.1.1')
        assert len(failing_backend) == 5  # failed fast, no gateway call
        assert not OTPCode.objects.filter(phone=phones[5]).exists()

    def test_healthy_gateway_unaffected(self, db, mock_sms_backend, mock_redis):
        from apps.accounts.services.otp_service import send_otp

        for i in range(6):
            send_otp(f'+99890765432{i}', ip=f'10.0.2.{i}')
        assert len(mock_sms_backend) == 6
