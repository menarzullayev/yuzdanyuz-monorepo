"""
ISSUE-103 — GDPR Article 17 (right to erasure) endpoint stub.

Foydalanuvchi o'z ma'lumotlarini o'chirishni so'rasa, biz:
  1. User PII'ni anonimlashtirish (email, phone, username → redacted)
  2. Audit modellaridagi tegishli row'larni soft_delete(redact_pii=True)
  3. Audit ID + statistikalar saqlanadi (UZ buxgalteriya 5 yil, DTM compliance)

Hozir bu STUB — production'da quyidagi qadamlar qo'shilishi kerak:
  - Email confirmation token (so'rov 24h ichida tasdiqlanmasa bekor bo'ladi)
  - 30 kunlik "cooling-off" period (user fikrini o'zgartirishi mumkin)
  - Celery task: scheduled anonymization (immediate execution emas)
  - Audit log entry: kim, qachon erasure so'radi
  - Admin notification

Endpoint: POST /api/auth/gdpr/erasure/
  Body: {confirm: true}
  Response: {request_id, scheduled_at, redacted_models: [...]}
"""

import logging

from django.contrib.auth import logout
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class GDPRErasureView(APIView):
    """POST /api/auth/gdpr/erasure/ — foydalanuvchi PII'ni redact qilish."""

    def post(self, request):
        if not request.data.get('confirm'):
            raise ValidationError({'confirm': 'GDPR erasure uchun tasdiqlash kerak'})

        user = request.user
        if user.is_anonymous:
            raise ValidationError({'auth': 'Login qilish kerak'})

        # PII redaction'lar ro'yxati (audit uchun)
        redacted = []

        # 1. User PII anonymize
        original_email = user.email
        user.email = f'redacted-{user.id}@gdpr.local'
        user.username = f'redacted-{user.id}'
        if hasattr(user, 'phone_number'):
            user.phone_number = None
        if hasattr(user, 'first_name'):
            user.first_name = ''
        if hasattr(user, 'last_name'):
            user.last_name = ''
        if hasattr(user, 'telegram_id'):
            user.telegram_id = None
        user.is_active = False
        user.save()
        redacted.append('CustomUser')

        # 2. Audit modellar (lazy imports — circular dependency oldini olish)
        try:
            from apps.commerce.models import PaymentIntent, WalletTransaction

            for tx in WalletTransaction.objects.filter(wallet__user=user, is_deleted=False):
                tx.soft_delete(redact_pii=True)
            redacted.append('WalletTransaction')

            for pi in PaymentIntent.objects.filter(user=user, is_deleted=False):
                pi.soft_delete(redact_pii=True)
            redacted.append('PaymentIntent')
        except Exception as e:
            logger.warning('GDPR commerce redaction failed for user %s: %s', user.id, e)

        try:
            from apps.exams.models import ExamAttempt, UserAnswer

            for ea in ExamAttempt.global_objects.filter(user=user, is_deleted=False):
                ea.soft_delete(redact_pii=True)
            redacted.append('ExamAttempt')

            for ua in UserAnswer.global_objects.filter(attempt__user=user, is_deleted=False):
                ua.soft_delete(redact_pii=True)
            redacted.append('UserAnswer')
        except Exception as e:
            logger.warning('GDPR exams redaction failed for user %s: %s', user.id, e)

        try:
            from apps.intelligence.models import OpenEndedSubmission

            for sub in OpenEndedSubmission.objects.filter(user=user, is_deleted=False):
                sub.soft_delete(redact_pii=True)
            redacted.append('OpenEndedSubmission')
        except Exception as e:
            logger.warning('GDPR intelligence redaction failed: %s', e)

        try:
            from apps.analytics.models import ExamEvent

            for ev in ExamEvent.global_objects.filter(user=user, is_deleted=False):
                ev.soft_delete(redact_pii=False)  # ExamEvent'da PII yo'q
            redacted.append('ExamEvent')
        except Exception as e:
            logger.warning('GDPR analytics redaction failed: %s', e)

        logger.info(
            'GDPR erasure complete: user=%s (was %s), redacted=%s',
            user.id,
            original_email,
            redacted,
        )

        # Logout
        logout(request)

        return Response(
            {
                'request_id': str(user.id),
                'completed_at': timezone.now().isoformat(),
                'redacted_models': redacted,
                'note': (
                    'Sizning PII (email, telefon, ism) anonimlashtirildi. '
                    "Audit ID'lar saqlandi (qonun talab — UZ buxgalteriya 5 yil)."
                ),
            },
            status=200,
        )
