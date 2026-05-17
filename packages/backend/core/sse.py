"""Server-Sent Events (SSE) helpers — ISSUE-112 Phase 1.

HestiaCP subpath constraint sababli WebSocket o'rniga SSE ishlatamiz. Bu modul
SSE message format va StreamingHttpResponse'ga umumiy header'larni qaytaradigan
yordamchi funksiyalarni saqlaydi. Per-app view'lar generator yoziydi.

Spec: https://html.spec.whatwg.org/multipage/server-sent-events.html
"""

from __future__ import annotations

import json
from typing import Any

from django.http import StreamingHttpResponse


def sse_format(
    event: str, data: Any, event_id: int | str | None = None, retry_ms: int | None = None
) -> str:
    """Format a single SSE message.

    Browser EventSource parsing:
      `id:`     — Last-Event-ID header'ga qaytariladi reconnect paytida
      `event:`  — `addEventListener(<name>, handler)` orqali ushlanadi
      `data:`   — JSON-serializable payload
      `retry:`  — reconnect delay (ms) override

    Output ends with `\\n\\n` (event terminator).
    """
    out = ''
    if event_id is not None:
        out += f'id: {event_id}\n'
    if retry_ms is not None:
        out += f'retry: {retry_ms}\n'
    out += f'event: {event}\n'
    out += f'data: {json.dumps(data, ensure_ascii=False)}\n\n'
    return out


def sse_response(event_generator, *, last_event_id: str | None = None) -> StreamingHttpResponse:
    """Wrap a generator in `StreamingHttpResponse` with SSE-correct headers.

    `X-Accel-Buffering: no` — nginx must not buffer (also respected by some proxies).
    `Cache-Control: no-cache, no-transform` — strict no-cache; no-transform stops gzip on streams.
    `Connection: keep-alive` — explicit (default in HTTP/1.1 but make intent clear).
    """
    resp = StreamingHttpResponse(event_generator, content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache, no-transform'
    resp['X-Accel-Buffering'] = 'no'
    resp['Connection'] = 'keep-alive'
    # PHP cURL proxy chunked'ni o'tkazib yuborishi uchun har qanday buffering signalini o'chiramiz.
    return resp
