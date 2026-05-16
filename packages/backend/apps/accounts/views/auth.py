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
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.services.telegram_auth import (
    TelegramAuthError,
    find_or_create_user,
    verify_init_data,
)
from apps.accounts.services.token_service import (
    clear_auth_cookies,
    create_token_pair,
    make_fingerprint,
    revoke_token,
    rotate_refresh_token,
    set_auth_cookies,
)


def _is_secure(request) -> bool:
    return request.is_secure() or not settings.DEBUG


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

        fingerprint = make_fingerprint(request)
        org = user.primary_organization
        access, refresh = create_token_pair(user, fingerprint, org=org)

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
        set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
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

        access_token = request.COOKIES.get('access_token')
        fingerprint = make_fingerprint(request)

        # user_id ni eskirgan access token dan olish (imzo tekshirilmaydi, faqat payload)
        import jwt as pyjwt

        user_id = None
        if access_token:
            try:
                payload = pyjwt.decode(
                    access_token,
                    settings.SECRET_KEY,
                    algorithms=['HS256'],
                    options={'verify_exp': False},
                )
                user_id = payload.get('sub', '')
            except pyjwt.PyJWTError:
                pass

        if not user_id:
            return JsonResponse({'error': "Token o'qib bo'lmadi"}, status=401)

        result = rotate_refresh_token(old_refresh, user_id, fingerprint)
        if result is None:
            return JsonResponse(
                {'error': "Refresh token noto'g'ri yoki muddati o'tgan"}, status=401
            )

        new_access, new_refresh = result
        response = JsonResponse({'ok': True})
        set_auth_cookies(response, new_access, new_refresh, is_secure=_is_secure(request))
        return response


@method_decorator(csrf_exempt, name='dispatch')
class LogoutView(View):
    """
    POST /api/auth/logout/
    Cookie: access_token, refresh_token
    """

    def post(self, request):
        access_token = request.COOKIES.get('access_token')
        fingerprint = make_fingerprint(request)

        if access_token:
            import jwt as pyjwt

            try:
                payload = pyjwt.decode(
                    access_token,
                    settings.SECRET_KEY,
                    algorithms=['HS256'],
                    options={'verify_exp': False},
                )
                user_id = payload.get('sub', '')
                if user_id:
                    revoke_token(user_id, fingerprint)
            except pyjwt.PyJWTError:
                pass

        response = JsonResponse({'ok': True})
        clear_auth_cookies(response)
        return response


class MeView(APIView):
    """
    GET /api/auth/me/

    Returns the authenticated user's profile + primary organization.
    Frontend invokes this immediately after login (or on app boot) to bootstrap
    the user shell (display name, role, org switcher, locale).

    Authentication: JWTAuthMiddleware reads the `access_token` HttpOnly cookie
    and populates request.user. SessionAuthentication (DRF default) then passes
    through. 401 when anonymous, 200 with payload when authenticated.
    """

    permission_classes = [IsAuthenticated]

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
