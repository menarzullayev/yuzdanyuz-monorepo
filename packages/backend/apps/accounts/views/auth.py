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
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.response import Response

from apps.accounts.models import CustomUser, PreferredLang
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
from apps.accounts.views.base import AuthAPIView, api_error, field_errors
from apps.accounts.views.credentials import text_field
from apps.accounts.views.payloads import user_payload


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
    GET   /api/auth/me/   current user (frontend bootstrap on app load / after login)
    PATCH /api/auth/me/   first_name, last_name, email (optional, unverified), preferred_lang

    401 when anonymous or when the device session was revoked.
    """

    def get(self, request):
        return Response(self._payload(request.user))

    def patch(self, request):
        user = request.user
        data = request.data
        errors: dict[str, list[tuple[str, str]]] = {}
        update = []

        for key in ('first_name', 'last_name'):
            if key in data:
                val = text_field(data, key)
                if not 2 <= len(val) <= 60:
                    errors[key] = [(f'{key}_length', 'Must be between 2 and 60 characters.')]
                else:
                    setattr(user, key, val)
                    update.append(key)

        if 'email' in data:
            email = text_field(data, 'email').lower()
            if not email:
                user.email = None
                update.append('email')
            else:
                try:
                    validate_email(email)
                except DjangoValidationError:
                    errors['email'] = [('email_invalid', 'Enter a valid email.')]
                else:
                    if CustomUser.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
                        errors['email'] = [('email_taken', 'This email is already in use.')]
                    else:
                        user.email = email
                        update.append('email')

        if 'preferred_lang' in data:
            lang = text_field(data, 'preferred_lang')
            if lang not in PreferredLang.values:
                errors['preferred_lang'] = [('invalid_choice', 'Unsupported language.')]
            else:
                user.preferred_lang = lang
                update.append('preferred_lang')

        if errors:
            return api_error(
                'validation_error', 'Maʼlumotlar notoʻgʻri.', 400, field_errors(**errors)
            )
        if update:
            user.save(update_fields=update)
        return Response(self._payload(user))

    @staticmethod
    def _payload(user) -> dict:
        body = user_payload(user)
        body['is_profile_complete'] = user.is_profile_complete
        return body
