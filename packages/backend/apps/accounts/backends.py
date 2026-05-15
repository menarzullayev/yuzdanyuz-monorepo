"""
Django authentication backend — JWT access token orqali.

Ishlatish:
    AUTHENTICATION_BACKENDS = [
        'apps.accounts.backends.JWTBackend',
        'django.contrib.auth.backends.ModelBackend',
    ]

    # Middleware yoki view da:
    user = authenticate(request, token=access_token)
"""

from django.contrib.auth.backends import BaseBackend

from apps.accounts.models import CustomUser
from apps.accounts.services.token_service import verify_access_token


class JWTBackend(BaseBackend):
    def authenticate(self, request, token: str | None = None, **kwargs):
        if not token:
            return None
        user_id = verify_access_token(token)
        if user_id is None:
            return None
        try:
            user = CustomUser.objects.get(pk=user_id, is_active=True)
            return user
        except CustomUser.DoesNotExist:
            return None

    def get_user(self, user_id):
        try:
            return CustomUser.objects.get(pk=user_id)
        except CustomUser.DoesNotExist:
            return None
