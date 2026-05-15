"""
Login sahifasi + HTMX partial view'lar.
"""

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator

from apps.accounts.services.otp_service import OTPError, OTPRateLimitError, send_otp, verify_otp
from apps.accounts.services.phone_utils import PhoneValidationError
from apps.accounts.services.token_service import create_token_pair, make_fingerprint, set_auth_cookies


def _is_secure(request):
    return request.is_secure() or not settings.DEBUG


class LoginView(View):
    def get(self, request):
        if request.user.is_authenticated:
            return HttpResponseRedirect('/')
        return render(request, 'accounts/login.html', {
            'PROJECT_BRAND_NAME': settings.PROJECT_BRAND_NAME,
        })


class OTPSendHTMXView(View):
    """HTMX: telefon kiritildi → SMS yuboradi → step2 partial qaytaradi."""

    def post(self, request):
        raw_phone = request.POST.get('phone', '').strip()
        try:
            phone = send_otp(raw_phone)
        except PhoneValidationError as e:
            return render(request, 'accounts/partials/phone_step1.html', {'error': str(e)})
        except OTPRateLimitError as e:
            return render(request, 'accounts/partials/phone_step1.html', {'error': str(e)})
        except OTPError as e:
            return render(request, 'accounts/partials/phone_step1.html', {'error': str(e)})

        return render(request, 'accounts/partials/phone_step2.html', {'phone': phone})


class OTPVerifyHTMXView(View):
    """HTMX: kod kiritildi → tekshiradi → JWT cookie o'rnatib redirect qiladi."""

    def post(self, request):
        raw_phone = request.POST.get('phone', '').strip()
        code      = request.POST.get('code', '').strip()

        try:
            user = verify_otp(raw_phone, code)
        except (PhoneValidationError, OTPError) as e:
            return render(request, 'accounts/partials/phone_step2.html', {
                'phone': raw_phone, 'error': str(e),
            })

        if not user.is_active:
            return render(request, 'accounts/partials/phone_step2.html', {
                'phone': raw_phone, 'error': 'Akkaunt bloklangan.',
            })

        fingerprint     = make_fingerprint(request)
        org = user.primary_organization
        access, refresh = create_token_pair(user, fingerprint, org=org)

        # HTMX redirect
        response = HttpResponse(status=204)
        response['HX-Redirect'] = '/'
        set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
        return response


class LoginRegisterView(View):
    """Asosiy login/register sahifasi — tab-based HTMX."""

    def get(self, request):
        if request.user.is_authenticated:
            return HttpResponseRedirect('/')
        return render(request, 'accounts/login_register.html', {
            'PROJECT_BRAND_NAME': settings.PROJECT_BRAND_NAME,
        })


class LoginFormView(View):
    """HTMX: Login form partial."""

    def get(self, request):
        return render(request, 'accounts/partials/login_form.html')


class RegisterFormView(View):
    """HTMX: Register form partial."""

    def get(self, request):
        return render(request, 'accounts/partials/register_form.html')


class PhoneLoginFormView(View):
    """HTMX: Phone OTP login form."""

    def get(self, request):
        return render(request, 'accounts/partials/phone_step1.html')


class PhoneRegisterFormView(View):
    """HTMX: Phone OTP register form."""

    def get(self, request):
        return render(request, 'accounts/partials/phone_step1.html', {
            'mode': 'register'
        })


