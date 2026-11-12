"""G01 — request id + structured access log + Sentry scope."""

import json
import logging
import re
from unittest.mock import patch

import pytest
from django.test import Client, RequestFactory

from core.middleware import request_id as rid
from tests.logging_snapshot import prod_json_logging

HEX32 = re.compile(r'^[0-9a-f]{32}$')


@pytest.mark.unit
class TestRequestIdHeader:
    def test_generated_when_missing(self, db):
        resp = Client().get('/health/')
        assert HEX32.match(resp['X-Request-ID'])

    def test_valid_incoming_id_is_echoed(self, db):
        resp = Client().get('/health/', HTTP_X_REQUEST_ID='abc-123.DEF_456')
        assert resp['X-Request-ID'] == 'abc-123.DEF_456'

    @pytest.mark.parametrize(
        'bad', ['x' * 129, 'has space', 'new\nline', '<script>', 'semi;colon', '']
    )
    def test_invalid_incoming_id_is_replaced(self, db, bad):
        resp = Client().get('/health/', HTTP_X_REQUEST_ID=bad)
        assert HEX32.match(resp['X-Request-ID'])

    def test_each_request_gets_its_own_id(self, db):
        c = Client()
        assert c.get('/health/')['X-Request-ID'] != c.get('/health/')['X-Request-ID']

    def test_context_is_cleared_after_request(self, db):
        Client().get('/health/', HTTP_X_REQUEST_ID='leftover-id')
        assert rid.get_request_id() is None


@pytest.mark.unit
class TestAccessLog:
    def test_access_record_has_required_fields(self, db, caplog):
        with caplog.at_level(logging.INFO, logger=rid.ACCESS_LOGGER_NAME):
            Client().get('/ping/?token=secret', HTTP_X_REQUEST_ID='req-1')
        records = [r for r in caplog.records if r.name == rid.ACCESS_LOGGER_NAME]
        assert len(records) == 1
        r = records[0]
        assert r.request_id == 'req-1'
        assert r.status == 200
        assert r.method == 'GET'
        assert r.path == '/ping/'  # query string is never logged
        assert isinstance(r.duration_ms, float) and r.duration_ms >= 0
        assert r.user_id is None

    def test_authenticated_user_id_is_logged(self, db, caplog, user):
        c = Client()
        c.force_login(user)
        with caplog.at_level(logging.INFO, logger=rid.ACCESS_LOGGER_NAME):
            c.get('/ping/')
        r = [r for r in caplog.records if r.name == rid.ACCESS_LOGGER_NAME][0]
        assert r.user_id == str(user.pk)

    def test_prod_json_access_line(self, db):
        with prod_json_logging() as buf:
            Client().get('/ping/', HTTP_X_REQUEST_ID='json-1')
        lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        access = [x for x in lines if x.get('name') == rid.ACCESS_LOGGER_NAME]
        assert len(access) == 1
        a = access[0]
        for key in ('request_id', 'status', 'duration_ms', 'user_id', 'method', 'path', 'level'):
            assert key in a, key
        assert a['request_id'] == 'json-1'

    def test_other_log_lines_carry_request_id(self, db):
        with prod_json_logging() as buf:
            token = rid._request_id.set('ctx-7')
            try:
                logging.getLogger('apps.whatever').warning('inside a request')
            finally:
                rid._request_id.reset(token)
        line = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert line['request_id'] == 'ctx-7'


@pytest.mark.unit
class TestSentryScope:
    def test_request_id_goes_to_sentry(self, db):
        with patch('core.middleware.request_id.sentry_sdk.set_tag') as set_tag:
            Client().get('/health/', HTTP_X_REQUEST_ID='sentry-1')
        set_tag.assert_any_call('request_id', 'sentry-1')

    def test_user_id_goes_to_sentry_before_the_view(self, user):
        request = RequestFactory().get('/x/')
        request.user = user
        mw = rid.RequestIdMiddleware(lambda r: None)
        with patch('core.middleware.request_id.sentry_sdk.set_user') as set_user:
            assert mw.process_view(request, lambda r: None, (), {}) is None
        set_user.assert_called_once_with({'id': str(user.pk)})

    def test_anonymous_user_is_not_sent(self):
        from django.contrib.auth.models import AnonymousUser

        request = RequestFactory().get('/x/')
        request.user = AnonymousUser()
        mw = rid.RequestIdMiddleware(lambda r: None)
        with patch('core.middleware.request_id.sentry_sdk.set_user') as set_user:
            mw.process_view(request, lambda r: None, (), {})
        set_user.assert_not_called()
