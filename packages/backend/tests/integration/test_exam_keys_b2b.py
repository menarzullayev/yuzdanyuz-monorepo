"""Monetisation: B2B exam keys — purchase, distribution, usage counters, isolation."""

import json

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse

from apps.commerce import key_service
from apps.commerce.models import ExamKey, ExamKeyBatch, PaymentIntent
from core.tenant import tenant_context
from tests.integration._monetisation import exam_type, join, make_user, student_plan

pytestmark = pytest.mark.integration


def _c(user):
    c = Client()
    c.force_login(user)
    return c


def _post(user, name, body):
    return _c(user).post(
        reverse(f'commerce:{name}'), json.dumps(body), content_type='application/json'
    )


@pytest.fixture
def dtm(db):
    return exam_type('dtm')


@pytest.fixture
def owner1(org):
    u = make_user('owner1')
    join(u, org, 'owner')
    return u


@pytest.fixture
def owner2(org2):
    u = make_user('owner2')
    join(u, org2, 'owner')
    return u


@pytest.fixture
def stu1(org):
    u = make_user('stu1')
    join(u, org)
    return u


@pytest.fixture
def stu2(org):
    u = make_user('stu2')
    join(u, org)
    return u


@pytest.fixture
def stu_b(org2):
    u = make_user('stub')
    join(u, org2)
    return u


def _grant(org, et, n):
    return key_service.grant_keys(org, et, n, source=ExamKeyBatch.Source.PLATFORM_GRANT)


class TestPlatformGrant:
    def test_staff_grants_keys(self, superuser, org, dtm):
        r = _post(
            superuser,
            'billing-admin-grant',
            {'organization': str(org.id), 'exam_type': 'dtm', 'quantity': 5, 'note': 'pilot'},
        )
        assert r.status_code == 201
        with tenant_context(org):
            assert ExamKey.objects.filter(status='available').count() == 5
        assert ExamKeyBatch.global_objects.get().source == 'platform_grant'

    def test_non_staff_cannot_grant(self, owner1, org, dtm):
        r = _post(
            owner1,
            'billing-admin-grant',
            {'organization': str(org.id), 'exam_type': 'dtm', 'quantity': 5},
        )
        assert r.status_code == 403
        assert ExamKey.global_objects.count() == 0

    def test_quantity_bounds(self, superuser, org, dtm):
        for q in (0, -1, 100000):
            r = _post(
                superuser,
                'billing-admin-grant',
                {'organization': str(org.id), 'exam_type': 'dtm', 'quantity': q},
            )
            assert r.status_code == 400
        assert ExamKey.global_objects.count() == 0


class TestPurchase:
    def test_org_purchase_creates_keys_only_when_paid(self, owner1, org, dtm):
        student_plan('key-dtm', 'exam_key', et=dtm, price='20000')
        r = _post(
            owner1, 'orgkeys-purchase', {'exam_type': 'dtm', 'quantity': 10, 'provider': 'payme'}
        )
        assert r.status_code == 201, r.content
        assert r.json()['amount_uzs'] == '200000.00'
        assert ExamKey.global_objects.count() == 0
        intent = PaymentIntent.objects.get(user=owner1)
        assert intent.target_organization_id == org.id
        tiyin = int(intent.amount_uzs * 100)
        Client().post(
            reverse('commerce:payme-webhook'),
            data={'id': intent.provider_tx_id, 'state': 'succeeded', 'amount': tiyin},
            content_type='application/json',
            HTTP_X_SIGNATURE='stub-sig',
        )
        assert ExamKey.global_objects.filter(organization=org).count() == 10
        assert ExamKeyBatch.global_objects.get().source == 'purchase'
        # duplicate callback does not mint again
        Client().post(
            reverse('commerce:payme-webhook'),
            data={'id': intent.provider_tx_id, 'state': 'succeeded', 'amount': tiyin},
            content_type='application/json',
            HTTP_X_SIGNATURE='stub-sig',
        )
        assert ExamKey.global_objects.count() == 10

    def test_student_cannot_buy_keys(self, stu1, dtm):
        student_plan('key-dtm', 'exam_key', et=dtm)
        r = _post(stu1, 'orgkeys-purchase', {'exam_type': 'dtm', 'quantity': 1})
        assert r.status_code == 403

    def test_disabled_type_not_purchasable(self, owner1, dtm):
        student_plan('key-dtm', 'exam_key', et=dtm)
        exam_type('dtm', enabled=False)
        r = _post(owner1, 'orgkeys-purchase', {'exam_type': 'dtm', 'quantity': 1})
        assert r.status_code == 404 and r.json()['code'] == 'exam_type_disabled'

    def test_no_price_configured(self, owner1, dtm):
        r = _post(owner1, 'orgkeys-purchase', {'exam_type': 'dtm', 'quantity': 1})
        assert r.status_code == 404 and r.json()['code'] == 'plan_not_available'


