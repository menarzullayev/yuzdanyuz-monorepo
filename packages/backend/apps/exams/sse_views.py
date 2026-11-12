"""ISSUE-112 Phase 1 — exam attempt SSE event stream + heartbeat.

WebSocket o'rniga (HestiaCP subpath constraint sababli) — SSE pattern:
  * `GET /api/v1/exams/attempts/<uuid>/events/`     — server-sent event stream
  * `POST /api/v1/exams/attempts/<uuid>/heartbeat/` — client→server polling

Channels consumer'lar (`apps/exams/consumers.py`) Phase 5'gacha back-compat sifatida
saqlanadi — frontend SSE'ga ko'chgandan keyin deprecate qilinadi.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext

from asgiref.sync import sync_to_async
from django.core.handlers.wsgi import WSGIRequest
from django.db import close_old_connections
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.sse import sse_format, sse_response
from core.tenant import tenant_context, unscoped_context

from .models import AntiCheatEvent, ExamAttempt

# Periodic timer interval (production'da 5s yetadi — frontend timer'i o'z-o'zi ham hisoblaydi).
_TIMER_PUSH_INTERVAL = 5

# Stream lifecycle uchun cheklov — nginx/ingress proxy_read_timeout 300s ichida.
# Bu vaqtdan keyin browser EventSource avtomatik reconnect qiladi (Last-Event-ID bilan davom).
_STREAM_MAX_DURATION = 240  # 4 daqiqa


def _seconds_left(attempt: ExamAttempt) -> int:
    """Imtihon yakuniga qancha sekund qolgan."""
    if not attempt.started_at:
        return 0
    elapsed = (timezone.now() - attempt.started_at).total_seconds()
    total = (attempt.exam.duration_minutes or 0) * 60
    return max(0, int(total - elapsed))


def _load_attempt(user, attempt_id) -> tuple[ExamAttempt | None, JsonResponse | None]:
    """Auth + ownership + status guard. Returns (attempt, None) or (None, error_response)."""
    if not user or not user.is_authenticated:
        return None, JsonResponse({'error': 'unauthorized'}, status=401)
    # SSE/heartbeat tenant context'siz chaqirilishi mumkin (mobile/anonymous-look),
    # shu sababli `unscoped_context()` + explicit `user=...` filter cross-tenant
    # leak yo'qligini ta'minlaydi.
    with unscoped_context():
        try:
            attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id)
        except ExamAttempt.DoesNotExist:
            return None, JsonResponse({'error': 'not_found'}, status=404)
        if attempt.user_id != user.id:
            return None, JsonResponse({'error': 'forbidden'}, status=403)
    return attempt, None


def _poll_attempt(attempt_pk, org) -> dict | None:
    """One status read for the stream, in a worker thread.

    Runs on the shared executor (not a per-request thread), so N open streams use a
    bounded number of threads and DB connections; close_old_connections() drops a
    connection that went stale between ticks. `org` re-enters the tenant context:
    the stream outlives TenantMiddleware, and RLS needs the org on each statement.
    """
    close_old_connections()
    try:
        with tenant_context(org) if org is not None else nullcontext(), unscoped_context():
            return (
                ExamAttempt.objects.filter(pk=attempt_pk)
                .values('status', 'cancel_reason', 'started_at', 'exam__duration_minutes')
                .first()
            )
    finally:
        close_old_connections()


def _row_seconds_left(row: dict) -> int:
    if not row['started_at']:
        return 0
    elapsed = (timezone.now() - row['started_at']).total_seconds()
    total = (row['exam__duration_minutes'] or 0) * 60
    return max(0, int(total - elapsed))


async def _event_stream(attempt: ExamAttempt, org) -> AsyncIterator[str]:
    # Initial timer event darhol jo'natiladi — frontend timer ko'rsatishi uchun.
    seconds = _seconds_left(attempt)
    yield sse_format('timer', {'seconds_left': seconds}, event_id=1, retry_ms=3000)
    if seconds <= 0:
        yield sse_format('expired', {}, event_id=2)
        return

    poll = sync_to_async(_poll_attempt, thread_sensitive=False)
    event_id = 2
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _STREAM_MAX_DURATION
    while loop.time() < deadline:
        # awaits instead of blocking: the event loop keeps serving other streams
        await asyncio.sleep(_TIMER_PUSH_INTERVAL)
        fresh = await poll(attempt.pk, org)
        if fresh is None:
            yield sse_format('error', {'message': 'attempt vanished'}, event_id=event_id)
            return
        seconds = _row_seconds_left(fresh)
        if seconds <= 0:
            yield sse_format('expired', {}, event_id=event_id)
            return
        if fresh['status'] == ExamAttempt.Status.CANCELLED:
            yield sse_format(
                'cancelled',
                {'reason': fresh['cancel_reason'] or 'unknown'},
                event_id=event_id,
            )
            return
        if fresh['status'] != ExamAttempt.Status.IN_PROGRESS:
            # Submitted yoki disputed — stream tugaydi
            yield sse_format('status_changed', {'status': fresh['status']}, event_id=event_id)
            return
        yield sse_format('timer', {'seconds_left': seconds}, event_id=event_id)
        event_id += 1


@csrf_exempt
@require_http_methods(['GET'])
async def attempt_event_stream(request, attempt_id):
    """SSE stream — server→client: timer, expired, cancelled, status_changed, error.

    Served by the ASGI (Daphne) service: nginx routes `.../events/` there (F17). An
    async view holds no worker thread while waiting, unlike the old sync generator
    that slept inside a gunicorn thread for up to 240 s.

    Authentication via existing JWTAuthMiddleware (cookie-based, see ISSUE-108).
    CSRF exempt — `GET` so'rov, va EventSource brauzeri default'da CSRF cookie yubormaydi.
    """
    # request.user is lazy and may hit the DB: resolve it in a thread
    attempt, error = await sync_to_async(_load_attempt)(request.user, attempt_id)
    if error is not None:
        return error
    if attempt.status != ExamAttempt.Status.IN_PROGRESS:
        return JsonResponse({'error': 'attempt_not_active', 'status': attempt.status}, status=409)
    if isinstance(request, WSGIRequest):
        # Reached gunicorn (nginx route missing): never hold a WSGI thread. Send the
        # first event and close; EventSource reconnects after `retry`.
        seconds = _seconds_left(attempt)
        first = [sse_format('timer', {'seconds_left': seconds}, event_id=1, retry_ms=3000)]
        if seconds <= 0:
            first.append(sse_format('expired', {}, event_id=2))
        return sse_response(iter(first))
    return sse_response(_event_stream(attempt, getattr(request, 'org', None)))


class HeartbeatView(APIView):
    """POST /api/v1/exams/attempts/<uuid>/heartbeat/

    Client 5-10 sek'da ping yuboradi. Server:
      * `heartbeat_last_at` yangilaydi (server-side detection: missed heartbeat → strike)
      * `seconds_left` qaytaradi (frontend timer drift correction)
      * `expired=true` agar vaqt tugagan bo'lsa (state mashinasi orqali EXPIRED)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, attempt_id):
        attempt, error = _load_attempt(request.user, attempt_id)
        if error is not None:
            return error
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            return Response(
                {'expired': True, 'seconds_left': 0, 'status': attempt.status}, status=409
            )

        seconds = _seconds_left(attempt)
        if seconds <= 0:
            # Stale state'ni darrov yakunlaymiz — fmsm sinov.
            with unscoped_context():
                attempt.expire()
            return Response({'expired': True, 'seconds_left': 0})

        with unscoped_context():
            attempt.heartbeat_last_at = timezone.now()
            attempt.save(update_fields=['heartbeat_last_at', 'updated_at'])
        return Response({'seconds_left': seconds, 'expired': False})


