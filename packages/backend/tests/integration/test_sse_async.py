"""F17: the exam SSE stream runs on the ASGI service and never blocks a worker thread.

The old view was a sync generator that slept 5 s per tick for up to 240 s inside a
gunicorn thread (8 threads per pod), so a handful of exam takers froze the API.
"""

import asyncio
import inspect
import re
from datetime import timedelta
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient
from django.utils import timezone

from apps.exams import sse_views
from apps.exams.models import ExamAttempt, MockExam
from core.settings import base
from core.tenant import tenant_context, unscoped_context

NGINX_CONF = Path(base.BASE_DIR).parent.parent / 'docker' / 'nginx' / 'default.conf'
SSE_PATHS = [
    '/api/v1/exams/attempts/7b1c8f3e-2d4a-4c55-9a5e-0d6f1b2c3a4e/events/',
    '/api/exams/attempts/7b1c8f3e-2d4a-4c55-9a5e-0d6f1b2c3a4e/events/',
]


def test_event_stream_is_an_async_view():
    assert inspect.iscoroutinefunction(sse_views.attempt_event_stream)


@pytest.mark.parametrize('path', SSE_PATHS)
def test_nginx_routes_sse_to_the_asgi_service(path):
    conf = NGINX_CONF.read_text()
    blocks = re.findall(r'location ~\*? "?([^"{]+?)"? \{(.*?)\n    \}', conf, re.S)
    for pattern, body in blocks:
        if re.search(pattern.strip(), path):
            assert 'proxy_pass http://django_asgi' in body
            assert 'proxy_buffering off' in body
            return
    pytest.fail(f'no nginx location sends {path} to django_asgi')


def _make_attempt(org, user, subject, *, minutes_left=30):
    with tenant_context(org):
        now = timezone.now()
        mock = MockExam.objects.create(
            organization=org,
            title='SSE mock',
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=1),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            created_by=user,
        )
        return ExamAttempt.objects.create(
            organization=org,
            exam=mock,
            user=user,
            status=ExamAttempt.Status.IN_PROGRESS,
            started_at=now - timedelta(minutes=60 - minutes_left),
        )


def _parse(chunks):
    events = []
    for raw in ''.join(chunks).split('\n\n'):
        if not raw.strip():
            continue
        fields = dict(line.split(': ', 1) for line in raw.splitlines() if ': ' in line)
        events.append(fields)
    return events


async def _collect(response):
    chunks = []
    async for chunk in response.streaming_content:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return chunks


@pytest.mark.django_db(transaction=True)
async def test_stream_keeps_the_event_loop_free(org, user, member, subject, monkeypatch):
    monkeypatch.setattr(sse_views, '_TIMER_PUSH_INTERVAL', 0.05)
    monkeypatch.setattr(sse_views, '_STREAM_MAX_DURATION', 0.4)
    attempt = await sync_to_async(_make_attempt)(org, user, subject)

    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(f'/api/v1/exams/attempts/{attempt.id}/events/')
    assert response.status_code == 200
    assert response['Content-Type'] == 'text/event-stream'
    assert response.is_async, 'SSE must stream from an async iterator on ASGI'

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    t = asyncio.create_task(ticker())
    try:
        chunks = await asyncio.wait_for(_collect(response), timeout=5)
    finally:
        t.cancel()

    events = _parse(chunks)
    # client contract: first event is an immediate timer with id 1 and retry 3000
    assert events[0]['event'] == 'timer'
    assert events[0]['id'] == '1'
    assert events[0]['retry'] == '3000'
    assert len([e for e in events if e['event'] == 'timer']) >= 3
    # the loop kept running other coroutines while the stream waited
    assert ticks >= 15, ticks


@pytest.mark.django_db(transaction=True)
async def test_stream_reports_cancellation(org, user, member, subject, monkeypatch):
    monkeypatch.setattr(sse_views, '_TIMER_PUSH_INTERVAL', 0.05)
    monkeypatch.setattr(sse_views, '_STREAM_MAX_DURATION', 5)
    attempt = await sync_to_async(_make_attempt)(org, user, subject)

    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(f'/api/v1/exams/attempts/{attempt.id}/events/')

    def cancel():
        with unscoped_context():
            ExamAttempt.objects.filter(pk=attempt.pk).update(
                status=ExamAttempt.Status.CANCELLED,
                cancel_reason=ExamAttempt.CancelReason.TAB_SWITCH,
            )

    await sync_to_async(cancel)()
    events = _parse(await asyncio.wait_for(_collect(response), timeout=5))
    assert events[-1]['event'] == 'cancelled'
    assert 'tab_switch' in events[-1]['data']


@pytest.mark.django_db(transaction=True)
async def test_stream_refuses_other_users_attempt(org, user, user2, member, subject):
    attempt = await sync_to_async(_make_attempt)(org, user, subject)
    client = AsyncClient()
    await client.aforce_login(user2)
    response = await client.get(f'/api/v1/exams/attempts/{attempt.id}/events/')
    assert response.status_code in (403, 404)


@pytest.mark.django_db
def test_wsgi_fallback_returns_at_once(org, user, member, subject):
    """If the nginx route is missing, gunicorn answers one event and frees the thread."""
    from django.test import Client

    attempt = _make_attempt(org, user, subject)
    client = Client()
    client.force_login(user)
    response = client.get(f'/api/v1/exams/attempts/{attempt.id}/events/')
    assert response.status_code == 200
    events = _parse([c.decode() for c in response.streaming_content])
    assert [e['event'] for e in events] == ['timer']
    assert events[0]['retry'] == '3000'