class TestDistributionAndCounters:
    def test_counters_through_lifecycle(self, owner1, org, stu1, stu2, dtm):
        from apps.commerce import access_service

        _grant(org, dtm, 10)
        s = _c(owner1).get(reverse('commerce:orgkeys-summary')).json()['summary'][0]
        assert (s['issued'], s['distributed'], s['consumed'], s['remaining']) == (10, 0, 0, 10)

        r = _post(
            owner1,
            'orgkeys-distribute',
            {
                'exam_type': 'dtm',
                'user_ids': [str(stu1.id), str(stu2.id)],
                'per_user': 2,
                'group_label': '9-A',
            },
        )
        assert r.status_code == 201 and r.json()['assigned'] == 4
        s = _c(owner1).get(reverse('commerce:orgkeys-summary')).json()['summary'][0]
        assert (s['issued'], s['distributed'], s['consumed'], s['remaining']) == (10, 4, 0, 10)

        assert access_service.consume_access(stu1, 'dtm').reason == 'org_key'
        s = _c(owner1).get(reverse('commerce:orgkeys-summary')).json()['summary'][0]
        assert (s['issued'], s['distributed'], s['consumed'], s['remaining'], s['unassigned']) == (
            10,
            4,
            1,
            9,
            6,
        )

        lst = _c(owner1).get(reverse('commerce:orgkeys-list') + '?group=9-A&status=assigned').json()
        assert lst['count'] == 3

    def test_insufficient_keys_is_all_or_nothing(self, owner1, org, stu1, stu2, dtm):
        _grant(org, dtm, 3)
        r = _post(
            owner1,
            'orgkeys-distribute',
            {'exam_type': 'dtm', 'user_ids': [str(stu1.id), str(stu2.id)], 'per_user': 2},
        )
        assert r.status_code == 400 and r.json()['code'] == 'insufficient_keys'
        assert ExamKey.global_objects.filter(status='assigned').count() == 0

    def test_cannot_distribute_to_non_member(self, owner1, org, stu_b, dtm):
        _grant(org, dtm, 3)
        r = _post(owner1, 'orgkeys-distribute', {'exam_type': 'dtm', 'user_ids': [str(stu_b.id)]})
        assert r.status_code == 400 and r.json()['code'] == 'not_a_member'
        assert ExamKey.global_objects.filter(status='assigned').count() == 0

    def test_student_cannot_distribute_or_view(self, stu1, org, dtm):
        _grant(org, dtm, 3)
        assert (
            _post(
                stu1, 'orgkeys-distribute', {'exam_type': 'dtm', 'user_ids': [str(stu1.id)]}
            ).status_code
            == 403
        )
        assert _c(stu1).get(reverse('commerce:orgkeys-summary')).status_code == 403
        assert _post(stu1, 'orgkeys-revoke', {'key_ids': []}).status_code == 403

    def test_revoke_returns_remaining_down_and_skips_consumed(self, owner1, org, stu1, dtm):
        from apps.commerce import access_service

        _grant(org, dtm, 3)
        _post(
            owner1,
            'orgkeys-distribute',
            {'exam_type': 'dtm', 'user_ids': [str(stu1.id)], 'per_user': 2},
        )
        access_service.consume_access(stu1, 'dtm')
        ids = [str(k.id) for k in ExamKey.global_objects.filter(organization=org)]
        r = _post(owner1, 'orgkeys-revoke', {'key_ids': ids})
        assert r.json()['revoked'] == 2  # the consumed one is untouched
        s = _c(owner1).get(reverse('commerce:orgkeys-summary')).json()['summary'][0]
        assert (s['consumed'], s['revoked'], s['remaining']) == (1, 2, 0)


