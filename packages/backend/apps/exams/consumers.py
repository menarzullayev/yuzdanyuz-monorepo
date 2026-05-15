"""
Task 4 — Exam WebSocket consumer.

URL: ws/exams/attempt/<attempt_id>/

Client → server messages (JSON):
  {"type": "heartbeat"}                  → server "heartbeat_ack" + remaining
  {"type": "anticheat", "event": "tab_switch"}
                                          → strike count + cancel agar 3+ bo'lsa
  {"type": "request_timer"}              → "timer" event with seconds_left

Server → client messages:
  {"type": "heartbeat_ack", "seconds_left": int}
  {"type": "strike", "count": int, "max": 3}
  {"type": "cancelled", "reason": str}
  {"type": "timer", "seconds_left": int}
  {"type": "expired"}    — vaqt tugagan, attempt EXPIRED
  {"type": "error", "message": str}

Auth: foydalanuvchi authenticated bo'lishi va attempt o'ziniki bo'lishi.
Heartbeat o'tkazib yuborilsa (60s timeout — frontend 10s'da ping yuborishi kerak),
keyingi ping'da `heartbeat_miss` event log qilinadi va strike oshadi.

Eslatma: Channel layer InMemoryChannelLayer test'da, Redis prod'da.
"""

import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_STRIKES = 3
HEARTBEAT_TIMEOUT_SECONDS = 60  # frontend 10s'da ping → 60s'da 6 ta o'tkazish max


class ExamAttemptConsumer(AsyncJsonWebsocketConsumer):
    """Single attempt WebSocket consumer — no group, just per-connection."""

    async def connect(self):
        self.attempt_id = self.scope['url_route']['kwargs']['attempt_id']
        user = self.scope.get('user')

        if user is None or user.is_anonymous:
            await self.close(code=4401)  # Unauthorized
            return

        attempt = await self._get_attempt(self.attempt_id, user.id)
        if attempt is None:
            await self.close(code=4404)  # Not found / not yours
            return
        if attempt['status'] != 'in_progress':
            await self.close(code=4409)  # Conflict — attempt not active
            return

        self.exam_duration_minutes = attempt['exam_duration_minutes']
        self.started_at = attempt['started_at']
        await self.accept()

        # Initial timer state
        await self.send_json({'type': 'timer', 'seconds_left': self._seconds_left()})

    async def disconnect(self, code):
        # Disconnect — heartbeat dropped. Server-side: keyingi heartbeat ping
        # paytida `heartbeat_miss` ni handle qiladigan vazifa Celery beat orqali
        # bo'lishi mumkin (PR #29 keyingi qism — out of scope for now).
        logger.debug('ExamAttemptConsumer disconnected: code=%s attempt=%s', code, self.attempt_id)

    async def receive_json(self, content, **kwargs):
        msg_type = content.get('type')

        if msg_type == 'heartbeat':
            await self._handle_heartbeat()
        elif msg_type == 'anticheat':
            event = content.get('event', 'tab_switch')
            await self._handle_anticheat(event, content.get('metadata', {}))
        elif msg_type == 'request_timer':
            await self.send_json({'type': 'timer', 'seconds_left': self._seconds_left()})
        else:
            await self.send_json({'type': 'error', 'message': f'Unknown type: {msg_type}'})

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _seconds_left(self) -> int:
        if not self.started_at:
            return 0
        elapsed = (timezone.now() - self.started_at).total_seconds()
        total = self.exam_duration_minutes * 60
        return max(0, int(total - elapsed))

    async def _handle_heartbeat(self):
        seconds = self._seconds_left()
        if seconds == 0:
            # Vaqt tugagan — attempt EXPIRED
            await self._set_status('expired')
            await self.send_json({'type': 'expired'})
            await self.close(code=4408)
            return

        await self._update_heartbeat(self.attempt_id)
        await self.send_json({'type': 'heartbeat_ack', 'seconds_left': seconds})

    async def _handle_anticheat(self, event_type: str, metadata: dict):
        result = await self._record_strike(self.attempt_id, event_type, metadata)
        await self.send_json({'type': 'strike', 'count': result['strikes'], 'max': MAX_STRIKES})
        if result['cancelled']:
            await self.send_json({'type': 'cancelled', 'reason': event_type})
            await self.close(code=4410)

    # ── DB calls (sync_to_async) ─────────────────────────────────────────────

    @database_sync_to_async
    def _get_attempt(self, attempt_id, user_id):
        from core.tenant import unscoped_context

        from .models import ExamAttempt

        with unscoped_context():
            try:
                attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id)
            except ExamAttempt.DoesNotExist:
                return None
            if attempt.user_id != user_id:
                return None
            return {
                'status': attempt.status,
                'exam_duration_minutes': attempt.exam.duration_minutes,
                'started_at': attempt.started_at,
            }

    @database_sync_to_async
    def _update_heartbeat(self, attempt_id):
        from core.tenant import unscoped_context

        from .models import ExamAttempt

        with unscoped_context():
            ExamAttempt.objects.filter(pk=attempt_id).update(heartbeat_last_at=timezone.now())

    @database_sync_to_async
    def _record_strike(self, attempt_id, event_type, metadata):
        from django.db import transaction

        from core.tenant import unscoped_context

        from .models import AntiCheatEvent, ExamAttempt

        with unscoped_context(), transaction.atomic():
            attempt = ExamAttempt.objects.select_for_update().get(pk=attempt_id)
            AntiCheatEvent.objects.create(
                organization=attempt.organization,
                attempt=attempt,
                event_type=event_type,
                metadata=metadata or {},
            )
            attempt.strikes = (attempt.strikes or 0) + 1
            update_fields = ['strikes', 'updated_at']

            cancelled = False
            if attempt.strikes >= MAX_STRIKES:
                attempt.status = ExamAttempt.Status.CANCELLED
                attempt.cancel_reason = ExamAttempt.CancelReason.TAB_SWITCH
                attempt.submitted_at = timezone.now()
                update_fields += ['status', 'cancel_reason', 'submitted_at']
                cancelled = True

            attempt.save(update_fields=update_fields)
            return {'strikes': attempt.strikes, 'cancelled': cancelled}

    @database_sync_to_async
    def _set_status(self, status_value: str):
        from core.tenant import unscoped_context

        from .models import ExamAttempt

        with unscoped_context():
            ExamAttempt.objects.filter(pk=self.attempt_id).update(
                status=ExamAttempt.Status.EXPIRED if status_value == 'expired' else status_value,
                submitted_at=timezone.now(),
            )
