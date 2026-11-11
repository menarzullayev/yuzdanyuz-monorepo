"""
Monetisation REST API (B2C tariffs, promo codes, B2B exam keys).

  GET  billing/catalog/                 enabled exam types + student plans + free attempts
  GET  billing/me/                      my subscriptions, passes, free/weekly usage
  GET  billing/access/?exam_type=       can I start this exam type now (preview, consumes nothing)
  POST billing/promo/validate/          {code, plan_slug} -> discount preview
  POST billing/checkout/                {plan_slug, provider, promo_code?}

  GET  org/keys/summary/                issued / distributed / consumed / remaining per type
  GET  org/keys/                        list keys (?exam_type=&status=&group=)
  POST org/keys/purchase/               {exam_type, quantity, provider, promo_code?}
  POST org/keys/distribute/             {exam_type, user_ids[], per_user?, group_label?}
  POST org/keys/revoke/                 {key_ids[]}
  GET  exam-keys/mine/                  keys assigned to me
  POST exam-keys/redeem/                {code}
  POST billing/admin/keys/grant/        platform staff: {organization, exam_type, quantity, note?}
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access_service, billing_service, key_service, promo_service
from .models import (
    ExamKey,
    ExamKeyBatch,
    ExamType,
    SubscriptionPlan,
    UserSubscription,
)

PERM_VIEW = 'exam_keys:view'
PERM_MANAGE = 'exam_keys:manage'


class AccessDenied(APIException):
    """402 raised by the exam-start hook; the client shows the paywall."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_detail = 'Payment required'
    default_code = 'payment_required'

    def __init__(self, decision: access_service.AccessDecision):
        # core.exceptions wraps this as {error: {code, message, details}}; code = reason
        super().__init__(detail=decision.message)
        self.default_code = decision.reason
        self.extra = decision.details


def _plan_dict(p: SubscriptionPlan) -> dict:
    return {
        'id': str(p.id),
        'slug': p.slug,
        'name': p.name,
        'description': p.description,
        'access_mode': p.access_mode,
        'exam_type': p.exam_type.code if p.exam_type_id else None,
        'unlimited_exam_types': [t.code for t in p.unlimited_exam_types.all()],
        'weekly_limit': p.weekly_limit if p.access_mode == 'student' else None,
        'attempts_included': p.attempts_included if p.access_mode == 'exam_key' else None,
        'billing_period': p.billing_period,
        'price_uzs': str(p.price_uzs),
    }


def _error(code, message, http=400, **extra):
    return Response({'detail': message, 'code': code, **extra}, status=http)


class CatalogView(APIView):
    def get(self, request):
        types = ExamType.objects.filter(is_enabled=True)
        plans = (
            SubscriptionPlan.objects.filter(is_active=True, audience='student')
            .exclude(exam_type__is_enabled=False)
            .select_related('exam_type')
            .prefetch_related('unlimited_exam_types')
        )
        return Response(
            {
                'exam_types': [{'code': t.code, 'name': t.name} for t in types],
                'plans': [_plan_dict(p) for p in plans],
                'free_attempts_total': access_service.free_attempts_total(),
                'free_attempts_left': access_service.free_attempts_left(request.user),
            }
        )


class MyBillingView(APIView):
    def get(self, request):
        now = timezone.now()
        subs = (
            UserSubscription.objects.filter(user=request.user, status='active')
            .select_related('plan')
            .order_by('-created_at')
        )
        start, end = access_service.week_window(now)
        return Response(
            {
                'subscriptions': [
                    {
                        'id': str(s.id),
                        'plan_slug': s.plan.slug,
                        'access_mode': s.plan.access_mode,
                        'current_period_ends_at': s.current_period_ends_at,
                        'attempts_remaining': s.attempts_remaining,
                        'is_current': s.current_period_ends_at is None
                        or s.current_period_ends_at > now,
                    }
                    for s in subs
                ],
                'free_attempts_total': access_service.free_attempts_total(),
                'free_attempts_left': access_service.free_attempts_left(request.user),
                'weekly_used': access_service._weekly_used(request.user, now),
                'week_starts_at': start.isoformat(),
                'week_resets_at': end.isoformat(),
            }
        )


class AccessCheckView(APIView):
    def get(self, request):
        code = request.query_params.get('exam_type', '')
        decision = access_service.check_access(request.user, code)
        return Response(decision.as_dict())


class PromoValidateView(APIView):
    def post(self, request):
        plan = SubscriptionPlan.objects.filter(
            slug=request.data.get('plan_slug'), audience='student', is_active=True
        ).first()
        if plan is None:
            return _error('plan_not_available', 'Reja topilmadi.', 404)
        try:
            quote = promo_service.validate_promo(
                request.data.get('code', ''), request.user, plan, plan.price_uzs
            )
        except promo_service.PromoError as e:
            return _error(e.code, e.message)
        return Response(
            {
                'code': quote.promo.code,
                'base_amount': str(quote.base_amount),
                'discount_uzs': str(quote.discount),
                'final_amount': str(quote.final_amount),
            }
        )


def _checkout_response(intent, url, extra=None):
    body = {
        'intent_id': str(intent.id),
        'amount_uzs': str(intent.amount_uzs),
        'status': intent.status,
        # URL is None when the order was free (fully discounted) and is already active.
        'checkout_url': url,
        **(extra or {}),
    }
    return Response(body, status=status.HTTP_201_CREATED)


