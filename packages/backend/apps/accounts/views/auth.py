"""
Auth views — Telegram TMA login, token refresh, logout, current user.

Endpoints:
  POST /api/auth/telegram/   — TMA initData → JWT cookie
  POST /api/auth/refresh/    — Refresh token rotation
  POST /api/auth/logout/     — Cookie tozalash
  GET  /api/auth/me/         — Current user + primary org
"""

import json

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response

from apps.accounts.services.telegram_auth import (
    TelegramAuthError,
    find_or_create_user,
    verify_init_data,
)
from apps.accounts.services.token_service import (
    attach_session,
    clear_auth_cookies,
    issue_login,
    revoke_session,
    rotate_refresh_token,
    session_from_refresh,
)
from apps.accounts.views.base import AuthAPIView


@method_decorator(csrf_exempt, name='dispatch')
class TelegramAuthView(View):
    """
    POST /api/auth/telegram/
    Body: {"init_data": "<TMA WebApp.initData string>"}

    Response: 200 + httpOnly cookie (access_token, refresh_token)
              400 yoki 401 xato bo'lsa
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        init_data = body.get('init_data', '').strip()
        if not init_data:
            return JsonResponse({'error': 'init_data required'}, status=400)

        try:
            tg_user = verify_init_data(init_data)
        except TelegramAuthError as e:
            return JsonResponse({'error': str(e)}, status=401)

        user, created = find_or_create_user(tg_user)
        if not user.is_active:
            return JsonResponse({'error': 'Account blocked'}, status=403)

        issued = issue_login(request, user, remember=True)

        response = JsonResponse(
            {
                'user': {
                    'id': user.pk,
                    'display_name': user.display_name,
                    'user_type': user.user_type,
                    'is_new': created,
                }
            }
        )
        attach_session(response, issued, request)
        return response


@method_decorator(csrf_exempt, name='dispatch')
class TokenRefreshView(View):
    """
    POST /api/auth/refresh/
    Cookie: refresh_token

    Response: 200 + yangilangan cookie juftligi
    """

    def post(self, request):
        old_refresh = request.COOKIES.get('refresh_token')
        if not old_refresh:
            return JsonResponse({'error': "refresh_token cookie yo'q"}, status=401)

        # The refresh token alone identifies the session; the access cookie is
        # normally already gone by the time we get here.
        issued = rotate_refresh_token(old_refresh, request)
        if issued is None:
            response = JsonResponse(
                {'error': "Refresh token noto'g'ri yoki muddati o'tgan"}, status=401
            )
            clear_auth_cookies(response)
            return response

        response = JsonResponse({'ok': True})
        attach_session(response, issued, request)
        return response


@method_decorator(csrf_exempt, name='dispatch')
class LogoutView(View):
    """
    POST /api/auth/logout/
    Cookie: access_token, refresh_token
    """

    def post(self, request):
        session = None
        refresh = request.COOKIES.get('refresh_token')
        if refresh:
            session = session_from_refresh(refresh)
        if session is None:
            # Fall back to the session id inside a (possibly expired) access token.
            token = request.COOKIES.get('access_token')
            if token:
                import jwt as pyjwt

                try:
                    payload = pyjwt.decode(
                        token,
                        settings.SECRET_KEY,
                        algorithms=['HS256'],
                        options={'verify_exp': False},
                    )
                except pyjwt.PyJWTError:
                    payload = {}
                sid = payload.get('sid')
                if sid:
                    from apps.accounts.models import DeviceSession

                    session = DeviceSession.objects.filter(
                        pk=sid, user_id=payload.get('sub'), revoked_at__isnull=True
                    ).first()
        if session is not None:
            revoke_session(session, 'logout')

        response = JsonResponse({'ok': True})
        clear_auth_cookies(response)
        return response


class MeView(AuthAPIView):
    """
    GET /api/auth/me/

    Returns the authenticated user's profile + primary organization.
    Frontend invokes this immediately after login (or on app boot) to bootstrap
    the user shell (display name, role, org switcher, locale).

    Authentication: JWTAuthMiddleware reads the `access_token` HttpOnly cookie
    and populates request.user. SessionAuthentication (DRF default) then passes
    through. 401 when anonymous, 200 with payload when authenticated.
    """

    def get(self, request):
        user = request.user
        org = user.primary_organization
        return Response(
            {
                'id': str(user.pk),
                'email': user.email or None,
                'phone_number': user.phone_number or None,
                'display_name': user.display_name,
                'first_name': user.first_name or None,
                'last_name': user.last_name or None,
                'avatar': user.avatar.url if user.avatar else None,
                'preferred_lang': user.preferred_lang,
                'user_type': user.user_type,
                'is_profile_complete': user.is_profile_complete,
                'telegram': {
                    'id': user.telegram_id,
                    'username': user.telegram_username,
                    'photo_url': user.telegram_photo_url,
                }
                if user.telegram_id
                else None,
                'primary_organization': {
                    'id': str(org.id),
                    'name': org.name,
                    'slug': getattr(org, 'slug', None),
                }
                if org
                else None,
                'is_staff': user.is_staff,
                'is_superuser': user.is_superuser,
            }
        )