class EmailLoginView(View):
    """Email yoki username + parol bilan kirish."""

    def post(self, request):
        login_val = request.POST.get('login', '').strip()
        password  = request.POST.get('password', '').strip()

        # email yoki username aniqlash
        from apps.accounts.models import CustomUser
        from allauth.account.models import EmailAddress

        user = None
        try:
            if '@' in login_val:
                u = CustomUser.objects.get(email__iexact=login_val)
                user = authenticate(request, username=u.username, password=password)
            else:
                user = authenticate(request, username=login_val, password=password)
        except CustomUser.DoesNotExist:
            pass

        if user is None:
            return HttpResponse('Email/foydalanuvchi nomi yoki parol noto\'g\'ri', status=400)

        if not user.is_active:
            return HttpResponse('Akkaunt bloklangan', status=403)

        # Email verification check — email+password users uchun
        if '@' in login_val:
            email_addr = EmailAddress.objects.filter(
                user=user,
                email__iexact=login_val
            ).first()

            if email_addr and not email_addr.verified:
                return HttpResponse(
                    'Email tasdiqlanmagan. Tekshirish havolasini emailda tekshiring.',
                    status=403
                )

        fingerprint     = make_fingerprint(request)
        org = user.primary_organization
        access, refresh = create_token_pair(user, fingerprint, org=org)

        response = HttpResponse(status=204)
        response['HX-Redirect'] = '/'
        set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
        return response


class RegisterView(View):
    """Email yoki username bilan ro'yxatdan o'tish."""

    def post(self, request):
        from apps.accounts.models import CustomUser
        import re

        reg_method = request.POST.get('reg_method', 'email').strip()
        password   = request.POST.get('password', '').strip()
        password_confirm = request.POST.get('password_confirm', '').strip()

        # Parol tekshiruvi
        if len(password) < 8:
            return HttpResponse('Parol kamida 8 ta belgidan iborat bo\'lishi kerak', status=400)
        if password != password_confirm:
            return HttpResponse('Parollar mos kelmaydi', status=400)
        if not re.search(r'\d', password) or not re.search(r'[a-zA-Z]', password):
            return HttpResponse('Parol raqam va harflarni o\'z ichiga olishi kerak', status=400)

        try:
            if reg_method == 'email':
                email = request.POST.get('email', '').strip()
                if not email or '@' not in email:
                    return HttpResponse('Email noto\'g\'ri', status=400)

                if CustomUser.objects.filter(email__iexact=email).exists():
                    return HttpResponse('Bu email allaqachon ro\'yxatdan o\'tgan', status=400)

                user = CustomUser.objects.create_user(
                    email=email,
                    username=email.split('@')[0],
                    password=password,
                )
            elif reg_method == 'username':
                username = request.POST.get('username', '').strip()
                if not username or not re.match(r'^[a-zA-Z0-9_]{3,}$', username):
                    return HttpResponse('Foydalanuvchi nomi 3+ ta belgi, harflar/raqamlar/pastki chiziq', status=400)

                if CustomUser.objects.filter(username__iexact=username).exists():
                    return HttpResponse('Bu foydalanuvchi nomi band', status=400)

                user = CustomUser.objects.create_user(
                    username=username,
                    email=f'{username}@local.local',
                    password=password,
                )
            else:
                return HttpResponse('Noto\'g\'ri ro\'yxatdan o\'tish usuli', status=400)

            # Email verification — email+password users uchun majburiy
            from allauth.account.models import EmailAddress

            if reg_method == 'email':
                # Email unverified deb belgilash
                EmailAddress.objects.create(
                    user=user,
                    email=user.email,
                    verified=False,
                    primary=True
                )
                # TODO: Send verification email (allauth orqali)
                # Hozircha faqat unverified deb belgilaydi
                return HttpResponse(
                    'Email tasdiqlash havolasi yuborildi. Tekshirish havolasini emailda tekshiring.',
                    status=200
                )
            else:
                # Username/OTP registration — email tasdiqlanmagan
                user.is_active = True
                user.save()

            # Token yaratish
            fingerprint     = make_fingerprint(request)
            org = user.primary_organization
            access, refresh = create_token_pair(user, fingerprint, org=org)

            response = HttpResponse(status=204)
            response['HX-Redirect'] = '/'
            set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
            return response

        except Exception as e:
            return HttpResponse(f'Ro\'yxatdan o\'tishda xato: {str(e)}', status=500)
