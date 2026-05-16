"""
Phone OTP auth views.

POST /api/auth/otp/send/    — telefon raqamga SMS yuboradi
POST /api/auth/otp/verify/  — kodni tekshiradi, JWT cookie qaytaradi
"""

import json

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.services.otp_service import (
    OTPError,
    OTPRateLimitError,
    send_otp,
    verify_otp,
)
from apps.accounts.services.phone_utils import PhoneValidationError
from apps.accounts.services.token_service import (
    create_token_pair,
    make_fingerprint,
    set_auth_cookies,
)


def _is_secure(request) -> bool:
    return request.is_secure() or not settings.DEBUG


@method_decorator(csrf_exempt, name='dispatch')
class OTPSendView(View):
    """
    POST /api/auth/otp/send/
    Body: {"phone": "+998901234567"}
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        raw_phone = body.get('phone', '').strip()
        if not raw_phone:
            return JsonResponse({'error': 'phone required'}, status=400)

        # ISSUE-106: IP-tier rate limit uchun client IP
        client_ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[
            0
        ].strip() or request.META.get('REMOTE_ADDR')
        try:
            normalized = send_otp(raw_phone, ip=client_ip)
        except PhoneValidationError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except OTPRateLimitError as e:
            return JsonResponse({'error': str(e)}, status=429)
        except OTPError as e:
            return JsonResponse({'error': str(e)}, status=503)

        return JsonResponse(
            {
                'phone': normalized,
                'message': 'SMS yuborildi',
            }
        )


@method_decorator(csrf_exempt, name='dispatch')
class OTPVerifyView(View):
    """
    POST /api/auth/otp/verify/
    Body: {"phone": "+998901234567", "code": "483920"}

    Response: 200 + httpOnly JWT cookie
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        raw_phone = body.get('phone', '').strip()
        code = body.get('code', '').strip()

        if not raw_phone or not code:
            return JsonResponse({'error': 'phone va code majburiy'}, status=400)

        try:
            user = verify_otp(raw_phone, code)
        except PhoneValidationError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except OTPError as e:
            return JsonResponse({'error': str(e)}, status=401)

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
                    'is_new': not user.date_joined < user.date_joined,
                }
            }
        )
        set_auth_cookies(response, access, refresh, is_secure=_is_secure(request))
        return response
