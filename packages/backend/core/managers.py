"""
TenantManager — barcha tenant-related modellarda default Manager sifatida ishlatiladi.

Avtomatik ravishda joriy org ga filter qo'shadi.
global_objects → filter bypas qiladi (admin, background worker uchun).

Defense-in-depth: agar tenant context o'rnatilmagan bo'lsa va explicit unscoped
bypass yo'q bo'lsa — bo'sh queryset qaytaradi (fail-closed).
"""

from functools import wraps

from django.db import models

from .tenant import get_current_org, is_unscoped_allowed, tenant_context, unscoped_context


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
        # unscoped_context() barcha boshqa filter'larni bypass qiladi (admin/worker).
        # Bu birinchi tekshiriladi: agar boshqa joyda set_current_org leak qilingan
        # bo'lsa ham, explicit unscoped_context ishlatilganda barcha qatorlar ko'rinadi.
        if is_unscoped_allowed():
            return qs
        org = get_current_org()
        if org is not None:
            return qs.filter(organization=org)
        return qs.none()

    def for_org(self, org):
        """Aniq org uchun queryset — tenant context dan mustaqil."""
        return OrgScopedQuerySet.for_model(self.model, org, using=self._db).filter(organization=org)


def _unscoped(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with unscoped_context():
            return method(self, *args, **kwargs)

    return run


class UnscopedQuerySet(models.QuerySet):
    """Evaluates inside unscoped_context(), so the database bypass (core/db_rls.py) matches
    the application's: `global_objects` is the declared cross-tenant manager (B15)."""

    _fetch_all = _unscoped(models.QuerySet._fetch_all)
    count = _unscoped(models.QuerySet.count)
    exists = _unscoped(models.QuerySet.exists)
    contains = _unscoped(models.QuerySet.contains)
    aggregate = _unscoped(models.QuerySet.aggregate)
    update = _unscoped(models.QuerySet.update)
    delete = _unscoped(models.QuerySet.delete)
    bulk_update = _unscoped(models.QuerySet.bulk_update)
    explain = _unscoped(models.QuerySet.explain)

    def iterator(self, chunk_size=None):
        with unscoped_context():
            yield from super().iterator(chunk_size=chunk_size)


def _in_org(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with _org_scope(self._rls_org):
            return method(self, *args, **kwargs)

    return run


def _org_scope(org):
    from contextlib import nullcontext

    return tenant_context(org) if org is not None else nullcontext()


class OrgScopedQuerySet(models.QuerySet):
    """`for_org(org)` is the explicit "this org, whatever the context" scope; its querysets
    evaluate under that org, so RLS shows that org's rows (B15)."""

    _rls_org = None

    @classmethod
    def for_model(cls, model, org, using=None):
        qs = cls(model=model, using=using)
        qs._rls_org = org
        return qs

    def _clone(self):
        c = super()._clone()
        c._rls_org = self._rls_org
        return c

    _fetch_all = _in_org(models.QuerySet._fetch_all)
    count = _in_org(models.QuerySet.count)
    exists = _in_org(models.QuerySet.exists)
    contains = _in_org(models.QuerySet.contains)
    aggregate = _in_org(models.QuerySet.aggregate)
    update = _in_org(models.QuerySet.update)
    delete = _in_org(models.QuerySet.delete)
    explain = _in_org(models.QuerySet.explain)

    def iterator(self, chunk_size=None):
        with _org_scope(self._rls_org):
            yield from super().iterator(chunk_size=chunk_size)


class GlobalManager(models.Manager.from_queryset(UnscopedQuerySet)):
    """
    Tenant filtrisiz to'liq queryset.
    Superadmin, background worker va cross-tenant operatsiyalar uchun.
    Querysets evaluate inside unscoped_context(), which also lifts RLS for them.

    Ishlatish:
        Question.global_objects.all()   # barcha tenantlarning savollari
    """