class TestRedeem:
    def test_member_redeems_available_key_and_it_is_usable(self, org, stu1, dtm):
        from apps.commerce import access_service

        _grant(org, dtm, 1)
        key = ExamKey.global_objects.get()
        r = _post(stu1, 'examkeys-redeem', {'code': key.code.lower()})
        assert r.status_code == 200 and r.json()['status'] == 'assigned'
        assert access_service.consume_access(stu1, 'dtm').reason == 'org_key'

    def test_student_of_org_a_cannot_redeem_org_b_key(self, org, org2, stu1, dtm):
        _grant(org2, dtm, 1)
        key = ExamKey.global_objects.get()
        r = _post(stu1, 'examkeys-redeem', {'code': key.code})
        assert r.status_code == 404 and r.json()['code'] == 'invalid_key'
        key.refresh_from_db()
        assert key.status == 'available' and key.assigned_to_id is None

    def test_service_level_cross_org_redeem_rejected(self, org, org2, stu1, dtm):
        _grant(org2, dtm, 1)
        key = ExamKey.global_objects.get()
        with pytest.raises(key_service.KeyError_):
            key_service.redeem_key(stu1, org2, key.code)  # stu1 is not a member of org2
        with pytest.raises(key_service.KeyError_):
            key_service.redeem_key(stu1, org, key.code)  # right membership, wrong org's key

    def test_unknown_and_wrong_org_errors_are_indistinguishable(self, org, org2, stu1, dtm):
        _grant(org2, dtm, 1)
        real = ExamKey.global_objects.get().code
        a = _post(stu1, 'examkeys-redeem', {'code': real}).json()
        b = _post(stu1, 'examkeys-redeem', {'code': 'ZZZZZZZZZZZZ'}).json()
        assert a == b

    def test_consumed_key_cannot_be_reused_or_redeemed(self, org, stu1, stu2, dtm):
        from apps.commerce import access_service

        _grant(org, dtm, 1)
        key = ExamKey.global_objects.get()
        _post(stu1, 'examkeys-redeem', {'code': key.code})
        assert access_service.consume_access(stu1, 'dtm', reference='x').allowed
        # same student: key is gone, free attempts are used instead
        assert access_service.consume_access(stu1, 'dtm').reason == 'free_attempt'
        r = _post(stu2, 'examkeys-redeem', {'code': key.code})
        assert r.status_code == 409 and r.json()['code'] == 'key_consumed'
        key.refresh_from_db()
        assert key.consumed_by_id == stu1.id and key.consumed_reference == 'x'

    def test_assigned_key_cannot_be_stolen(self, org, stu1, stu2, dtm):
        _grant(org, dtm, 1)
        key = ExamKey.global_objects.get()
        _post(stu1, 'examkeys-redeem', {'code': key.code})
        r = _post(stu2, 'examkeys-redeem', {'code': key.code})
        assert r.status_code == 409
        key.refresh_from_db()
        assert key.assigned_to_id == stu1.id

    def test_suspended_member_cannot_redeem(self, org, dtm):
        from apps.organizations.models import MembershipStatus

        u = make_user('susp')
        join(u, org, status=MembershipStatus.SUSPENDED)
        _grant(org, dtm, 1)
        with pytest.raises(key_service.KeyError_):
            key_service.redeem_key(u, org, ExamKey.global_objects.get().code)


class TestCrossTenantIsolation:
    def test_org_admin_sees_only_own_keys(self, owner1, owner2, org, org2, dtm):
        _grant(org, dtm, 3)
        _grant(org2, dtm, 7)
        s1 = _c(owner1).get(reverse('commerce:orgkeys-summary')).json()['summary']
        s2 = _c(owner2).get(reverse('commerce:orgkeys-summary')).json()['summary']
        assert s1[0]['issued'] == 3 and s2[0]['issued'] == 7
        assert _c(owner1).get(reverse('commerce:orgkeys-list')).json()['count'] == 3

    def test_org_admin_cannot_revoke_other_orgs_keys(self, owner1, org2, dtm):
        _grant(org2, dtm, 2)
        ids = [str(k.id) for k in ExamKey.global_objects.all()]
        assert _post(owner1, 'orgkeys-revoke', {'key_ids': ids}).json()['revoked'] == 0
        assert ExamKey.global_objects.filter(status='available').count() == 2

    def test_tenant_manager_isolates_rows(self, org, org2, dtm):
        _grant(org, dtm, 2)
        _grant(org2, dtm, 4)
        with tenant_context(org):
            assert ExamKey.objects.count() == 2
        with tenant_context(org2):
            assert ExamKey.objects.count() == 4
        assert ExamKey.objects.count() == 0  # no tenant context: fail closed

    def test_distribute_uses_only_own_org_keys(self, owner1, org, org2, stu1, dtm):
        _grant(org2, dtm, 5)  # org has none
        r = _post(owner1, 'orgkeys-distribute', {'exam_type': 'dtm', 'user_ids': [str(stu1.id)]})
        assert r.status_code == 400 and r.json()['code'] == 'insufficient_keys'

    def test_my_keys_lists_only_mine(self, org, stu1, stu2, owner1, dtm):
        _grant(org, dtm, 2)
        _post(owner1, 'orgkeys-distribute', {'exam_type': 'dtm', 'user_ids': [str(stu1.id)]})
        assert len(_c(stu1).get(reverse('commerce:examkeys-mine')).json()['keys']) == 1
        assert _c(stu2).get(reverse('commerce:examkeys-mine')).json()['keys'] == []

    def test_rls_enabled_on_key_tables(self, db):
        with connection.cursor() as cur:
            cur.execute(
                'SELECT tablename, rowsecurity FROM pg_tables WHERE tablename IN '
                "('commerce_examkey','commerce_examkeybatch') AND schemaname='public'"
            )
            rows = dict(cur.fetchall())
            cur.execute(
                'SELECT count(*) FROM pg_policies WHERE tablename IN '
                "('commerce_examkey','commerce_examkeybatch')"
            )
            n_policies = cur.fetchone()[0]
        assert rows == {'commerce_examkey': True, 'commerce_examkeybatch': True}
        assert n_policies == 8