def _run_checkout(fn):
    try:
        return fn()
    except billing_service.CheckoutError as e:
        return _error(e.code, e.message, e.status)
    except billing_service.PaymentsUnavailable:
        return Response(
            {'detail': 'Payments are not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE
        )


class CheckoutView(APIView):
    def post(self, request):
        plan = (
            SubscriptionPlan.objects.filter(slug=request.data.get('plan_slug'), audience='student')
            .select_related('exam_type')
            .first()
        )
        if plan is None:
            return _error('plan_not_available', 'Reja topilmadi.', 404)

        def go():
            intent, url = billing_service.checkout_user_plan(
                request.user,
                plan,
                request.data.get('provider', 'payme'),
                request.data.get('promo_code', ''),
            )
            return _checkout_response(intent, url, {'plan': plan.slug})

        return _run_checkout(go)


# ─── B2B keys ────────────────────────────────────────────────────────────────


def _org_or_error(request, perm):
    org = getattr(request, 'org', None)
    if org is None:
        raise ValidationError({'detail': "Org context yo'q"})
    if not request.user.has_org_permission(org, perm):
        raise PermissionDenied(f'Ruxsat kerak: {perm}')
    return org


def _exam_type_or_404(code):
    et = ExamType.objects.filter(code=code).first()
    if et is None:
        raise ValidationError({'exam_type': "Noma'lum imtihon turi."})
    return et


def _key_dict(k: ExamKey) -> dict:
    return {
        'id': str(k.id),
        'code': k.code,
        'exam_type': k.exam_type.code,
        'status': k.status,
        'group_label': k.group_label,
        'assigned_to': str(k.assigned_to_id) if k.assigned_to_id else None,
        'assigned_at': k.assigned_at,
        'consumed_at': k.consumed_at,
    }


class OrgKeySummaryView(APIView):
    def get(self, request):
        org = _org_or_error(request, PERM_VIEW)
        return Response({'summary': key_service.summary(org)})


class OrgKeyListView(APIView):
    def get(self, request):
        org = _org_or_error(request, PERM_VIEW)
        qs = ExamKey.objects.filter(organization=org).select_related('exam_type')
        if v := request.query_params.get('exam_type'):
            qs = qs.filter(exam_type__code=v)
        if v := request.query_params.get('status'):
            qs = qs.filter(status=v)
        if v := request.query_params.get('group'):
            qs = qs.filter(group_label=v)
        keys = list(qs[:500])
        return Response({'count': len(keys), 'keys': [_key_dict(k) for k in keys]})


class OrgKeyPurchaseView(APIView):
    def post(self, request):
        org = _org_or_error(request, PERM_MANAGE)
        et = _exam_type_or_404(request.data.get('exam_type'))
        try:
            quantity = int(request.data.get('quantity', 0))
        except (TypeError, ValueError):
            raise ValidationError({'quantity': 'invalid'}) from None

        def go():
            intent, url = billing_service.checkout_org_keys(
                request.user,
                org,
                et,
                quantity,
                request.data.get('provider', 'payme'),
                request.data.get('promo_code', ''),
            )
            return _checkout_response(intent, url, {'exam_type': et.code, 'quantity': quantity})

        return _run_checkout(go)


def _key_error(e: key_service.KeyError_):
    return _error(e.code, e.message, e.status, **e.extra)


class OrgKeyDistributeView(APIView):
    def post(self, request):
        org = _org_or_error(request, PERM_MANAGE)
        et = _exam_type_or_404(request.data.get('exam_type'))
        try:
            per_user = int(request.data.get('per_user', 1))
        except (TypeError, ValueError):
            raise ValidationError({'per_user': 'invalid'}) from None
        user_ids = request.data.get('user_ids') or []
        try:
            keys = key_service.distribute_keys(
                org, et, user_ids, per_user, str(request.data.get('group_label', ''))
            )
        except key_service.KeyError_ as e:
            return _key_error(e)
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({'user_ids': 'invalid'}) from None
        return Response({'assigned': len(keys)}, status=status.HTTP_201_CREATED)


class OrgKeyRevokeView(APIView):
    def post(self, request):
        org = _org_or_error(request, PERM_MANAGE)
        ids = request.data.get('key_ids') or []
        try:
            n = key_service.revoke_keys(org, ids)
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({'key_ids': 'invalid'}) from None
        return Response({'revoked': n})


class MyKeysView(APIView):
    def get(self, request):
        keys = ExamKey.global_objects.filter(
            assigned_to=request.user, status=ExamKey.Status.ASSIGNED
        ).select_related('exam_type')
        return Response({'keys': [_key_dict(k) for k in keys]})


class RedeemKeyView(APIView):
    def post(self, request):
        org = getattr(request, 'org', None)
        try:
            key = key_service.redeem_key(request.user, org, request.data.get('code', ''))
        except key_service.KeyError_ as e:
            return _key_error(e)
        return Response(_key_dict(key))


class AdminGrantKeysView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        from apps.organizations.models import Organization

        org = Organization.objects.filter(pk=request.data.get('organization')).first()
        if org is None:
            raise ValidationError({'organization': 'not found'})
        et = _exam_type_or_404(request.data.get('exam_type'))
        try:
            batch = key_service.grant_keys(
                org,
                et,
                int(request.data.get('quantity', 0)),
                source=ExamKeyBatch.Source.PLATFORM_GRANT,
                granted_by=request.user,
                note=str(request.data.get('note', '')),
            )
        except key_service.KeyError_ as e:
            return _key_error(e)
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({'quantity': 'invalid'}) from None
        return Response(
            {'batch_id': str(batch.id), 'quantity': batch.quantity}, status=status.HTTP_201_CREATED
        )
