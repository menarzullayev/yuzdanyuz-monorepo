import jwt
from django.conf import settings
from django.http import HttpResponseForbidden

from ..membership_cache import is_active_member
from ..tenant import (
    clear_current_org,
    set_current_org,
    set_unscoped_allowed,
    unscoped_context,
)


class TenantMiddleware:
    """
    JWT token'dan current_org claim'ni oladi va thread-local storage'ga o'rnatadi.

    Tartib:
      1. JWT access_token'dan current_org claim'ni extract qilish (verify_exp ON)
      2. Membership validation: user bu org'ning active member'i mi?
      3. Fallback: primary_organization (agar JWT'da org yo'q yoki validation fail bo'lsa)

    Side effects:
      - request.org = Organization | None (RLSMiddleware va views uchun)
      - thread-local set_current_org() (TenantManager uchun)
      - Platform admin/staff org'siz holatda → unscoped_allowed (admin uchun)
      - Regular user (non-admin, non-staff) org'siz → 403 Forbidden
        (auth/static endpoint'larda exempt — login/logout/signup org'siz ham ishlashi kerak)
    """

    # Tenant context talab qilmaydigan URL prefiksilari.
    # Auth flow (login/logout/register/OAuth) va static/media — barchasi exempt.
    # /accounts/ — django-allauth, /api/auth/ — apps.accounts auth endpoints.
    EXEMPT_URL_PREFIXES = (
        '/accounts/',
        '/api/auth/',
        '/static/',
        '/media/',
        '/__debug__/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # _resolve_org() Membership tenant model'ga query qiladi.
        # Bu chaqiruv tenant context o'rnatilishidan OLDIN sodir bo'ladi,
        # shuning uchun unscoped_context bilan o'rab qo'yamiz (chicken-and-egg).
        with unscoped_context():
            org = self._resolve_org(request)

        # Authenticated user'da org bo'lmasa va u platform admin/staff emas — 403.
        # Auth/static URL'larda exempt: login/logout uchun membership shart emas.
        # Anonymous request'lar ham o'tib ketadi.
        is_exempt_path = request.path.startswith(self.EXEMPT_URL_PREFIXES)
        if (
            request.user.is_authenticated
            and not (request.user.is_superuser or request.user.is_staff)
            and org is None
            and not is_exempt_path
        ):
            return HttpResponseForbidden(
                'No active organization membership. Contact administrator.'
            )

        # Platform admin/staff org'siz request'da unscoped queryset'ga ruxsat
        # (Django admin va cross-tenant operatsiyalar uchun).
        is_platform_unscoped = (
            request.user.is_authenticated
            and (request.user.is_superuser or request.user.is_staff)
            and org is None
        )

        set_current_org(org)
        request.org = org
        if is_platform_unscoped:
            set_unscoped_allowed(True)
        try:
            response = self.get_response(request)
        finally:
            clear_current_org()
            if is_platform_unscoped:
                set_unscoped_allowed(False)
        return response

    def _resolve_org(self, request):
        if not (hasattr(request, 'user') and request.user.is_authenticated):
            return None

        org_id = self._org_id_from_jwt(request)

        # JWT'da org yo'q → primary_organization (B2C, yangi user)
        if not org_id:
            return request.user.primary_organization

        # DB dan olish + membership validate
        from apps.organizations.models import Organization

        try:
            org = Organization.objects.get(pk=org_id)
        except (Organization.DoesNotExist, ValueError):
            return request.user.primary_organization

        # User bu org'ning active member'i mi?
        if request.user.is_superuser:
            return org

        if not is_active_member(request.user, org):
            return request.user.primary_organization

        return org

    def _org_id_from_jwt(self, request):
        token = request.COOKIES.get('access_token')
        if not token:
            return None
        try:
            # verify_exp=True (default) — eskirgan token'dan org o'qib bo'lmaydi.
            # Eskirgan/buzuq token → PyJWTError → None → primary_organization fallback.
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=['HS256'],
            )
            return payload.get('current_org')
        except jwt.PyJWTError:
            return None
