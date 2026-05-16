# WebSocket Strategy — SSE Migration Plan

> **Status**: 2026-05-16 yangi qaror — Django Channels WebSocket o'rniga **Server-Sent Events (SSE) + Polling POST**
> **Sabab**: HestiaCP subpath deployment cheklovlari (PHP cURL proxy WebSocket'ni tashishi mumkin emas, subdomain yo'q, admin yo'q)
> **Status**: 🔴 BLOCKER — frontend qurishdan oldin backend SSE'ga ko'chirish kerak

---

## 1. Cheklov tahlili

### Hozirgi arxitektura
```
Browser → https://hsm.sammu.uz/yuzdanyuz/ws/exams/{id}/
       → Apache (port 443)
       → PHP-FPM (index.php proxy)
       → cURL → http://127.0.0.1:8002/yuzdanyuz/ws/exams/{id}/
       → Daphne (Django Channels)
       → ExamAttemptConsumer (AsyncJsonWebsocketConsumer)
```

### Nima ishlamaydi
- **PHP cURL HTTP/1.1 client** — `Upgrade: websocket` header'ini tashishi mumkin emas
- **mod_proxy_wstunnel** — Apache module, admin install kerak (bizda yo'q)
- **Subdomain** (`ws.hsm.sammu.uz`) — HestiaCP'da yangi user subdomain create kerak (admin yo'q)
- **External service** (Pusher, Ably) — $$$, vendor lock-in

### Real-world hodisa
Hozir `ws/exams/{id}/` ga so'rov yuborilsa: PHP cURL `GET` qiladi → daphne `400 Bad Request` qaytaradi (chunki WebSocket handshake yo'q) → PHP `500` browser'ga qaytaradi.

---

## 2. SSE — yagona ishlaydigan yo'l

### Server-Sent Events nima?
- **Standard HTML5 API** — `EventSource` (har modern browser, Safari/Chrome/Firefox/Edge)
- **HTTP/1.1 chunked transfer** — `Content-Type: text/event-stream`
- **One-way**: server → client (client → server uchun oddiy POST)
- **Auto-reconnect** built-in — browser uzilsa o'zi qayta ulanadi
- **Last-Event-ID header** — uzilgan event'larni catchup qila olamiz
- **Plain HTTP** — PHP cURL bemalol tashiy oladi (faqat output buffering off + chunked transfer)

### Bizning use-case'larga moslik

| Use case | WebSocket usul | SSE + POST usul | Mos? |
|---|---|---|---|
| Anti-cheat strike (server→client) | `{"type": "strike"}` send | SSE `event: strike\ndata: {...}` | ✅ |
| Anti-cheat event log (client→server) | `{"type": "anticheat"}` receive | `POST /yuzdanyuz/api/v1/exams/{id}/anticheat/` | ✅ |
| Heartbeat ping (client→server) | `{"type": "heartbeat"}` receive | `POST /yuzdanyuz/api/v1/exams/{id}/heartbeat/` | ✅ |
| Heartbeat ack + timer (server→client) | `{"type": "heartbeat_ack"}` send | POST response body | ✅ (synchronous in same request) |
| Timer update (server→client periodic) | `{"type": "timer"}` send | SSE `event: timer\ndata: {seconds: N}` | ✅ |
| Exam expired (server→client) | `{"type": "expired"}` + close | SSE `event: expired` + browser EventSource close | ✅ |
| Cancelled (server→client) | `{"type": "cancelled"}` + close | SSE `event: cancelled` | ✅ |
| Leaderboard live update | Channel group broadcast | SSE multiplexed per user / Redis pub-sub | ✅ |
| AI tutor streaming response | Channel pipe | SSE token-by-token | ✅ (Anthropic SDK already supports streaming) |
| Notification feed | Channel group | SSE per-user | ✅ |

**100% coverage** — hech qanday use case yo'qotmaydi.

---

## 3. Misol — heartbeat consumer SSE'ga ko'chirish

### Hozirgi (WebSocket — apps/exams/consumers.py)

