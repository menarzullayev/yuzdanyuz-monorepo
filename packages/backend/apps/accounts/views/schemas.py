"""Request schemas for the plain-Django (non-DRF) auth endpoints.

These views read a JSON body directly; validating it through a DRF serializer
turns wrong types (a number for a phone, a list for the body) into a 400
instead of an AttributeError and a 500.
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from rest_framework import serializers

MAX_JSON_BODY_BYTES = 64 * 1024


class StrictCharField(serializers.CharField):
    """CharField that refuses non-strings (DRF would coerce 123 into '123')."""

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class PhoneSchema(serializers.Serializer):
    phone = StrictCharField(max_length=32)


class OTPVerifySchema(PhoneSchema):
    code = StrictCharField(max_length=12)


class TelegramInitDataSchema(serializers.Serializer):
    init_data = StrictCharField(max_length=8192)
    acquisition = serializers.DictField(required=False, default=dict)


class DeeplinkCompleteSchema(serializers.Serializer):
    token = StrictCharField(max_length=64)
    remember = serializers.BooleanField(required=False, default=True)


def parse_json_body(request, schema: type[serializers.Serializer]):
    """-> (validated_data, None) or (None, JsonResponse 400)."""
    if len(request.body or b'') > MAX_JSON_BODY_BYTES:
        return None, JsonResponse({'error': 'Request body too large'}, status=400)
    try:
        body = json.loads(request.body or b'{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse({'error': 'Invalid JSON'}, status=400)
    if not isinstance(body, dict):
        return None, JsonResponse({'error': 'JSON object expected'}, status=400)
    ser = schema(data=body)
    if not ser.is_valid():
        fields = sorted(ser.errors)
        return None, JsonResponse(
            {'error': f'Invalid fields: {", ".join(fields)}', 'fields': ser.errors}, status=400
        )
    return ser.validated_data, None
