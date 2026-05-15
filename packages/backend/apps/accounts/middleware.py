"""
Device check middleware.

Har bir so'rovda:
  1. Access token cookie dan user_id olinadi
  2. Refresh token cookie dan joriy fingerprint tekshiriladi
  3. Redis da bu user_id uchun faol fingerprint hozirgi bilan mos kelmasа —
     boshqa qurilmadan kirgan deb hisoblanadi → cookie tozalanadi (logout)

Bu "bir akkaunt = bir qurilma" qoidasini enforce qiladi.
JWTAuthMiddleware dan KEYIN, TenantMiddleware dan OLDIN joylashadi.
"""

import logging

from django.conf import settings

from apps.accounts.services.token_service import (
    clear_auth_cookies,
    make_fingerprint,
    verify_access_token,
    _redis,
)

log = logging.getLogger(__name__)


class DeviceCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._check_device(request)
        return self.get_response(request)

    def _check_device(self, request) -> None:
        """
        Refresh token mavjud bo'lsa — Redis da saqlangan fingerprint
        joriy so'rov fingerprinti bilan taqqoslanadi.

        Agar single_device_policy=True: mos kelmasa → force logout
        Agar single_device_policy=False: ko'p qurilmaga ruxsat, check skip
        """
        access_token  = request.COOKIES.get('access_token')
        refresh_token = request.COOKIES.get('refresh_token')

        if not access_token or not refresh_token:
            return

        user_id = verify_access_token(access_token)
        if user_id is None:
            # Muddati o'tgan access token — refresh endpoint hal qiladi
            return

        # Organization device policy tekshirish
        from apps.accounts.models import CustomUser
        from core.tenant import get_current_org

        try:
            user = CustomUser.objects.get(pk=user_id)
            org = get_current_org()

            if org and not org.single_device_policy:
                # Multi-device mode — fingerprint check skip
                return
        except (CustomUser.DoesNotExist, Exception):
            pass

        # Single-device policy — fingerprint tekshirish
        current_fp = make_fingerprint(request)
        r = _redis()
        stored_fp = r.get(f'session:active:{user_id}')

        if stored_fp is None:
            # Redis da sessiya yo'q — logout bo'lgan yoki tozalangan
            request._force_logout = True
            return

        if stored_fp != current_fp:
            # Boshqa qurilma kirib, bu sessiya o'chirilgan
            log.info('Device mismatch — force logout: user=%s', user_id)
            request._force_logout = True


class DeviceLogoutMiddleware:
    """
    DeviceCheckMiddleware force logout belgisini response ga apply qiladi.
    Response davrimizda ishlaydi (cookie o'chirish uchun response kerak).

    HTMX requestlar uchun HX-Redirect header qo'shadi (frontend redirect).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, '_force_logout', False):
            clear_auth_cookies(response)
            # request.user ni ham tozalaymiz
            from django.contrib.auth.models import AnonymousUser
            request.user = AnonymousUser()

            # HTMX requestlarda redirect signal yuborish
            if request.headers.get('HX-Request') == 'true':
                response['HX-Redirect'] = '/login/'

        return response