```python
class ExamAttemptConsumer(AsyncJsonWebsocketConsumer):
    async def receive_json(self, content, **kwargs):
        msg_type = content.get('type')
        if msg_type == 'heartbeat':
            await self._handle_heartbeat()
        elif msg_type == 'anticheat':
            await self._handle_anticheat(content['event'], content.get('metadata', {}))

    async def _handle_heartbeat(self):
        seconds = self._seconds_left()
        if seconds == 0:
            await self.send_json({'type': 'expired'})
            await self.close(code=4408)
            return
        await self.send_json({'type': 'heartbeat_ack', 'seconds_left': seconds})
```

### Yangi (SSE + POST — apps/exams/sse_views.py)

```python
"""Exam SSE event stream + polling POST endpoints."""

import json
import time
from django.http import StreamingHttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from .models import ExamAttempt


def _sse_format(event: str, data: dict, event_id: int | None = None) -> str:
    """Format an SSE message."""
    out = ''
    if event_id is not None:
        out += f'id: {event_id}\n'
    out += f'event: {event}\n'
    out += f'data: {json.dumps(data)}\n\n'
    return out


def attempt_event_stream(request, attempt_id):
    """SSE stream — server → client push events (timer, strike, expired).

    Long-lived HTTP response. PHP proxy streams chunks as they arrive.
    Browser EventSource() auto-reconnects on disconnect.
    """
    # Auth + ownership check
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({'error': 'unauthorized'}, status=401)

    from core.tenant import unscoped_context
    with unscoped_context():
        try:
            attempt = ExamAttempt.objects.select_related('exam').get(pk=attempt_id)
        except ExamAttempt.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if attempt.user_id != user.id:
            return JsonResponse({'error': 'forbidden'}, status=403)
        if attempt.status != 'in_progress':
            return JsonResponse({'error': 'attempt not active'}, status=409)

    def event_generator():
        # Initial timer
        seconds_left = _seconds_left(attempt)
        yield _sse_format('timer', {'seconds_left': seconds_left}, event_id=1)

        event_id = 2
        while True:
            time.sleep(5)  # Push every 5 sec
            seconds_left = _seconds_left(attempt)

            if seconds_left <= 0:
                # Expired
                yield _sse_format('expired', {}, event_id=event_id)
                return  # close stream

            # Periodic timer update
            yield _sse_format('timer', {'seconds_left': seconds_left}, event_id=event_id)
            event_id += 1

            # Check for new strikes (via Redis pub-sub or DB poll)
            new_strikes = _check_new_strikes_since(attempt_id, event_id)
            for strike in new_strikes:
                yield _sse_format('strike', strike, event_id=event_id)
                event_id += 1
                if strike.get('cancelled'):
                    yield _sse_format('cancelled', {'reason': strike['reason']}, event_id=event_id)
                    return  # close stream

    response = StreamingHttpResponse(event_generator(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # nginx
    return response


class HeartbeatView(APIView):
    """POST /yuzdanyuz/api/v1/exams/{id}/heartbeat/

    Client every 5-10 sec → server response with seconds_left.
    Updates attempt.heartbeat_last_at (anti-cheat: server detects missed heartbeats).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, attempt_id):
        from core.tenant import unscoped_context
        with unscoped_context():
            try:
                attempt = ExamAttempt.objects.select_related('exam').get(
                    pk=attempt_id, user=request.user
                )
            except ExamAttempt.DoesNotExist:
                return JsonResponse({'error': 'not found'}, status=404)
            if attempt.status != 'in_progress':
                return JsonResponse({'error': 'not active'}, status=409)

            seconds_left = _seconds_left(attempt)
            if seconds_left <= 0:
                attempt.expire()
                return JsonResponse({'expired': True, 'seconds_left': 0})

            attempt.heartbeat_last_at = timezone.now()
            attempt.save(update_fields=['heartbeat_last_at'])
            return JsonResponse({'seconds_left': seconds_left})


class AntiCheatView(APIView):
    """POST /yuzdanyuz/api/v1/exams/{id}/anticheat/

    Client detects tab switch / fullscreen exit / etc.
    Server records strike. If 3+ strikes → cancel attempt.

    Response (synchronous) + SSE push (broadcast to all open EventSource on this attempt).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, attempt_id):
        event_type = request.data.get('event', 'tab_switch')
        metadata = request.data.get('metadata', {})

        from core.tenant import unscoped_context
        from .models import AntiCheatEvent
        from django.db import transaction

        with unscoped_context(), transaction.atomic():
            try:
                attempt = ExamAttempt.objects.select_for_update().get(
                    pk=attempt_id, user=request.user
                )
            except ExamAttempt.DoesNotExist:
                return JsonResponse({'error': 'not found'}, status=404)

            AntiCheatEvent.objects.create(
                organization=attempt.organization,
                attempt=attempt,
                event_type=event_type,
                metadata=metadata,
            )
            attempt.strikes = (attempt.strikes or 0) + 1

            from core.config import get_org_setting
            max_strikes = get_org_setting(attempt.organization, 'anti_cheat.max_strikes')

            cancelled = attempt.strikes >= max_strikes
            if cancelled:
                attempt.cancel_for_cheating(reason=ExamAttempt.CancelReason.TAB_SWITCH)
            else:
                attempt.save(update_fields=['strikes', 'updated_at'])

            # TODO: publish to SSE stream via Redis pub-sub
            # (so other open tabs / SSE consumers see the strike in real-time)

            return JsonResponse({
                'strikes': attempt.strikes,
                'max': max_strikes,
                'cancelled': cancelled,
            })


def _seconds_left(attempt) -> int:
    if not attempt.started_at:
        return 0
    elapsed = (timezone.now() - attempt.started_at).total_seconds()
    total = attempt.exam.duration_minutes * 60
    return max(0, int(total - elapsed))


def _check_new_strikes_since(attempt_id, last_event_id):
    """Stub — kelajakda Redis pub-sub bilan implement qilamiz."""
    # MVP: just return empty list (strikes come via AntiCheatView response)
    return []
```

