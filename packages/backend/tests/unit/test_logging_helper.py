"""ISSUE-X06 — Unit tests for core.logging.log_event structured helper.

caplog fixture'ni ishlatamiz — Python logging stack'dan barcha LogRecord'larni
ushlaydi, formatter konfiguratsiyasidan qat'i nazar (test settings'da
disable_existing_loggers=True bo'lsa ham caplog handler propagation orqali
ishlaydi).
"""

import logging

import pytest

from core.logging import EVENT_LOGGER_NAME, log_event


@pytest.mark.unit
class TestLogEvent:
    """Structured logging helper — log_event(level, event, **fields)."""

    def test_emits_at_correct_level(self, caplog):
        """log_event('warning', …) → LogRecord.levelno == WARNING."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            log_event('warning', 'rate_limit_tripped', ip='1.2.3.4')

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.WARNING
        assert record.levelname == 'WARNING'

    def test_event_field_in_extra(self, caplog):
        """'event' key landed on LogRecord (via extra=)."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            log_event('info', 'exam_submitted', user_id='u-123')

        record = caplog.records[0]
        # extra= keys become attributes on the LogRecord
        assert getattr(record, 'event', None) == 'exam_submitted'
        # message also equals event name (plain-text grep convenience)
        assert record.getMessage() == 'exam_submitted'

    def test_arbitrary_fields_pass_through(self, caplog):
        """**fields land as LogRecord attributes (top-level JSON keys in prod)."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            log_event(
                'info',
                'payment_succeeded',
                user_id='u-1',
                org_id='o-1',
                amount=15000,
                currency='UZS',
                idempotency_key='abc-xyz',
            )

        record = caplog.records[0]
        assert record.user_id == 'u-1'
        assert record.org_id == 'o-1'
        assert record.amount == 15000
        assert record.currency == 'UZS'
        assert record.idempotency_key == 'abc-xyz'

    def test_invalid_level_falls_back_to_info(self, caplog):
        """Unknown level string → INFO (don't crash, don't drop the event)."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            # 'verbose' is not in _LEVEL_MAP
            log_event('verbose', 'cache_warmed', keys=10)  # type: ignore[arg-type]

        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.INFO

    def test_logger_name_is_yuzdanyuz_events(self, caplog):
        """Records emitted on the canonical 'yuzdanyuz.events' logger."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            log_event('error', 'webhook_signature_invalid', provider='click')

        record = caplog.records[0]
        assert record.name == 'yuzdanyuz.events'
        assert EVENT_LOGGER_NAME == 'yuzdanyuz.events'

    def test_level_string_is_case_insensitive(self, caplog):
        """'INFO' / 'Info' / 'info' all accepted (defensive)."""
        with caplog.at_level(logging.DEBUG, logger=EVENT_LOGGER_NAME):
            log_event('INFO', 'mixed_case_level')  # type: ignore[arg-type]

        assert caplog.records[0].levelno == logging.INFO
