"""
Active-device management.

  GET    /api/auth/sessions/               list active devices of the caller
  DELETE /api/auth/sessions/<uuid>/        revoke one device
  POST   /api/auth/sessions/revoke-others/ revoke every device but the current one
"""

import uuid

from rest_framework.response import Response

from apps.accounts.models import DeviceSession
from apps.accounts.services.token_service import (
    active_sessions,
    clear_auth_cookies,
    revoke_session,
    revoke_user_sessions,
)
from apps.accounts.services.user_agents import parse_user_agent
from apps.accounts.views.base import AuthAPIView, api_error


def serialize_session(s: DeviceSession, current_sid: str | None) -> dict:
    ua = parse_user_agent(s.user_agent)
    return {
        'id': str(s.pk),
        'device_label': s.device_label or ua.label,
        'browser': ua.browser,
        'os': ua.os,
        'device_type': ua.device_type,
        'country': s.country or None,
        'city': s.city or None,
        'ip_address': s.ip_address,
        'remember': s.remember,
        'created_at': s.created_at,
        'last_used_at': s.last_used_at,
        'is_current': current_sid is not None and s.pk.hex == current_sid,
    }


class SessionListView(AuthAPIView):
    def get(self, request):
        current = getattr(request, 'session_sid', None)
        items = [serialize_session(s, current) for s in active_sessions(request.user)]
        return Response({'sessions': items})


class SessionDetailView(AuthAPIView):
    def delete(self, request, session_id: uuid.UUID):
        session = active_sessions(request.user).filter(pk=session_id).first()
        if session is None:
            return api_error('session_not_found', 'Sessiya topilmadi.', 404)
        revoke_session(session, 'revoked')
        response = Response({'ok': True})
        if session.pk.hex == getattr(request, 'session_sid', None):
            clear_auth_cookies(response)
        return response


class SessionRevokeOthersView(AuthAPIView):
    def post(self, request):
        current = getattr(request, 'session_sid', None)
        count = revoke_user_sessions(
            request.user,
            except_session_id=uuid.UUID(hex=current) if current else None,
            reason='revoked',
        )
        return Response({'ok': True, 'revoked': count})
