"""
Telegram Deep Link Sign In endpoints + Bot webhook.

Endpoints:
  POST /api/auth/tg/init/       — sessiya yaratish, deep_link qaytarish
  GET  /api/auth/tg/status/     — sessiya holati (frontend polling uchun)
  POST /api/auth/tg/complete/   — verified sessiyadan JWT cookie olish
  POST /api/bot/webhook/        — Telegram bot webhook
"""

import hmac
import json
import logging
import secrets

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.authentication import origin_allowed
from apps.accounts.models import CustomUser
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.bot_sender import (
    answer_callback,
    send_contact_request,
    send_link_confirmation,
    send_message,
    send_success_message,
)
from apps.accounts.services.token_service import attach_session, issue_login
from apps.accounts.services.user_agents import parse_user_agent
from apps.accounts.views.credentials import flag_field
from apps.accounts.views.payloads import user_payload
from core.utils.client_ip import get_client_ip

log = logging.getLogger(__name__)

NONCE_COOKIE = 'tg_nonce'


# ── 1. Sessiya yaratish ───────────────────────────────────────


@method_decorator(csrf_exempt, name='dispatch')
class TGDeeplinkInitView(View):
    """
    POST /api/auth/tg/init/
    ← {token, deep_link}
    """

    def post(self, request):
        try:
            body = json.loads(request.body or b'{}')
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        acquisition = body.get('acquisition') if isinstance(body, dict) else None
        nonce = secrets.token_urlsafe(32)
        ua = request.META.get('HTTP_USER_AGENT', '')
        requester = {'ip': get_client_ip(request), 'label': parse_user_agent(ua).label}
        session = dl.create_session(
            acquisition=acquisition, nonce_hash=dl.hash_nonce(nonce), requester=requester
        )
        response = JsonResponse(session)
        # Only this browser can finish the sign-in: a link forwarded to someone else
        # (or an attacker who learned the token) cannot complete it elsewhere.
        response.set_cookie(
            NONCE_COOKIE,
            nonce,
            max_age=dl.SESSION_TTL,
            httponly=True,
            secure=request.is_secure() or not settings.DEBUG,
            samesite='Lax',
            path='/api/auth/tg/',
        )
        return response


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
        if not origin_allowed(request):
            return JsonResponse({'error': 'origin_not_allowed'}, status=403)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        token = body.get('token', '').strip()
        if not token:
            return JsonResponse({'error': 'token majburiy'}, status=400)

        if not dl.nonce_matches(token, request.COOKIES.get(NONCE_COOKIE, '')):
            return JsonResponse(
                {'error': "Sessiya bu brauzerda boshlanmagan yoki muddati o'tgan"}, status=401
            )

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

        issued = issue_login(request, user, remember=flag_field(body, 'remember', True))

        response = JsonResponse({'user': user_payload(user)})
        attach_session(response, issued, request)
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
        # Fail closed: without a configured secret anyone could forge updates.
        secret = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or ''
        received = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
        if not secret or not hmac.compare_digest(received.encode(), secret.encode()):
            return JsonResponse({'error': 'Forbidden'}, status=403)

        try:
            update = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'ok': True})

        if update.get('callback_query'):
            self._handle_callback(update['callback_query'])
            return JsonResponse({'ok': True})

        message = update.get('message', {})
        if not message:
            return JsonResponse({'ok': True})

        from_user = message.get('from', {})
        tg_id = from_user.get('id')
        if not tg_id:
            return JsonResponse({'ok': True})

        text = message.get('text', '')

        # ── /reset: one-time password recovery link for the sender's own account ──
        if text.split('@', 1)[0].strip() == '/reset':
            self._send_recovery_link(tg_id)
            return JsonResponse({'ok': True})

        # ── /start tgauth_TOKEN ──────────────────────────────
        if text.startswith('/start'):
            parts = text.split(maxsplit=1)
            if len(parts) == 2 and parts[1].startswith('tgauth_'):
                token = parts[1][len('tgauth_') :]
                ok = dl.on_bot_start(token, tg_id)
                if ok:
                    target = dl.link_target(token)
                    if target is not None:
                        send_link_confirmation(tg_id, target.display_name, token)
                    else:
                        send_contact_request(tg_id, dl.requester_text(token))
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

            ok = dl.on_bot_contact(
                tg_id,
                phone,
                contact_uid,
                first_name=from_user.get('first_name', '') or '',
                last_name=from_user.get('last_name', '') or '',
            )
            if ok:
                send_success_message(tg_id)
            else:
                send_message(tg_id, "⚠️ Tasdiqlashda xatolik. Web saytda qayta urinib ko'ring.")
            return JsonResponse({'ok': True})

        return JsonResponse({'ok': True})

    @staticmethod
    def _send_recovery_link(tg_id: int) -> None:
        from apps.accounts.services import recovery
        from apps.accounts.services.security_notifications import render

        user = CustomUser.objects.filter(telegram_id=tg_id, is_active=True).first()
        if user is None:
            send_message(tg_id, "Bu Telegram bilan bog'langan hisob topilmadi.")
        elif not recovery.allow_request(f'tg:{tg_id}'):
            send_message(tg_id, "Juda ko'p so'rov. Keyinroq urinib ko'ring.")
        else:
            link = recovery.recovery_link(recovery.create_token(user))
            send_message(tg_id, render('recovery_link', link=link))

    @staticmethod
    def _handle_callback(cb: dict) -> None:
        """Inline-button presses of the link confirmation (only the chat owner can match)."""
        data = cb.get('data', '') or ''
        sender = cb.get('from') or {}
        from_id = sender.get('id')
        cb_id = cb.get('id', '')
        if not from_id:
            return
        if data.startswith('tglink:'):
            result = dl.on_link_confirm(data[len('tglink:') :], from_id, sender)
            texts = {
                'linked': '✅ Telegram hisobingizga ulandi.',
                'conflict': '⚠️ Bu Telegram boshqa hisobga ulangan yoki hisobingizda Telegram bor.',
                'invalid': "⚠️ Havola muddati o'tgan. Saytda qayta urinib ko'ring.",
            }
            answer_callback(cb_id, texts[result])
            send_message(from_id, texts[result])
        elif data.startswith('tgcancel:'):
            dl.on_link_cancel(data[len('tgcancel:') :], from_id)
            answer_callback(cb_id, 'Bekor qilindi.')
