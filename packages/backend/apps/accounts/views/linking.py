"""
Account linking — Telegram + Phone + Email + Google ni bir akkauntga bog'lash
"""

from django.http import JsonResponse
from django.views import View
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib.auth import get_user_model

User = get_user_model()


@method_decorator(login_required, name='dispatch')
class LinkPhoneView(View):
    """Phone raqamni akkauntga bog'lash"""

    def post(self, request):
        phone = request.POST.get('phone', '').strip()
        if not phone:
            return JsonResponse({'error': 'Phone majburiy'}, status=400)

        from apps.accounts.services.phone_utils import normalize_phone, PhoneValidationError

        try:
            normalized = normalize_phone(phone)
        except PhoneValidationError as e:
            return JsonResponse({'error': str(e)}, status=400)

        # Boshqa foydalanuvchi allaqachon bu raqamdan foydalanayotganmi?
        if User.objects.filter(phone_number=normalized).exclude(pk=request.user.pk).exists():
            return JsonResponse({'error': 'Bu raqam boshqa akkauntga bog\'langan'}, status=400)

        request.user.phone_number = normalized
        request.user.save(update_fields=['phone_number'])

        return JsonResponse({'success': True, 'phone': normalized})


@method_decorator(login_required, name='dispatch')
class LinkTelegramView(View):
    """Telegram ID ni akkauntga bog'lash"""

    def post(self, request):
        telegram_id = request.POST.get('telegram_id', '').strip()
        if not telegram_id:
            return JsonResponse({'error': 'Telegram ID majburiy'}, status=400)

        try:
            telegram_id = int(telegram_id)
        except ValueError:
            return JsonResponse({'error': 'Telegram ID raqam bo\'lishi kerak'}, status=400)

        # Boshqa foydalanuvchi allaqachon bu Telegram ID dan foydalanayotganmi?
        if User.objects.filter(telegram_id=telegram_id).exclude(pk=request.user.pk).exists():
            return JsonResponse({'error': 'Bu Telegram ID boshqa akkauntga bog\'langan'}, status=400)

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
            return JsonResponse({'error': 'Email ajratib bo\'lmaydi'}, status=400)

        else:
            return JsonResponse({'error': 'Noto\'g\'ri method'}, status=400)


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
        google = SocialAccount.objects.filter(
            user=user,
            provider='google'
        ).first()
        linked['google'] = google is not None

        return JsonResponse(linked)
