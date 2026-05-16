"""ISSUE-501 — ClickHouse client tests (stub + real-mode error swallow)."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from apps.analytics import clickhouse_client


@pytest.mark.unit
class TestClickHouseStub:
    def setup_method(self):
        clickhouse_client._reset_client()

    def test_default_is_stub_mode(self):
        assert clickhouse_client.is_real_mode() is False

    def test_stub_write_is_noop(self):
        # Should not raise, should not require clickhouse-driver
        clickhouse_client.write_event({'score': 92.5, 'user_id': 'x'})

    def test_stub_query_returns_empty_list(self):
        result = clickhouse_client.query_aggregation('SELECT 1')
        assert result == []

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='')
    def test_enabled_without_dsn_stays_stub(self):
        # is_real_mode requires BOTH flag + DSN
        assert clickhouse_client.is_real_mode() is False
        clickhouse_client.write_event({'a': 1})  # still no-op

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='clickhouse://localhost:9000/test')
    def test_enabled_with_dsn_attempts_real(self):
        assert clickhouse_client.is_real_mode() is True


@pytest.mark.unit
class TestClickHouseRealMode:
    def setup_method(self):
        clickhouse_client._reset_client()

    def teardown_method(self):
        clickhouse_client._reset_client()

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='clickhouse://localhost:9000/test')
    def test_write_event_calls_client_execute(self):
        mock_client = MagicMock()
        with patch.object(clickhouse_client, '_get_client', return_value=mock_client):
            clickhouse_client.write_event({'subject_id': 'x', 'score': 80})
        mock_client.execute.assert_called_once()
        call_args = mock_client.execute.call_args
        assert 'INSERT INTO' in call_args[0][0]
        assert call_args[0][1] == [{'subject_id': 'x', 'score': 80}]

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='clickhouse://localhost:9000/test')
    def test_write_event_swallows_exception(self):
        # Network/CH down → no exception propagated (PG remains source of truth)
        mock_client = MagicMock()
        mock_client.execute.side_effect = ConnectionError('CH unreachable')
        with patch.object(clickhouse_client, '_get_client', return_value=mock_client):
            clickhouse_client.write_event({'a': 1})  # no raise

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='clickhouse://localhost:9000/test')
    def test_query_aggregation_returns_dict_rows(self):
        mock_client = MagicMock()
        mock_client.execute.return_value = (
            [('Mat', 78.5), ('Phys', 85.0)],
            [('subject', 'String'), ('avg_score', 'Float64')],
        )
        with patch.object(clickhouse_client, '_get_client', return_value=mock_client):
            result = clickhouse_client.query_aggregation('SELECT subject, avg(score) ...')
        assert result == [
            {'subject': 'Mat', 'avg_score': 78.5},
            {'subject': 'Phys', 'avg_score': 85.0},
        ]

    @override_settings(CLICKHOUSE_ENABLED=True, CLICKHOUSE_DSN='clickhouse://localhost:9000/test')
    def test_query_aggregation_swallows_and_returns_empty(self):
        mock_client = MagicMock()
        mock_client.execute.side_effect = TimeoutError('CH slow')
        with patch.object(clickhouse_client, '_get_client', return_value=mock_client):
            result = clickhouse_client.query_aggregation('SELECT 1')
        assert result == []