---

## 4. PHP proxy SSE support — `index.php` qo'shimcha

Hozirgi `index.php` cURL'ni buffer'lab Django response'ni server'ga qaytaradi. SSE uchun **streaming** kerak.

```php
// SSE detection: agar URL '/events/' bilan tugasa
$is_sse = str_ends_with(parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH), '/events/');

if ($is_sse) {
    // Disable PHP buffering
    @ini_set('output_buffering', 'off');
    @ini_set('zlib.output_compression', false);
    while (ob_get_level()) ob_end_flush();
    ob_implicit_flush(true);

    header('Content-Type: text/event-stream');
    header('Cache-Control: no-cache');
    header('X-Accel-Buffering: no');

    // cURL streaming write callback
    curl_setopt($ch, CURLOPT_WRITEFUNCTION, function($curl, $data) {
        echo $data;
        flush();  // chunk darhol browser'ga
        return strlen($data);
    });
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Accept: text/event-stream',
        'Cache-Control: no-cache',
        // pass user cookies for auth
        'Cookie: ' . $_SERVER['HTTP_COOKIE'] ?? '',
    ]);
    curl_setopt($ch, CURLOPT_TIMEOUT, 0);  // no timeout (long-lived)
    curl_exec($ch);
    curl_close($ch);
    exit;
}

// Boshqa hammasi — oddiy buffered proxy
// (mavjud code)
```

