"""
ISSUE-204 — Unified DRF exception handler with standardized error envelope.

DRF default `{detail: ...}` format'i ko'p variant — RateLimit middleware
o'zining envelope'ini ishlatadi, view'lar ad-hoc shapes. Bu handler barcha
DRF exception'larni quyidagi yagona shape'ga aylantiradi:

    {
        "success": false,
        "error": {
            "code": "validation_error",
            "message": "Human-readable Uzbek message",
            "details": {...}  // field-level errors yoki additional context
        }
    }

Successful response'lar uchun envelope global emas (har endpoint o'z shape'ini
saqlaydi back-compat uchun). Frontend yangi `error.code` orqali switching
qiladi, eski `detail` field ham mavjud (backward compat 6 oy).
"""

from __future__ import annotations

from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler


def unified_exception_handler(exc, context):
    """DRF exception → standartlashtirilgan envelope.

    Eski `detail` field saqlanadi (backward-compat).
    """
    response = drf_default_handler(exc, context)
    if response is None:
        return None

    code = _extract_code(exc, response)
    message = _extract_message(response)
    details = _extract_details(response)

    envelope = {
        'success': False,
        'error': {
            'code': code,
            'message': message,
            'details': details,
        },
        # Backward-compat: DRF eski `detail` field
        'detail': message,
    }
    return Response(envelope, status=response.status_code, headers=response.headers)


def _extract_code(exc, response) -> str:
    """HTTP status'dan tashqari, exception class'idan stable code chiqarish."""
    if isinstance(exc, APIException):
        default_code = getattr(exc, 'default_code', None)
        if default_code:
            return str(default_code)
    status = response.status_code
    return {
        400: 'bad_request',
        401: 'authentication_required',
        403: 'permission_denied',
        404: 'not_found',
        405: 'method_not_allowed',
        409: 'conflict',
        413: 'payload_too_large',
        415: 'unsupported_media_type',
        429: 'rate_limit_exceeded',
        500: 'server_error',
        503: 'service_unavailable',
    }.get(status, 'unknown_error')


def _extract_message(response) -> str:
    """First-level human message (field-level errors `details`'da qoladi)."""
    data = response.data
    if isinstance(data, dict):
        if 'detail' in data:
            return str(data['detail'])
        # Field error pattern: {field: [msg]}
        for key, val in data.items():
            if isinstance(val, list) and val:
                return f'{key}: {val[0]}'
            if isinstance(val, str):
                return val
    return 'Xato yuz berdi'


def _extract_details(response) -> dict | None:
    data = response.data
    if isinstance(data, dict) and 'detail' not in data:
        return data
    return None
