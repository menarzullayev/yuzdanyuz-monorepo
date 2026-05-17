"""ISSUE-112 — `core.sse` helper tests."""

import json

import pytest

from core.sse import sse_format, sse_response

pytestmark = pytest.mark.unit


class TestSSEFormat:
    def test_basic_event(self):
        out = sse_format('timer', {'seconds_left': 60})
        assert out == 'event: timer\ndata: {"seconds_left": 60}\n\n'

    def test_with_event_id(self):
        out = sse_format('timer', {}, event_id=42)
        assert out.startswith('id: 42\n')
        assert out.endswith('\n\n')

    def test_with_retry(self):
        out = sse_format('ping', {}, retry_ms=3000)
        assert 'retry: 3000\n' in out

    def test_unicode_payload(self):
        out = sse_format('msg', {'text': "O'zbekiston"})
        data_line = next(line for line in out.split('\n') if line.startswith('data:'))
        payload = json.loads(data_line[len('data: ') :])
        assert payload['text'] == "O'zbekiston"

    def test_event_terminator(self):
        # SSE spec: messages must end with double newline
        out = sse_format('e', {})
        assert out.endswith('\n\n')


class TestSSEResponse:
    def test_headers(self):
        def gen():
            yield sse_format('init', {})

        resp = sse_response(gen())
        assert resp['Content-Type'] == 'text/event-stream'
        assert resp['Cache-Control'] == 'no-cache, no-transform'
        assert resp['X-Accel-Buffering'] == 'no'
        assert resp['Connection'] == 'keep-alive'
