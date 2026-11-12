"""
Account linking (profile screen). Every endpoint needs a signed-in user.

  GET    /api/auth/linked/                   sign-in methods + what can be unlinked
  POST   /api/auth/linked/telegram/          {init_data}: link from inside the Mini App
  POST   /api/auth/linked/telegram/start/    -> {token, deep_link}: link through the bot
                                             (poll GET /api/auth/tg/status/?token=)
  DELETE /api/auth/linked/telegram/          unlink Telegram
  DELETE /api/auth/linked/google/            unlink Google

Google is linked by sending the browser to ``google_connect_url`` (allauth "connect"
flow). A Telegram id is only ever taken from signed Mini App initData or from a
confirmation by the Telegram owner in the bot; never from a client-supplied number.
The last remaining sign-in method cannot be unlinked.
"""

from django.db import transaction
from rest_framework.response import Response

from apps.accounts.models import CustomUser
from apps.accounts.services import linking
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.telegram_auth import TelegramAuthError, verify_init_data
from apps.accounts.views.base import AuthAPIView, api_error
from apps.accounts.views.credentials import raw_text_field


def linked_payload(user) -> dict:
    google = linking.google_account(user)
    return {
        'username': user.username,
        'has_password': user.has_usable_password(),
        'telegram': {
            'linked': bool(user.telegram_id),
            'username': user.telegram_username or None,
        },
        'google': {
            'linked': google is not None,
            'email': (google.extra_data or {}).get('email') if google else None,
        },
        'can_unlink': {
            'telegram': linking.can_unlink(user, 'telegram'),
            'google': linking.can_unlink(user, 'google'),
        },
        'google_connect_url': '/accounts/google/login/?process=connect',
    }


class LinkedAccountsView(AuthAPIView):
    def get(self, request):
        return Response(linked_payload(request.user))


class TelegramLinkView(AuthAPIView):
    """POST links from signed Mini App initData; DELETE unlinks."""

    def post(self, request):
        init_data = raw_text_field(request.data, 'init_data').strip()
        if not init_data:
            return api_error('validation_error', 'init_data majburiy', 400)
        try:
            tg_user = verify_init_data(init_data)
            telegram_id = int(tg_user['id'])
        except (TelegramAuthError, KeyError, TypeError, ValueError):
            return api_error('telegram_not_verified', "Telegram ma'lumotlari tasdiqlanmadi", 400)
        try:
            linking.link_telegram(request.user, telegram_id, tg_user)
        except linking.TelegramLinkError as e:
            return api_error(e.code, str(e), 409)
        return Response(linked_payload(request.user))

    def delete(self, request):
        # Row lock: two parallel unlinks must not each see "another method remains".
        with transaction.atomic():
            user = CustomUser.objects.select_for_update().get(pk=request.user.pk)
            if not user.telegram_id:
                return api_error('not_linked', 'Telegram is not linked.', 404)
            if not linking.can_unlink(user, 'telegram'):
                return api_error('last_method', "Oxirgi kirish usulini ajratib bo'lmaydi.", 400)
            linking.unlink_telegram(user)
        return Response(linked_payload(user))


class LinkTelegramStartView(AuthAPIView):
    def post(self, request):
        if request.user.telegram_id:
            return api_error('already_linked', 'Telegram is already linked.', 409)
        return Response(dl.create_session(link_user_pk=str(request.user.pk)))


class UnlinkGoogleView(AuthAPIView):
    def delete(self, request):
        with transaction.atomic():
            user = CustomUser.objects.select_for_update().get(pk=request.user.pk)
            google = linking.google_account(user)
            if google is None:
                return api_error('not_linked', 'Google is not linked.', 404)
            if not linking.can_unlink(user, 'google'):
                return api_error('last_method', "Oxirgi kirish usulini ajratib bo'lmaydi.", 400)
            google.delete()
        return Response(linked_payload(user))
