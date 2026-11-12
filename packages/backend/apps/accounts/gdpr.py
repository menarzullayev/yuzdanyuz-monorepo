"""GDPR endpoints (C22): right to erasure (art. 17) and data export (art. 15/20).

    GET  /api/auth/gdpr/erasure/          current request (or null)
    POST /api/auth/gdpr/erasure/          {password?} start: password -> scheduled,
                                          otherwise a Telegram code is sent
    POST /api/auth/gdpr/erasure/confirm/  {code} Telegram code -> scheduled
    POST /api/auth/gdpr/erasure/cancel/   cancel during the cooling-off
    GET  /api/auth/gdpr/export/           JSON file with the user's own data

The erasure itself runs later (accounts.process_due_erasures); see services/erasure.py.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection
from django.db.models.fields.files import FieldFile
from django.http import HttpResponse
from django.utils import timezone

from apps.accounts.models import ErasureRequest
from apps.accounts.services import erasure
from apps.accounts.views.base import AuthAPIView, api_error
from core.tenant import unscoped_context

SENSITIVE = ('hash', 'secret', 'token', 'password', '_enc')


def _request_json(req: ErasureRequest | None) -> dict | None:
    if req is None:
        return None
    return {
        'request_id': str(req.pk),
        'status': req.status,
        'confirm_via': req.confirm_via,
        'requested_at': req.requested_at,
        'scheduled_for': req.scheduled_for,
        'cooling_off_hours': int(erasure.cooling_off().total_seconds() // 3600),
    }


def _error(exc: erasure.ErasureError):
    return api_error(exc.code, exc.message, 400)


class GDPRErasureView(AuthAPIView):
    def get(self, request):
        return self._ok(erasure.open_request(request.user))

    def post(self, request):
        try:
            req = erasure.start(request.user, (request.data.get('password') or '') or None)
        except erasure.ErasureError as exc:
            return _error(exc)
        return self._ok(req, status=202)

    @staticmethod
    def _ok(req, status=200):
        from rest_framework.response import Response

        return Response(
            json.loads(json.dumps(_request_json(req), cls=DjangoJSONEncoder)), status=status
        )


class GDPRErasureConfirmView(GDPRErasureView):
    def get(self, request):
        return api_error('method_not_allowed', 'Use POST.', 405)

    def post(self, request):
        try:
            req = erasure.confirm(request.user, str(request.data.get('code') or ''))
        except erasure.ErasureError as exc:
            return _error(exc)
        return self._ok(req)


class GDPRErasureCancelView(GDPRErasureView):
    def get(self, request):
        return api_error('method_not_allowed', 'Use POST.', 405)

    def post(self, request):
        try:
            req = erasure.cancel(request.user)
        except erasure.ErasureError as exc:
            return _error(exc)
        return self._ok(req)


# ── Export ──────────────────────────────────────────────────


@contextmanager
def _all_orgs_for_own_rows(request):
    """The export covers every organisation the user belongs to, so lift the org filter
    (app layer and RLS) for the duration; every query below is filtered by the user."""
    org = getattr(request, 'org', None)
    with unscoped_context():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cur:
                cur.execute('RESET app.current_org_id')
        try:
            yield
        finally:
            if connection.vendor == 'postgresql' and org is not None:
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('app.current_org_id', %s, false)", [str(org.pk)])


def _rows(qs) -> list[dict]:
    out = []
    for obj in qs:
        row = {}
        for f in obj._meta.concrete_fields:
            if any(s in f.name for s in SENSITIVE):
                continue
            value = f.value_from_object(obj)
            if isinstance(value, FieldFile):  # the stored name, not the file object
                value = value.name or None
            row[f.attname] = value
        out.append(row)
    return out


def _manager(model):
    return getattr(model, 'global_objects', model._default_manager)


def collect(user) -> dict:
    from apps.accounts.models import (
        DeviceSession,
        ExamCertificate,
        OnboardingProfile,
        OrgResultsConsent,
    )
    from apps.commerce.models import PaymentIntent, Wallet, WalletTransaction
    from apps.exams.models import ExamAttempt
    from apps.organizations.models import Membership

    profile = _rows([user])[0]
    profile.pop('password', None)
    return {
        'generated_at': timezone.now(),
        'profile': profile,
        'onboarding': _rows(OnboardingProfile.objects.filter(user=user)),
        'memberships': _rows(Membership.objects.filter(user=user)),
        'org_result_consents': _rows(OrgResultsConsent.objects.filter(user=user)),
        'certificates': _rows(ExamCertificate.objects.filter(user=user)),
        'sessions': _rows(DeviceSession.objects.filter(user=user)),
        'exam_attempts': _rows(_manager(ExamAttempt).filter(user=user)),
        'wallet': _rows(Wallet.objects.filter(user=user)),
        'wallet_transactions': _rows(_manager(WalletTransaction).filter(wallet__user=user)),
        'payments': _rows(_manager(PaymentIntent).filter(user=user)),
        'erasure_requests': [_request_json(r) for r in ErasureRequest.objects.filter(user=user)],
    }


class GDPRExportView(AuthAPIView):
    def get(self, request):
        with _all_orgs_for_own_rows(request):
            data = collect(request.user)
        body = json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2)
        resp = HttpResponse(body, content_type='application/json; charset=utf-8')
        stamp = timezone.now().strftime('%Y%m%d')
        resp['Content-Disposition'] = f'attachment; filename="yuzdanyuz-data-{stamp}.json"'
        resp['Cache-Control'] = 'no-store'
        return resp
