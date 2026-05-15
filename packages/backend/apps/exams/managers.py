"""
Exam-specific manager: PublicOrTenantManager.

MockExam joriy tenant'ning private mock'lari va `is_public=True` bo'lgan
platform mock'larini bir joyda qaytaradi. Tenant context'siz va explicit
unscoped marker yo'qda — fail-closed (.none()).

QuestionBank pattern bilan parallel (apps.catalog.models.QuestionBank).
"""

from django.db import models
from django.db.models import Q

from core.tenant import get_current_org, is_unscoped_allowed


class PublicOrTenantManager(models.Manager):
    """
    Joriy tenant context'ga qarab queryset:
      - org mavjud → Q(is_public=True) | Q(organization=org)
      - org=None va unscoped_allowed → barcha qator (admin/worker)
      - org=None va NOT unscoped_allowed → bo'sh queryset (fail-closed)

    Defense-in-depth: noma'lum context'da private mock'lar ko'rinmaydi,
    lekin platform-level public mock'lar ham ko'rinmaydi — chunki
    "qaysi user uchun ekanligi" aniqlanmagan. Fail-closed eng xavfsiz default.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        # unscoped_context() barcha boshqa filter'larni bypass qiladi.
        if is_unscoped_allowed():
            return qs
        org = get_current_org()
        if org is not None:
            return qs.filter(Q(is_public=True) | Q(organization=org))
        return qs.none()

    def for_org(self, org):
        """Aniq org uchun queryset — context'siz."""
        return super().get_queryset().filter(Q(is_public=True) | Q(organization=org))

    def public_only(self):
        """Faqat platform mocks (cross-tenant)."""
        return super().get_queryset().filter(is_public=True)
