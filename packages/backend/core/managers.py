"""
TenantManager — barcha tenant-related modellarda default Manager sifatida ishlatiladi.

Avtomatik ravishda joriy org ga filter qo'shadi.
global_objects → filter bypas qiladi (admin, background worker uchun).

Defense-in-depth: agar tenant context o'rnatilmagan bo'lsa va explicit unscoped
bypass yo'q bo'lsa — bo'sh queryset qaytaradi (fail-closed).
"""

from django.db import models

from .tenant import get_current_org, is_unscoped_allowed


class TenantManager(models.Manager):
    """
    Joriy tenant context'ga qarab queryset qaytaradi:
      - org mavjud → organization=org filter
      - org=None VA unscoped_allowed → barcha qator (admin/worker)
      - org=None VA NOT unscoped_allowed → bo'sh queryset (fail-closed)

    Fail-closed himoya: noma'lum context'da tenant ma'lumotlari ko'rinmaydi.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        org = get_current_org()
        if org is not None:
            return qs.filter(organization=org)
        if is_unscoped_allowed():
            return qs
        return qs.none()

    def for_org(self, org):
        """Aniq org uchun queryset — tenant context dan mustaqil."""
        return super().get_queryset().filter(organization=org)


class GlobalManager(models.Manager):
    """
    Tenant filtrisiz to'liq queryset.
    Superadmin, background worker va cross-tenant operatsiyalar uchun.

    Ishlatish:
        Question.global_objects.all()   # barcha tenantlarning savollari
    """

    def get_queryset(self):
        return super().get_queryset()
