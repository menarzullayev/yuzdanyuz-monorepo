"""
Account linking — Telegram + Phone + Email + Google ni bir akkauntga bog'lash
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View

User = get_user_model()


@method_decorator(login_required, name='dispatch')
class LinkPhoneView(View):
    """Phone raqamni akkauntga bog'lash.

    Disabled: there is no phone verification step (no SMS OTP by design), so a
    client-supplied number cannot be trusted. A phone is attached only through
    the Telegram deep-link flow, where Telegram attests the number.
    """

    def post(self, request):
        return JsonResponse(
            {
                'error': "Telefon raqam faqat Telegram orqali tasdiqlanib bog'lanadi",
                'code': 'phone_link_requires_telegram',
            },
            status=410,
        )


@method_decorator(login_required, name='dispatch')
class LinkTelegramView(View):
    """Telegram ID ni akkauntga bog'lash.

    The Telegram id is taken only from Mini App ``init_data`` signed with the
    bot token; a bare ``telegram_id`` from the client proves nothing.
    """

    def post(self, request):
        init_data = request.POST.get('init_data', '').strip()
        if not init_data:
            return JsonResponse({'error': 'init_data majburiy'}, status=400)

        from apps.accounts.services.telegram_auth import TelegramAuthError, verify_init_data

        try:
            tg_user = verify_init_data(init_data)
            telegram_id = int(tg_user['id'])
        except (TelegramAuthError, KeyError, TypeError, ValueError):
            return JsonResponse({'error': "Telegram ma'lumotlari tasdiqlanmadi"}, status=400)

        # Boshqa foydalanuvchi allaqachon bu Telegram ID dan foydalanayotganmi?
        if User.objects.filter(telegram_id=telegram_id).exclude(pk=request.user.pk).exists():
            return JsonResponse({'error': "Bu Telegram ID boshqa akkauntga bog'langan"}, status=400)

        request.user.telegram_id = telegram_id
        request.user.save(update_fields=['telegram_id'])

        return JsonResponse({'success': True, 'telegram_id': telegram_id})


@method_decorator(login_required, name='dispatch')
class UnlinkAuthMethodView(View):
    """Auth methodini ajratib olish"""

    def post(self, request):
        method = request.POST.get('method', '').strip()

        if method == 'phone':
            request.user.phone_number = ''
            request.user.save(update_fields=['phone_number'])
            return JsonResponse({'success': True, 'message': 'Phone ajratildi'})

        elif method == 'telegram':
            request.user.telegram_id = None
            request.user.save(update_fields=['telegram_id'])
            return JsonResponse({'success': True, 'message': 'Telegram ajratildi'})

        elif method == 'email':
            # Email ajratib bo'lmaydi — har bir akkauntda email bo'lishi kerak
            return JsonResponse({'error': "Email ajratib bo'lmaydi"}, status=400)

        else:
            return JsonResponse({'error': "Noto'g'ri method"}, status=400)


@method_decorator(login_required, name='dispatch')
class GetLinkedAccountsView(View):
    """Bog'langan akkauntlarni ko'rsatish"""

    def get(self, request):
        user = request.user

        linked = {
            'email': user.email,
            'phone': user.phone_number or None,
            'telegram_id': user.telegram_id or None,
            'has_password': user.has_usable_password(),
        }

        # Google account (allauth)
        from allauth.socialaccount.models import SocialAccount

        google = SocialAccount.objects.filter(user=user, provider='google').first()
        linked['google'] = google is not None

        return JsonResponse(linked)
