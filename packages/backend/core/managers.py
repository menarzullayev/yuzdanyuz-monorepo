"""
TenantManager — barcha tenant-related modellarda default Manager sifatida ishlatiladi.

Avtomatik ravishda joriy org ga filter qo'shadi.
global_objects → filter bypas qiladi (admin, background worker uchun).
"""

from django.db import models
from .tenant import get_current_org


class TenantManager(models.Manager):
    """
    Joriy tenant mavjud bo'lsa — organization ga filter qo'shadi.
    Mavjud bo'lmasa (admin, worker, test) — barcha qatorlar qaytariladi.
    """

    def get_queryset(self):
        qs  = super().get_queryset()
        org = get_current_org()
        if org is not None:
            qs = qs.filter(organization=org)
        return qs

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