**Risk**: long-lived PHP-FPM worker. Default PHP-FPM `pm.max_requests = 500`, `pm.max_children = 5`. Agar 5 ta SSE connection ochilsa, PHP-FPM banded — boshqa request'lar block. Mitigation:
- `pm = ondemand` (kerak bo'lganda spawn)
- `pm.max_spare_servers = 10`
- SSE timeout 5-10 daqiqada client reconnect (browser auto-reconnect)

---

## 5. Frontend EventSource client

```typescript
// packages/frontend-web/src/lib/sse.ts
export class ExamEventStream {
    private source: EventSource | null = null;

    constructor(
        private attemptId: string,
        private handlers: {
            onTimer: (seconds: number) => void;
            onStrike: (count: number, max: number) => void;
            onExpired: () => void;
            onCancelled: (reason: string) => void;
            onError: (e: Event) => void;
        }
    ) {}

    connect() {
        const url = `/yuzdanyuz/api/v1/exams/${this.attemptId}/events/`;
        this.source = new EventSource(url, { withCredentials: true });

        this.source.addEventListener('timer', (e) => {
            const data = JSON.parse(e.data);
            this.handlers.onTimer(data.seconds_left);
        });

        this.source.addEventListener('strike', (e) => {
            const data = JSON.parse(e.data);
            this.handlers.onStrike(data.count, data.max);
        });

        this.source.addEventListener('expired', () => {
            this.handlers.onExpired();
            this.disconnect();
        });

        this.source.addEventListener('cancelled', (e) => {
            const data = JSON.parse(e.data);
            this.handlers.onCancelled(data.reason);
            this.disconnect();
        });

        this.source.onerror = (e) => {
            this.handlers.onError(e);
            // browser auto-reconnects after 3 sec by default
        };
    }

    disconnect() {
        this.source?.close();
        this.source = null;
    }
}

// Heartbeat — separate POST every 10 sec
export async function sendHeartbeat(attemptId: string): Promise<{seconds_left: number; expired?: boolean}> {
    const res = await fetch(`/yuzdanyuz/api/v1/exams/${attemptId}/heartbeat/`, {
        method: 'POST',
        credentials: 'include',
    });
    if (!res.ok) throw new Error(`Heartbeat failed: ${res.status}`);
    return res.json();
}

// Anti-cheat — POST when detection fires
export async function reportAntiCheat(
    attemptId: string,
    event: 'tab_switch' | 'fullscreen_exit' | 'heartbeat_lost',
    metadata: object = {}
): Promise<{strikes: number; max: number; cancelled: boolean}> {
    const res = await fetch(`/yuzdanyuz/api/v1/exams/${attemptId}/anticheat/`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'include',
        body: JSON.stringify({event, metadata}),
    });
    if (!res.ok) throw new Error(`Anti-cheat report failed: ${res.status}`);
    return res.json();
}
```

---

## 6. Migration plan

### Phase 1 — Backend SSE views (2 kun)
- [ ] `apps/exams/sse_views.py` yaratish (yuqoridagi misol asosida)
- [ ] `apps/exams/urls/api.py` — yangi route'lar:
  - `GET  /api/v1/exams/{id}/events/`     — SSE stream
  - `POST /api/v1/exams/{id}/heartbeat/`  — heartbeat
  - `POST /api/v1/exams/{id}/anticheat/`  — anti-cheat report
- [ ] Unit tests (Django StreamingHttpResponse'ni test qilish biroz mushkul, lekin mumkin)

### Phase 2 — PHP proxy SSE support (4 soat)
- [ ] `index.php`'ga SSE detection + streaming cURL callback
- [ ] PHP-FPM config — `pm = ondemand`, `pm.max_spare_servers = 10`
- [ ] Test: `curl -N https://hsm.sammu.uz/yuzdanyuz/api/v1/exams/{id}/events/`
  - Kutiladi: stream chunks every 5 sec

### Phase 3 — Frontend client (1 kun)
- [ ] `src/lib/sse.ts` — `ExamEventStream` class
- [ ] React hook: `useExamHeartbeat(attemptId)` — start/stop, handle disconnect
- [ ] Exam taking page integration

### Phase 4 — Engagement consumers (1 kun)
- [ ] `apps/engagement/consumers.py` — leaderboard SSE
- [ ] `apps/engagement/consumers.py` — notification feed SSE

### Phase 5 — Deprecate Channels (1 kun)
- [ ] Old `consumers.py` fayllarni `_deprecated/` papkasiga ko'chirish (3 oy back-compat)
- [ ] `core/asgi.py` — WebSocket routing'ni o'chirish (HTTP-only)
- [ ] Daphne'ni gunicorn'ga to'liq ko'chirish (1 ta web server, 1 ta tip)
- [ ] `requirements/base.txt`: `channels`, `channels-redis` o'chirish (agar boshqa joyda ishlatilmayotgan bo'lsa)

### Total: 5-7 kun ish

---

## 7. Decision matrix — bizning vaziyatda nima eng yaxshi

| Kriteriya | WebSocket (mavjud) | SSE + POST (yangi) | Long-polling | Pusher (paid) |
|---|---|---|---|---|
| HestiaCP'da ishlaydi | ❌ | ✅ | ✅ | ✅ |
| Admin kerakmi | YES | NO | NO | NO |
| Subdomain kerakmi | YES | NO | NO | NO |
| UX (real-time feel) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐⭐ |
| Implementatsiya effort | (mavjud) | 5-7 kun | 1-2 kun | 1-2 kun |
| Cost | $0 | $0 | $0 | $50+/oy |
| Vendor lock | NO | NO | NO | YES |
| Browser support | Modern | Modern (IE11 yo'q, lekin biz IE11 support qilmaymiz) | Universal | Modern |
| Auto-reconnect | Manual | Built-in | Manual | Built-in |
| AI tutor streaming | Native | Native | ❌ (request/response) | Native |
| Mobile React Native | Native (ws) | EventSource polyfill kerak | Universal | Native |

**Tanlov**: **SSE + POST** — bizning constraints'larda yagona to'g'ri yo'l.

---

## 8. Risk va mitigation

### Risk 1: PHP-FPM workers exhausted
- **Senariy**: 100 student bir vaqtda exam tashkil qiladi, 100 SSE connection ochiladi → PHP-FPM `max_children = 5` band → yangi user kira olmaydi
- **Mitigation**:
  - `pm = ondemand` + `pm.max_children = 50` (har worker ~30 MB → 1.5 GB peak — server'da bor)
  - Yoki: SSE timeout 5 daqiqada (browser auto-reconnect — load distribute)
  - Long-term: nginx async proxy (admin kerak — kelajak)

### Risk 2: cURL timeout on long stream
- **Senariy**: cURL default 30 sek timeout — SSE 30 sek'da uziladi
- **Mitigation**: `CURLOPT_TIMEOUT = 0` (no timeout) — kod'da qo'shildi yuqorida

### Risk 3: Apache request timeout
- **Senariy**: Apache default `Timeout 60` — 60 sek'dan uzun request uziladi
- **Mitigation**: `.htaccess`'ga `Timeout 300` qo'shish (5 daqiqa) — `.htaccess`'da `Timeout` directive ishlamaydi (faqat httpd.conf), lekin uzilgan SSE auto-reconnect bo'ladi

### Risk 4: Mobile (React Native) EventSource yo'q
- **Senariy**: React Native'da native `EventSource` API yo'q
- **Mitigation**: `react-native-sse` polyfill (npm, well-maintained)

### Risk 5: Anti-cheat real-time slippage
- **Senariy**: Strike POST → server → SSE push gap (Redis pub-sub kerak) — 2 sek'da boshqa tab'da ko'rinmaydi
- **Mitigation**:
  - MVP: faqat bitta tab (browser'da window.focus tekshirish)
  - Future: Redis pub-sub + Django signals

---

## 9. Fallback — agar SSE ham ishlamasa

Theoretical worst case. SSE PHP proxy'da unstable bo'lsa:

**Long-polling** (1 daraja pasayish):
- Client `GET /events/?since=<last_event_id>` qiladi
- Server 30 sek kutadi yangi event, bo'lmasa empty response qaytaradi
- Client darhol qayta so'raydi (loop)
- UX 1-2 sek latency

Bu **WebSocket bilan SSE o'rtasidagi yo'l** — eng oxirgi fallback.

---

## 10. Document status

- [x] Constraint analysis (PHP cURL, no admin, no subdomain)
- [x] SSE solution rationale (10 use case'da 100% coverage)
- [x] Backend implementation pattern (`StreamingHttpResponse` + POST views)
- [x] PHP proxy SSE support (chunked transfer + `CURLOPT_WRITEFUNCTION`)
- [x] Frontend EventSource client (`src/lib/sse.ts`)
- [x] Migration plan (5 phase, 5-7 kun)
- [x] Decision matrix
- [x] Risk analysis + mitigation
- [x] Fallback (long-polling) plan

**Owner**: Backend dev (Phase 1+2+4+5), Frontend dev (Phase 3)
**Timeline**: Frontend MVP bilan birga (Q3 2026)
**Blocker depends on**: `apps/frontend-web/` foundation tayyor bo'lishi

---

**Last updated**: 2026-05-16
**Related**: `docs/pre_launch_checklist.md` (BLOCKER #1), `apps/exams/consumers.py` (deprecated path)
