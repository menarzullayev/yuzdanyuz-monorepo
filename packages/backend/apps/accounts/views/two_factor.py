"""
Two-factor authentication management (all require a signed-in user).

  GET  /api/auth/2fa/                          {enabled, backup_codes_remaining}
  POST /api/auth/2fa/setup/                    -> {secret, otpauth_uri}
  POST /api/auth/2fa/confirm/                  {code} -> {backup_codes: [10]}
  POST /api/auth/2fa/disable/                  {code, password?}
  POST /api/auth/2fa/backup-codes/regenerate/  {code} -> {backup_codes: [10]}

Sign-in itself is handled by /api/auth/login/ (``need_code`` + ``code``).
"""

from rest_framework.response import Response

from apps.accounts.services import two_factor as tf
from apps.accounts.services.security_notifications import notify
from apps.accounts.views.base import AuthAPIView, api_error, field_errors
from apps.accounts.views.credentials import raw_text_field, throttled_response
from core.utils import login_throttle


def _guard(request):
    """Shared throttle for every endpoint that checks a code or password."""
    key = login_throttle.account_key(f'user:{request.user.pk}')
    try:
        login_throttle.check(key)
    except login_throttle.LoginThrottled as e:
        return key, throttled_response(e)
    return key, None


def _bad_code(key):
    login_throttle.register_failure(key)
    return api_error(
        'invalid_code',
        'Kod notoʻgʻri.',
        400,
        field_errors(code=[('invalid_code', 'The code is not valid.')]),
    )


class TwoFactorStatusView(AuthAPIView):
    def get(self, request):
        enabled = tf.is_enabled(request.user)
        return Response(
            {
                'enabled': enabled,
                'backup_codes_remaining': tf.backup_codes_remaining(request.user) if enabled else 0,
            }
        )


class TwoFactorSetupView(AuthAPIView):
    def post(self, request):
        try:
            return Response(tf.begin_setup(request.user))
        except tf.TwoFactorError as e:
            return api_error(e.code, str(e), 409)


class TwoFactorConfirmView(AuthAPIView):
    def post(self, request):
        key, blocked = _guard(request)
        if blocked:
            return blocked
        try:
            codes = tf.confirm_setup(request.user, raw_text_field(request.data, 'code'))
        except tf.TwoFactorError as e:
            if e.code == 'invalid_code':
                return _bad_code(key)
            return api_error(e.code, str(e), 409 if e.code == 'already_enabled' else 400)
        login_throttle.reset(key)
        return Response({'enabled': True, 'backup_codes': codes})


class TwoFactorDisableView(AuthAPIView):
    def post(self, request):
        user = request.user
        key, blocked = _guard(request)
        if blocked:
            return blocked
        if not tf.is_enabled(user):
            return api_error('not_enabled', 'Two-factor authentication is not enabled.', 409)
        if user.has_usable_password() and not user.check_password(
            raw_text_field(request.data, 'password')
        ):
            login_throttle.register_failure(key)
            return api_error(
                'invalid_credentials',
                'Parol notoʻgʻri.',
                400,
                field_errors(password=[('invalid_password', 'Wrong password.')]),
            )
        if not tf.verify_second_factor(user, raw_text_field(request.data, 'code')):
            return _bad_code(key)
        login_throttle.reset(key)
        tf.disable(user)
        notify(user, 'two_factor_disabled')
        return Response({'enabled': False})


class BackupCodesRegenerateView(AuthAPIView):
    def post(self, request):
        key, blocked = _guard(request)
        if blocked:
            return blocked
        if not tf.is_enabled(request.user):
            return api_error('not_enabled', 'Two-factor authentication is not enabled.', 409)
        if not tf.verify_second_factor(request.user, raw_text_field(request.data, 'code')):
            return _bad_code(key)
        login_throttle.reset(key)
        return Response({'backup_codes': tf.generate_backup_codes(request.user)})
