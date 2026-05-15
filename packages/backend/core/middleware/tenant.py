import jwt
from django.conf import settings
from ..tenant import set_current_org, clear_current_org


class TenantMiddleware:
    """
    JWT token'dan current_org claim'ni oladi va thread-local storage'ga o'rnatadi.

    Tartib:
      1. JWT access_token'dan current_org claim'ni extract qilish
      2. Membership validation: user bu org'ning active member'i mi?
      3. Fallback: primary_organization (agar JWT'da org yo'q yoki validation fail bo'lsa)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org = self._resolve_org(request)
        set_current_org(org)
        try:
            response = self.get_response(request)
        finally:
            clear_current_org()
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

        if not request.user.memberships.filter(
            organization=org, status='active'
        ).exists():
            return request.user.primary_organization

        return org

    def _org_id_from_jwt(self, request):
        token = request.COOKIES.get('access_token')
        if not token:
            return None
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY,
                algorithms=['HS256'],
                options={'verify_exp': False},
            )
            return payload.get('current_org')
        except jwt.PyJWTError:
            return None
