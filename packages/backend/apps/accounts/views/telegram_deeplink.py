"""
Telegram Deep Link Sign In endpoints + Bot webhook.

Endpoints:
  POST /api/auth/tg/init/       — sessiya yaratish, deep_link qaytarish
  GET  /api/auth/tg/status/     — sessiya holati (frontend polling uchun)
  POST /api/auth/tg/complete/   — verified sessiyadan JWT cookie olish
  POST /api/bot/webhook/        — Telegram bot webhook
"""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import CustomUser
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.bot_sender import (
    send_contact_request,
    send_message,
    send_success_message,
)
from apps.accounts.services.token_service import (
    create_token_pair,
    make_fingerprint,
    set_auth_cookies,
)

log = logging.getLogger(__name__)


def _is_secure(request) -> bool:
    return request.is_secure() or not settings.DEBUG


# ── 1. Sessiya yaratish ───────────────────────────────────────


@method_decorator(csrf_exempt, name='dispatch')
class TGDeeplinkInitView(View):
    """
    POST /api/auth/tg/init/
    ← {token, deep_link}
    """

    def post(self, request):
        session = dl.create_session()
        return JsonResponse(session)


# ── 2. Holat tekshirish (polling) ─────────────────────────────


class TGDeeplinkStatusView(View):
    """
    GET /api/auth/tg/status/?token=TOKEN
    ← {status: 'pending' | 'waiting' | 'verified' | 'expired'}
    """

    def get(self, request):
        token = request.GET.get('token', '').strip()
        session = dl.get_session(token)
        if session is None:
            return JsonResponse({'status': 'expired'})
        return JsonResponse({'status': session['status']})


# ── 3. Verified sessiyadan JWT olish ─────────────────────────


@method_decorator(csrf_exempt, name='dispatch')
class TGDeeplinkCompleteView(View):
    """
    POST /api/auth/tg/complete/
    Body: {token}
    ← JWT httpOnly cookie (OTP kiritish kerak emas)
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        token = body.get('token', '').strip()
        if not token:
            return JsonResponse({'error': 'token majburiy'}, status=400)

        user_pk = dl.get_verified_user_pk(token)
        if user_pk is None:
            return JsonResponse(
                {'error': "Sessiya tasdiqlanmagan yoki muddati o'tgan"},
                status=401,
            )

        try:
            user = CustomUser.objects.get(pk=user_pk)
        except CustomUser.DoesNotExist:
            return JsonResponse({'error': 'Foydalanuvchi topilmadi'}, status=404)

        if not user.is_active:
            return JsonResponse({'error': 'Akkaunt bloklangan'}, status=403)

        fingerprint = make_fingerprint(request)
        access, refresh = create_token_pair(user, fingerprint)

        response = JsonResponse(
            {
                'user': {
                    'id': str(user.pk),
                    'display_name': user.display_name,
                    'user_type': user.user_type,
                    'phone': user.phone_number,
                }
            }
        )
        set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
        dl.expire_session(token)
        return response


# ── 4. Bot Webhook ────────────────────────────────────────────


@method_decorator(csrf_exempt, name='dispatch')
class BotWebhookView(View):
    """
    POST /api/bot/webhook/

    Ikki turdagi update ishlaydi:
      • /start tgauth_TOKEN  — klaviatura yuboradi
      • contact message      — telefon tasdiqlash, sessiya verified
    """

    def post(self, request):
        secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '')
        if secret and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != secret:
            return JsonResponse({'error': 'Forbidden'}, status=403)

        try:
            update = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'ok': True})

        message = update.get('message', {})
        if not message:
            return JsonResponse({'ok': True})

        from_user = message.get('from', {})
        tg_id = from_user.get('id')
        if not tg_id:
            return JsonResponse({'ok': True})

        # ── /start tgauth_TOKEN ──────────────────────────────
        text = message.get('text', '')
        if text.startswith('/start'):
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith('tgauth_'):
                token = parts[1][len('tgauth_') :]
                ok = dl.on_bot_start(token, tg_id)
                if ok:
                    send_contact_request(tg_id)
                else:
                    send_message(
                        tg_id,
                        "⚠️ Havola muddati o'tgan yoki noto'g'ri. Web saytda qayta urinib ko'ring.",
                    )
            return JsonResponse({'ok': True})

        # ── Contact (telefon raqam) ───────────────────────────
        contact = message.get('contact')
        if contact:
            phone = contact.get('phone_number', '')
            contact_uid = contact.get('user_id')

            ok = dl.on_bot_contact(tg_id, phone, contact_uid)
            if ok:
                send_success_message(tg_id)
            else:
                send_message(tg_id, "⚠️ Tasdiqlashda xatolik. Web saytda qayta urinib ko'ring.")
            return JsonResponse({'ok': True})

        return JsonResponse({'ok': True})
