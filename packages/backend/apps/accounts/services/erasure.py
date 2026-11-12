"""GDPR art. 17 erasure: request, confirm, cooling-off, anonymise (C22).

Email is optional on this platform, so the confirmation factor is the account password or,
without one, a 6-digit code sent by the Telegram bot. After confirmation the request waits
GDPR_ERASURE_COOLING_OFF_HOURS (cancellable), then `process_due_erasures` anonymises the
account in one transaction: a failure rolls everything back, is recorded on the request and
logged with its traceback, and the request stays FAILED until an admin reschedules it.

What is kept, and why (docs/privacy/data-retention.md): the anonymised user row (foreign keys
of exam results and wallet transactions point at it; accounting records are kept 5 years),
soft-deleted and PII-redacted commerce/exam rows, and the ErasureRequest itself as the audit
entry. Nothing that names or contacts the person survives, including simple_history rows.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    BackupCode,
    CustomUser,
    DeviceSession,
    ErasureRequest,
    ExamCertificate,
    OnboardingProfile,
    OTPCode,
    PasswordResetRequest,
    RecoveryToken,
    TOTPDevice,
)

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=15)
MAX_CODE_ATTEMPTS = 5
OPEN = (ErasureRequest.Status.PENDING, ErasureRequest.Status.SCHEDULED)


class ErasureError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def cooling_off() -> timedelta:
    return timedelta(hours=int(getattr(settings, 'GDPR_ERASURE_COOLING_OFF_HOURS', 72)))


def _code_hash(req: ErasureRequest, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f'gdpr-erasure:{req.pk}:{code}'.encode(), hashlib.sha256).hexdigest()


def open_request(user: CustomUser) -> ErasureRequest | None:
    return ErasureRequest.objects.filter(user=user, status__in=OPEN).first()


def _schedule(req: ErasureRequest, now: datetime) -> None:
    req.status = ErasureRequest.Status.SCHEDULED
    req.confirmed_at = now
    req.scheduled_for = now + cooling_off()
    req.code_hash = ''
    req.code_expires_at = None


def start(user: CustomUser, password: str | None) -> ErasureRequest:
    """Create (or restart) the user's request. Password -> scheduled at once;
    otherwise a Telegram code is sent and the request waits for it."""
    now = timezone.now()
    if password:
        if not user.has_usable_password() or not user.check_password(password):
            raise ErasureError('invalid_password', 'Parol noto‘g‘ri.')
        via = ErasureRequest.ConfirmVia.PASSWORD
    elif user.telegram_id:
        via = ErasureRequest.ConfirmVia.TELEGRAM
    else:
        raise ErasureError(
            'reauth_required',
            'Tasdiqlash uchun parolingizni kiriting yoki Telegram hisobingizni ulang.',
        )

    with transaction.atomic():
        existing = open_request(user)
        if existing and existing.status == ErasureRequest.Status.SCHEDULED:
            return existing
        req = existing or ErasureRequest(user=user, user_ref=user.pk)
        req.confirm_via = via
        req.requested_at = now
        req.code_attempts = 0
        if via == ErasureRequest.ConfirmVia.PASSWORD:
            _schedule(req, now)
            req.save()
            return req
        req.status = ErasureRequest.Status.PENDING
        req.save()
        code = f'{secrets.randbelow(10**6):06d}'
        req.code_hash = _code_hash(req, code)
        req.code_expires_at = now + CODE_TTL
        req.save(update_fields=['code_hash', 'code_expires_at'])

    from apps.accounts.services import bot_sender

    text = (
        f'Hisobingizni o‘chirish so‘rovi. Tasdiqlash kodi: <b>{code}</b>\n'
        f'Kod 15 daqiqa amal qiladi. So‘rovni siz yubormagan bo‘lsangiz, '
        f'kodni hech kimga bermang.'
    )
    if not bot_sender.send_message(user.telegram_id, text):
        raise ErasureError('telegram_unavailable', 'Telegram orqali kod yuborib bo‘lmadi.')
    return req


def confirm(user: CustomUser, code: str) -> ErasureRequest:
    error = None
    with transaction.atomic():
        req = (
            ErasureRequest.objects.select_for_update()
            .filter(user=user, status=ErasureRequest.Status.PENDING)
            .first()
        )
        if req is None:
            raise ErasureError('no_pending_request', 'Tasdiqlanadigan so‘rov yo‘q.')
        now = timezone.now()
        if (
            req.code_attempts >= MAX_CODE_ATTEMPTS
            or not req.code_expires_at
            or now > req.code_expires_at
        ):
            raise ErasureError('code_expired', 'Kod eskirgan. Yangi so‘rov yuboring.')
        req.code_attempts += 1
        if hmac.compare_digest(req.code_hash, _code_hash(req, (code or '').strip())):
            _schedule(req, now)
            req.save()
        else:
            # the attempt counter must be committed, so raise only after the block
            req.save(update_fields=['code_attempts'])
            error = ErasureError('invalid_code', 'Kod noto‘g‘ri.')
    if error:
        raise error
    return req


def cancel(user: CustomUser) -> ErasureRequest:
    with transaction.atomic():
        req = ErasureRequest.objects.select_for_update().filter(user=user, status__in=OPEN).first()
        if req is None:
            raise ErasureError('no_open_request', 'Bekor qilinadigan so‘rov yo‘q.')
        req.status = ErasureRequest.Status.CANCELLED
        req.cancelled_at = timezone.now()
        req.code_hash = ''
        req.save()
        return req


# ── Execution ───────────────────────────────────────────────


def _manager(model):
    return getattr(model, 'global_objects', model._default_manager)


def _anonymise_user(user: CustomUser) -> None:
    tag = f'deleted-{user.pk.hex[:12]}'
    user.username = tag
    user.email = None
    user.phone_number = None
    user.first_name = ''
    user.last_name = ''
    user.telegram_id = None
    user.telegram_username = None
    user.telegram_photo_url = None
    user.telegram_language = None
    user.tg_linked_at = None
    user.birth_year = None
    user.is_active = False
    user.set_unusable_password()
    user.skip_history_when_saving = True
    try:
        user.save()
    finally:
        del user.skip_history_when_saving


def _purge_history(user: CustomUser) -> None:
    """simple_history keeps every earlier version (old email, phone, name, username)."""
    user.history.all().delete()


def _delete_account_data(user: CustomUser, phone: str | None) -> list[str]:
    done = []
    for model in (
        DeviceSession,
        TOTPDevice,
        BackupCode,
        RecoveryToken,
        PasswordResetRequest,
        OnboardingProfile,
        ExamCertificate,
    ):
        model.objects.filter(user=user).delete()
        done.append(model.__name__)
    if phone:
        OTPCode.objects.filter(phone=phone).delete()
        done.append('OTPCode')

    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount

    EmailAddress.objects.filter(user=user).delete()
    SocialAccount.objects.filter(user=user).delete()  # cascades to SocialToken
    done += ['EmailAddress', 'SocialAccount']
    return done


def _redact_activity(user: CustomUser) -> list[str]:
    from apps.analytics.models import ExamEvent
    from apps.commerce.models import PaymentIntent, WalletTransaction
    from apps.exams.models import ExamAttempt, UserAnswer
    from apps.intelligence.models import OpenEndedSubmission

    done = []
    for name, model, flt, redact in (
        ('WalletTransaction', WalletTransaction, {'wallet__user': user}, True),
        ('PaymentIntent', PaymentIntent, {'user': user}, True),
        ('ExamAttempt', ExamAttempt, {'user': user}, True),
        ('UserAnswer', UserAnswer, {'attempt__user': user}, True),
        ('OpenEndedSubmission', OpenEndedSubmission, {'user': user}, True),
        ('ExamEvent', ExamEvent, {'user': user}, False),
    ):
        for row in _manager(model).filter(is_deleted=False, **flt):
            row.soft_delete(redact_pii=redact)
        done.append(name)
    return done


def erase(req: ErasureRequest, now: datetime) -> None:
    """Anonymise req.user. Atomic: on any error nothing is changed."""
    user = req.user
    with transaction.atomic():
        phone = user.phone_number
        avatar = user.avatar.name if user.avatar else ''
        user.avatar = None
        _anonymise_user(user)
        redacted = ['CustomUser']
        _purge_history(user)
        redacted.append('HistoricalCustomUser')
        redacted += _delete_account_data(user, phone)
        redacted += _redact_activity(user)
        if avatar:
            # Last step: a file delete cannot be rolled back, so nothing may fail after it.
            CustomUser._meta.get_field('avatar').storage.delete(avatar)
            redacted.append('avatar')
        req.status = ErasureRequest.Status.COMPLETED
        req.completed_at = now
        req.redacted = redacted
        req.error = ''
        req.save()


def run_due(now: datetime | None = None, *, raise_errors: bool = False) -> dict:
    """Erase every request whose cooling-off has ended. Each request is its own transaction."""
    now = now or timezone.now()
    stats = {'completed': 0, 'failed': 0}
    due = ErasureRequest.objects.filter(
        status=ErasureRequest.Status.SCHEDULED, scheduled_for__lte=now
    ).values_list('pk', flat=True)
    for pk in list(due):
        try:
            with transaction.atomic():
                req = (
                    ErasureRequest.objects.select_for_update(skip_locked=True, of=('self',))
                    .select_related('user')
                    .filter(pk=pk, status=ErasureRequest.Status.SCHEDULED)
                    .first()
                )
                if req is None:
                    continue
                erase(req, now)
        except Exception as exc:
            # Rolled back as a whole; record why so an admin can fix and reschedule.
            log.exception('GDPR erasure %s failed', pk)
            ErasureRequest.objects.filter(pk=pk).update(
                status=ErasureRequest.Status.FAILED, error=f'{type(exc).__name__}: {exc}'[:2000]
            )
            stats['failed'] += 1
            if raise_errors:
                raise
            continue
        log.info('GDPR erasure %s completed', pk)
        stats['completed'] += 1
    return stats
