"""Shared helpers for the auth JSON API."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.authentication import CookieJWTAuthentication


def api_error(code: str, message: str, status: int = 400, details=None, **extra) -> Response:
    """Error envelope identical to core.exceptions.unified_exception_handler."""
    payload = {
        'success': False,
        'error': {'code': code, 'message': message, 'details': details or {}},
        'detail': message,
    }
    payload['error'].update(extra)
    return Response(payload, status=status)


def field_errors(**fields: list[tuple[str, str]]) -> dict:
    """``field_errors(password=[('too_short', 'msg')])`` -> details dict."""
    return {f: [{'code': c, 'message': m} for c, m in errs] for f, errs in fields.items()}


class AuthAPIView(APIView):
    """Authenticated JSON endpoint (cookie JWT, 401 when anonymous)."""

    authentication_classes = [CookieJWTAuthentication]
    permission_classes = [IsAuthenticated]


class PublicAPIView(APIView):
    """Anonymous endpoint; never touches the session/CSRF machinery."""

    authentication_classes: list = []
    permission_classes: list = []
