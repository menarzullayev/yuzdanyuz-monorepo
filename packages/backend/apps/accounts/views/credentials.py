"""
Username + password auth (JSON API).

  POST /api/auth/register/             create account + sign in
  POST /api/auth/login/                sign in
  GET  /api/auth/username/check/       availability + suggestions
  POST /api/auth/username/             change username (one free change for auto usernames)
  POST /api/auth/password/change/      change (or first-time set) the password
"""

import logging
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.accounts.models import CustomUser
from apps.accounts.services import two_factor
from apps.accounts.services import usernames as un
from apps.accounts.services.password_policy import password_errors
from apps.accounts.services.security_notifications import notify, notify_account_locked
from apps.accounts.services.token_service import (
    attach_session,
    issue_login,
    revoke_user_sessions,
)
from apps.accounts.views.base import AuthAPIView, PublicAPIView, api_error, field_errors
from apps.accounts.views.payloads import user_payload
from core.utils import login_throttle

log = logging.getLogger(__name__)

_DUMMY_HASH: str | None = None

MSG_INVALID = "Foydalanuvchi nomi yoki parol noto'g'ri."


def _burn_password_check(password: str) -> None:
    """Spend the time of a real check so unknown users cannot be told apart by latency."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = make_password('not-a-real-password')
    check_password(password, _DUMMY_HASH)


def text_field(data, key: str) -> str:
    value = data.get(key, '')
    return value.strip() if isinstance(value, str) else ''


def raw_text_field(data, key: str) -> str:
    """Like text_field but keeps surrounding whitespace (passwords)."""
    value = data.get(key, '')
    return value if isinstance(value, str) else ''


def flag_field(data, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)


def throttled_response(exc: login_throttle.LoginThrottled) -> Response:
    resp = api_error(
        'account_locked',
        "Juda ko'p noto'g'ri urinish. Iltimos, biroz kutib qayta urinib ko'ring.",
        429,
        retry_after=exc.retry_after,
    )
    resp['Retry-After'] = str(exc.retry_after)
    return resp


def finish_login(request, user: CustomUser, *, remember: bool, status: int = 200, **extra):
    """Issue the device session and build the success response with cookies."""
    issued = issue_login(request, user, remember=remember)
    response = Response({'user': user_payload(user), **extra}, status=status)
    attach_session(response, issued, request)
    return response


def _taken_error(name: str):
    return api_error(
        'validation_error',
        'Maʼlumotlar notoʻgʻri.',
        400,
        field_errors(username=[('username_taken', 'This username is already taken.')]),
        suggestions=un.suggest_usernames(name),
    )


# ── Register ────────────────────────────────────────────────


class RegisterView(PublicAPIView):
    def post(self, request):
        data = request.data
        first = text_field(data, 'first_name')
        last = text_field(data, 'last_name')
        username = un.normalize_username(text_field(data, 'username'))
        password = raw_text_field(data, 'password')
        email = text_field(data, 'email').lower()

        errors: dict[str, list[tuple[str, str]]] = {}
        for key, val in (('first_name', first), ('last_name', last)):
            if not 2 <= len(val) <= 60:
                errors.setdefault(key, []).append(
                    (f'{key}_length', 'Must be between 2 and 60 characters.')
                )

        suggestions: list[str] = []
        fmt = un.username_format_error(username)
        if fmt:
            errors.setdefault('username', []).append(fmt)
        elif un.is_taken(username):
            errors.setdefault('username', []).append(
                ('username_taken', 'This username is already taken.')
            )
            suggestions = un.suggest_usernames(username)

        if email:
            try:
                validate_email(email)
            except DjangoValidationError:
                errors.setdefault('email', []).append(('email_invalid', 'Enter a valid email.'))
            else:
                if CustomUser.objects.filter(email__iexact=email).exists():
                    errors.setdefault('email', []).append(
                        ('email_taken', 'This email is already in use.')
                    )

        if password:
            probe = CustomUser(
                username=username, first_name=first, last_name=last, email=email or None
            )
            pw_errs = password_errors(password, user=probe)
        else:
            pw_errs = [('password_required', 'Password is required.')]
        if pw_errs:
            errors['password'] = pw_errs

        if errors:
            extra = {'suggestions': suggestions} if suggestions else {}
            return api_error(
                'validation_error', 'Maʼlumotlar notoʻgʻri.', 400, field_errors(**errors), **extra
            )

        user = CustomUser(
            username=username,
            first_name=first,
            last_name=last,
            email=email or None,
            terms_version=settings.TERMS_VERSION,
            terms_accepted_at=timezone.now(),
        )
        user.set_password(password)
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError:
            # Lost a race for the username/email; never echo database text.
            return _taken_error(username)

        return finish_login(request, user, remember=flag_field(data, 'remember', True), status=201)


# ── Login ───────────────────────────────────────────────────


class LoginView(PublicAPIView):
    def post(self, request):
        data = request.data
        name = un.normalize_username(text_field(data, 'username'))
        password = raw_text_field(data, 'password')
        remember = flag_field(data, 'remember', False)

        target = CustomUser.objects.filter(username__iexact=name).first() if name else None
        key = login_throttle.account_key(f'user:{target.pk}' if target else f'login:{name}')
        try:
            login_throttle.check(key)
        except login_throttle.LoginThrottled as e:
            return throttled_response(e)

        user = None
        if target is not None and target.has_usable_password():
            if target.check_password(password):
                user = target
        else:
            _burn_password_check(password)

        if user is None or not user.is_active:
            locked_for = login_throttle.register_failure(key)
            if locked_for and target is not None:
                notify_account_locked(target, locked_for)
            return api_error('invalid_credentials', MSG_INVALID, 400)

        # Second factor: password alone never signs in an account that has 2FA.
        if two_factor.is_enabled(user):
            code = raw_text_field(data, 'code')
            if not code:
                return Response({'need_code': True})
            if not two_factor.verify_second_factor(user, code):
                locked_for = login_throttle.register_failure(key)
                if locked_for:
                    notify_account_locked(user, locked_for)
                return api_error('invalid_code', 'Kod notoʻgʻri.', 400)

        login_throttle.reset(key)
        return finish_login(request, user, remember=remember)


# ── Username ────────────────────────────────────────────────


class UsernameCheckView(PublicAPIView):
    def get(self, request):
        name = un.normalize_username(request.query_params.get('username', ''))
        err = un.username_format_error(name)
        if err:
            return Response(
                {'username': name, 'valid': False, 'available': False, 'reason': err[0]}
            )
        taken = un.is_taken(name)
        body = {'username': name, 'valid': True, 'available': not taken}
        if taken:
            body['reason'] = 'username_taken'
            body['suggestions'] = un.suggest_usernames(name)
        return Response(body)


class UsernameChangeView(AuthAPIView):
    def post(self, request):
        user = request.user
        if not (user.username_is_auto and user.username_changed_at is None):
            return api_error(
                'username_change_not_allowed', "Foydalanuvchi nomini o'zgartirib bo'lmaydi.", 403
            )
        name = un.normalize_username(text_field(request.data, 'username'))
        err = un.username_format_error(name)
        if err:
            return api_error('validation_error', err[1], 400, field_errors(username=[err]))
        if un.is_taken(name, exclude_pk=user.pk):
            return _taken_error(name)
        user.username = name
        user.username_changed_at = timezone.now()
        user.username_is_auto = False
        try:
            with transaction.atomic():
                user.save(update_fields=['username', 'username_changed_at', 'username_is_auto'])
        except IntegrityError:
            return _taken_error(name)
        return Response({'user': user_payload(user)})


# ── Password change ─────────────────────────────────────────


class PasswordChangeView(AuthAPIView):
    """Change the password; accounts without one (Telegram/Google) may set a first one."""

    def post(self, request):
        user = request.user
        new = raw_text_field(request.data, 'new_password')
        current = raw_text_field(request.data, 'current_password')

        if user.has_usable_password():
            key = login_throttle.account_key(f'user:{user.pk}')
            try:
                login_throttle.check(key)
            except login_throttle.LoginThrottled as e:
                return throttled_response(e)
            if not user.check_password(current):
                locked_for = login_throttle.register_failure(key)
                if locked_for:
                    notify_account_locked(user, locked_for)
                return api_error(
                    'invalid_credentials',
                    "Joriy parol noto'g'ri.",
                    400,
                    field_errors(current_password=[('invalid_password', 'Wrong password.')]),
                )
            login_throttle.reset(key)

        errs = password_errors(new, user=user) if new else [('password_required', 'Required.')]
        if errs:
            return api_error(
                'validation_error',
                'Yangi parol talablarga javob bermaydi.',
                400,
                field_errors(new_password=errs),
            )

        user.set_password(new)
        user.must_change_password = False
        user.save(update_fields=['password', 'must_change_password'])
        current_sid = getattr(request, 'session_sid', None)
        revoke_user_sessions(
            user,
            except_session_id=uuid.UUID(hex=current_sid) if current_sid else None,
            reason='password',
        )
        notify(user, 'password_changed')
        return Response({'ok': True})