class AntiCheatSSEReportView(APIView):
    """POST /api/v1/exams/attempts/<uuid>/anticheat/

    NB: mavjud `AntiCheatEventView` (`apps/exams/views.py`) DRF-style allaqachon
    bor. Bu yangi varianti — SSE-flow uchun light-weight (frontend ham bir xil
    chaqirig'idan foydalanishi mumkin, response shape mos). Parallel coexistence
    Phase 5'gacha — `views.AntiCheatEventView` kelajak deprecate.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, attempt_id):
        from django.db import transaction

        from core.config import get_org_setting

        attempt, error = _load_attempt(request.user, attempt_id)
        if error is not None:
            return error
        if attempt.status != ExamAttempt.Status.IN_PROGRESS:
            return Response({'error': 'attempt_not_active'}, status=409)

        event_type = request.data.get('event') or AntiCheatEvent.EventType.TAB_SWITCH
        metadata = request.data.get('metadata') or {}

        with unscoped_context(), transaction.atomic():
            attempt = ExamAttempt.objects.select_for_update().get(pk=attempt.pk)
            AntiCheatEvent.objects.create(
                organization=attempt.organization,
                attempt=attempt,
                event_type=event_type,
                metadata=metadata,
            )
            attempt.strikes = (attempt.strikes or 0) + 1
            max_strikes = get_org_setting(attempt.organization, 'anti_cheat.max_strikes') or 3
            cancelled = attempt.strikes >= max_strikes
            if cancelled:
                reason = (
                    ExamAttempt.CancelReason.TAB_SWITCH
                    if event_type == AntiCheatEvent.EventType.TAB_SWITCH
                    else ExamAttempt.CancelReason.FULLSCREEN_EXIT
                )
                attempt.cancel_for_cheating(reason=reason)
            else:
                attempt.save(update_fields=['strikes', 'updated_at'])

        return Response(
            {
                'strikes': attempt.strikes,
                'max': max_strikes,
                'cancelled': cancelled,
            }
        )
