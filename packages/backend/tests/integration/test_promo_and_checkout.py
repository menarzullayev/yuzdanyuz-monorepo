"""Monetisation: tariff checkout, webhook activation, promo codes (negative tests first)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.commerce import promo_service
from apps.commerce.models import (
    PaymentIntent,
    PromoCode,
    PromoRedemption,
    SubscriptionPlan,
    UserSubscription,
)
from tests.integration._monetisation import exam_type, join, make_user, student_plan

pytestmark = pytest.mark.integration


def _promo(code='SALE10', *, kind='percent', value='10', **kw):
    plans = kw.pop('plans', ())
    types = kw.pop('types', ())
    p = PromoCode.objects.create(code=code, discount_type=kind, value=Decimal(value), **kw)
    p.allowed_plans.set(plans)
    p.allowed_exam_types.set(types)
    return p


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _checkout(user, plan, promo=''):
    body = {'plan_slug': plan.slug, 'provider': 'payme'}
    if promo:
        body['promo_code'] = promo
    return _client(user).post(
        reverse('commerce:billing-checkout'), body, content_type='application/json'
    )


def _pay(intent, state='succeeded'):
    intent.refresh_from_db()
    return Client().post(
        reverse('commerce:payme-webhook'),
        data={'id': intent.provider_tx_id, 'state': state, 'amount': int(intent.amount_uzs * 100)},
        content_type='application/json',
        HTTP_X_SIGNATURE='stub-sig',
    )


@pytest.fixture
def plan(db):
    return student_plan('all-month', 'all_types', price='100000')


@pytest.fixture
def buyer(user, org, member):
    return user


class TestPlanValidation:
    def test_org_plan_cannot_have_access_mode(self, db):
        p = SubscriptionPlan(
            name='x',
            slug='x',
            billing_period='monthly',
            pricing_model='flat',
            price_uzs=1,
            access_mode='all_types',
        )
        with pytest.raises(ValidationError):
            p.clean()

    def test_student_plan_requires_mode(self, db):
        p = SubscriptionPlan(
            name='x',
            slug='x',
            audience='student',
            billing_period='monthly',
            pricing_model='flat',
            price_uzs=1,
        )
        with pytest.raises(ValidationError):
            p.clean()

    def test_single_type_requires_exam_type(self, db):
        p = SubscriptionPlan(
            name='x',
            slug='x',
            audience='student',
            access_mode='single_type',
            billing_period='monthly',
            pricing_model='flat',
            price_uzs=1,
        )
        with pytest.raises(ValidationError):
            p.clean()

    def test_db_constraint_blocks_student_plan_without_mode(self, db):
        with pytest.raises(IntegrityError), transaction.atomic():
            SubscriptionPlan.objects.create(
                name='x',
                slug='x',
                audience='student',
                billing_period='monthly',
                pricing_model='flat',
                price_uzs=1,
            )

    def test_org_endpoints_do_not_expose_student_plans(self, buyer, plan):
        r = _client(buyer).get(reverse('commerce:sub-plans'))
        assert plan.slug not in [p['slug'] for p in r.json()['plans']]


class TestPromoModel:
    def test_percent_over_100_rejected(self, db):
        p = PromoCode(code='X', discount_type='percent', value=Decimal('101'))
        with pytest.raises(ValidationError):
            p.clean()
        with pytest.raises(IntegrityError), transaction.atomic():
            PromoCode.objects.create(code='BIG', discount_type='percent', value=Decimal('101'))

    def test_zero_and_negative_rejected(self, db):
        for v in ('0', '-5'):
            with pytest.raises(IntegrityError), transaction.atomic():
                PromoCode.objects.create(code=f'N{v}', discount_type='fixed', value=Decimal(v))

    def test_code_unique_case_insensitively(self, db):
        _promo('Summer')
        with pytest.raises(IntegrityError), transaction.atomic():
            PromoCode.objects.create(code='SUMMER', discount_type='fixed', value=Decimal('1'))

    def test_lookup_is_case_insensitive(self, user, plan):
        _promo('Summer')
        assert promo_service.validate_promo(
            'sUmMeR', user, plan, plan.price_uzs
        ).discount == Decimal('10000.00')

    def test_expiry_must_follow_start(self, db):
        now = timezone.now()
        p = PromoCode(code='T', discount_type='fixed', value=1, starts_at=now, expires_at=now)
        with pytest.raises(ValidationError):
            p.clean()


class TestPromoValidation:
    def _err(self, code, user, plan):
        with pytest.raises(promo_service.PromoError) as e:
            promo_service.validate_promo(code, user, plan, plan.price_uzs)
        return e.value.code

    def test_unknown(self, user, plan):
        assert self._err('NOPE', user, plan) == 'promo_not_found'

    def test_inactive(self, user, plan):
        _promo(is_active=False)
        assert self._err('SALE10', user, plan) == 'promo_inactive'

    def test_expired(self, user, plan):
        _promo(expires_at=timezone.now() - timedelta(seconds=1))
        assert self._err('SALE10', user, plan) == 'promo_expired'

    def test_not_started(self, user, plan):
        _promo(starts_at=timezone.now() + timedelta(days=1))
        assert self._err('SALE10', user, plan) == 'promo_not_started'

    def test_exhausted(self, user, user2, plan):
        p = _promo(max_uses=1)
        intent = PaymentIntent.objects.create(
            user=user2, provider='payme', amount_uzs=1, coins_to_credit=0
        )
        PromoRedemption.objects.create(promo=p, user=user2, payment_intent=intent, discount_uzs=1)
        assert self._err('SALE10', user, plan) == 'promo_exhausted'

    def test_second_use_by_same_user(self, user, plan):
        p = _promo()
        intent = PaymentIntent.objects.create(
            user=user, provider='payme', amount_uzs=1, coins_to_credit=0
        )
        PromoRedemption.objects.create(promo=p, user=user, payment_intent=intent, discount_uzs=1)
        assert self._err('SALE10', user, plan) == 'promo_already_used'

    def test_one_use_per_user_enforced_by_db(self, user, db):
        p = _promo()
        for i in range(2):
            intent = PaymentIntent.objects.create(
                user=user, provider='payme', amount_uzs=1, coins_to_credit=0, provider_tx_id=f't{i}'
            )
            if i == 0:
                PromoRedemption.objects.create(
                    promo=p, user=user, payment_intent=intent, discount_uzs=1
                )
            else:
                with pytest.raises(IntegrityError), transaction.atomic():
                    PromoRedemption.objects.create(
                        promo=p, user=user, payment_intent=intent, discount_uzs=1
                    )

    def test_plan_not_allowed(self, user, plan):
        other = student_plan('other', 'all_types')
        _promo(plans=[other])
        assert self._err('SALE10', user, plan) == 'promo_plan_not_allowed'

    def test_exam_type_not_allowed(self, user, db):
        dtm, ielts = exam_type('dtm'), exam_type('ielts')
        pl = student_plan('p-ielts', 'single_type', et=ielts)
        _promo(types=[dtm])
        assert self._err('SALE10', user, pl) == 'promo_exam_type_not_allowed'

    def test_type_restricted_promo_rejects_plan_without_type(self, user, plan):
        _promo(types=[exam_type('dtm')])
        assert self._err('SALE10', user, plan) == 'promo_exam_type_not_allowed'

    def test_allowed_restrictions_accept(self, user, db):
        dtm = exam_type('dtm')
        pl = student_plan('p-dtm', 'single_type', et=dtm)
        _promo(plans=[pl], types=[dtm])
        q = promo_service.validate_promo('SALE10', user, pl, pl.price_uzs)
        assert q.final_amount == Decimal('90000.00')

    def test_fixed_discount_never_below_zero(self, user, plan):
        _promo(kind='fixed', value='999999999')
        q = promo_service.validate_promo('SALE10', user, plan, plan.price_uzs)
        assert q.final_amount == Decimal('0')

    def test_validate_endpoint_reports_error_code(self, buyer, plan):
        _promo(expires_at=timezone.now() - timedelta(days=1))
        r = _client(buyer).post(
            reverse('commerce:billing-promo-validate'),
            {'code': 'sale10', 'plan_slug': plan.slug},
            content_type='application/json',
        )
        assert r.status_code == 400 and r.json()['code'] == 'promo_expired'

    def test_validate_endpoint_preview(self, buyer, plan):
        _promo()
        r = _client(buyer).post(
            reverse('commerce:billing-promo-validate'),
            {'code': 'sale10', 'plan_slug': plan.slug},
            content_type='application/json',
        )
        assert r.status_code == 200
        assert r.json()['final_amount'] == '90000.00'


class TestCheckoutAndActivation:
    def test_checkout_grants_nothing_until_paid(self, buyer, plan):
        r = _checkout(buyer, plan)
        assert r.status_code == 201, r.content
        assert r.json()['amount_uzs'] == '100000.00'
        assert not UserSubscription.objects.filter(user=buyer).exists()

    def test_webhook_activates_subscription_once(self, buyer, plan):
        _checkout(buyer, plan)
        intent = PaymentIntent.objects.get(user=buyer)
        assert _pay(intent).status_code == 200
        sub = UserSubscription.objects.get(user=buyer)
        assert sub.plan == plan and sub.current_period_ends_at > timezone.now() + timedelta(days=29)
        # duplicate callback: no second subscription, no extension
        assert _pay(intent).status_code == 200
        assert UserSubscription.objects.filter(user=buyer).count() == 1

    def test_failed_payment_activates_nothing(self, buyer, plan):
        _checkout(buyer, plan)
        intent = PaymentIntent.objects.get(user=buyer)
        _pay(intent, state='failed')
        assert not UserSubscription.objects.filter(user=buyer).exists()

    def test_buying_again_extends(self, buyer, plan):
        for _ in range(2):
            _checkout(buyer, plan)
        for i in PaymentIntent.objects.filter(user=buyer):
            _pay(i)
        sub = UserSubscription.objects.get(user=buyer)
        assert sub.current_period_ends_at > timezone.now() + timedelta(days=59)

    def test_exam_pass_purchase(self, buyer, db):
        pl = student_plan('pass', 'exam_key', et=exam_type('dtm'), price='15000')
        _checkout(buyer, pl)
        _pay(PaymentIntent.objects.get(user=buyer))
        assert UserSubscription.objects.get(user=buyer).attempts_remaining == 1

    def test_inactive_plan_and_disabled_type_not_purchasable(self, buyer, db):
        et = exam_type('ielts')
        pl = student_plan('single', 'single_type', et=et)
        exam_type('ielts', enabled=False)
        assert _checkout(buyer, pl).status_code == 404
        exam_type('ielts')
        pl.is_active = False
        pl.save()
        assert _checkout(buyer, pl).status_code == 404

    def test_catalog_hides_disabled_type_and_its_plans(self, buyer, db):
        et = exam_type('ielts')
        student_plan('single', 'single_type', et=et)
        student_plan('all', 'all_types')
        c = _client(buyer)
        body = c.get(reverse('commerce:billing-catalog')).json()
        assert {t['code'] for t in body['exam_types']} >= {'ielts'}
        exam_type('ielts', enabled=False)
        body = c.get(reverse('commerce:billing-catalog')).json()
        assert 'ielts' not in {t['code'] for t in body['exam_types']}
        assert [p['slug'] for p in body['plans']] == ['all']

    def test_requires_authentication(self, plan):
        r = Client().post(reverse('commerce:billing-checkout'), {'plan_slug': plan.slug})
        assert r.status_code in (401, 403)

    def test_prod_like_settings_503(self, buyer, plan, settings):
        settings.PAYMENTS_STUB_ALLOWED = False
        settings.ENABLE_REAL_PAYMENTS = False
        settings.DEBUG = False
        assert _checkout(buyer, plan).status_code == 503


class TestPromoOnlyCountsWhenPaid:
    def test_checkout_does_not_count_use(self, buyer, plan):
        p = _promo(max_uses=1)
        r = _checkout(buyer, plan, 'sale10')
        assert r.status_code == 201 and r.json()['amount_uzs'] == '90000.00'
        assert PromoRedemption.objects.count() == 0
        assert promo_service.used_count(p) == 0

    def test_failed_payment_does_not_count_and_code_reusable(self, buyer, plan):
        _promo(max_uses=1)
        _checkout(buyer, plan, 'sale10')
        _pay(PaymentIntent.objects.get(user=buyer), state='failed')
        assert PromoRedemption.objects.count() == 0
        assert _checkout(buyer, plan, 'sale10').status_code == 201

    def test_paid_counts_then_blocks_reuse_and_exhausts(self, buyer, user2, org, plan):
        _promo(max_uses=1)
        _checkout(buyer, plan, 'sale10')
        _pay(PaymentIntent.objects.get(user=buyer))
        assert PromoRedemption.objects.get().discount_uzs == Decimal('10000.00')
        r = _checkout(buyer, plan, 'sale10')
        assert r.status_code == 400 and r.json()['code'] == 'promo_already_used'
        join(user2, org)
        r = _checkout(user2, plan, 'sale10')
        assert r.status_code == 400 and r.json()['code'] == 'promo_exhausted'

    def test_bad_promo_blocks_checkout(self, buyer, plan):
        _promo(expires_at=timezone.now() - timedelta(days=1))
        r = _checkout(buyer, plan, 'sale10')
        assert r.status_code == 400 and r.json()['code'] == 'promo_expired'
        assert not PaymentIntent.objects.filter(user=buyer).exists()

    def test_fully_discounted_order_activates_without_provider(self, buyer, plan):
        _promo('FREE', value='100')
        r = _checkout(buyer, plan, 'free')
        assert r.status_code == 201
        assert r.json()['checkout_url'] is None and r.json()['status'] == 'succeeded'
        assert UserSubscription.objects.filter(user=buyer).count() == 1
        assert PromoRedemption.objects.count() == 1
