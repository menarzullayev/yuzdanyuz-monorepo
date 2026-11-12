"""C01 — malformed request bodies are answered with 400, never a 500."""

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import Question, QuestionVersion
from apps.commerce.models import PaymentIntent
from apps.intelligence.models import OpenEndedSubmission
from core.tenant import tenant_context

pytestmark = [pytest.mark.security, pytest.mark.django_db]


def _client(user=None):
    c = Client(raise_request_exception=False)
    if user is not None:
        c.force_login(user)
    return c


def _post(client, url, payload):
    body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    return client.post(url, data=body, content_type='application/json')


class TestWallet:
    @pytest.fixture(autouse=True)
    def _stub_payments(self, settings):
        settings.PAYMENTS_STUB_ALLOWED = True

    @pytest.mark.parametrize(
        'amount', ['NaN', 'Infinity', '-Infinity', 'sNaN', '1e20', '1e999', [1], {'a': 1}, True]
    )
    def test_topup_rejects_non_finite_or_huge_amounts(self, user, member, amount):
        resp = _post(
            _client(user),
            reverse('commerce:wallet-topup'),
            {'provider': 'payme', 'amount_uzs': amount},
        )
        assert resp.status_code == 400
        assert not PaymentIntent.objects.exists()

    def test_topup_rejects_unknown_provider_type(self, user, member):
        resp = _post(
            _client(user),
            reverse('commerce:wallet-topup'),
            {'provider': ['payme'], 'amount_uzs': 5},
        )
        assert resp.status_code == 400

    def test_topup_valid_request_still_works(self, user, member):
        resp = _post(
            _client(user),
            reverse('commerce:wallet-topup'),
            {'provider': 'payme', 'amount_uzs': 10000},
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize(
        'payload',
        [
            {'coins': 1, 'description': 'x' * 10_000},
            {'coins': 1, 'description': {'a': 1}},
            {'coins': 10**30},
            {'coins': 'many'},
        ],
    )
    def test_spend_validates_payload(self, user, member, payload):
        resp = _post(_client(user), reverse('commerce:wallet-spend'), payload)
        assert resp.status_code == 400

    def test_non_object_body(self, user, member):
        resp = _post(_client(user), reverse('commerce:wallet-spend'), [1, 2])
        assert resp.status_code == 400


def _oe_question(org, subject):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.OPEN_ENDED
        )
        return QuestionVersion.objects.create(
            question=q, version_number=1, content={'text': 'Essay'}, options=[]
        )


class TestOpenEnded:
    @pytest.mark.parametrize(
        'payload',
        [
            {'question_version': 'not-a-uuid', 'content': 'essay'},
            {'question_version': ['x'], 'content': 'essay'},
            {'question_version': '{qv}', 'content': {'text': 'essay'}},
            {'question_version': '{qv}', 'content': 'x' * 200_000},
            {'question_version': '{qv}', 'content': 'essay', 'submission_type': ['essay']},
        ],
    )
    def test_submit_validates_payload(self, org, user, member, subject, payload):
        qv = _oe_question(org, subject)
        data = {k: (v.format(qv=qv.id) if isinstance(v, str) else v) for k, v in payload.items()}
        resp = _post(_client(user), reverse('intelligence:openended-submit'), data)
        assert resp.status_code == 400
        assert not OpenEndedSubmission.objects.exists()

    @pytest.mark.parametrize(
        'payload',
        [
            {'action': 'approve', 'score': 'NaN'},
            {'action': 'approve', 'score': 'abc'},
            {'action': 'approve', 'score': 10**9},
            {'action': 'approve', 'score': -5},
            {'action': 'approve', 'score': 50, 'notes': ['x']},
            {'action': ['approve']},
        ],
    )
    def test_qa_validates_payload(self, org, user, member, subject, superuser, payload):
        qv = _oe_question(org, subject)
        sub = OpenEndedSubmission.objects.create(
            user=user, question_version=qv, submission_type='essay', content='essay'
        )
        resp = _post(
            _client(superuser), reverse('intelligence:openended-qa', args=[sub.id]), payload
        )
        assert resp.status_code == 400
        sub.refresh_from_db()
        assert sub.human_score is None


class TestAuthBodies:
    @pytest.fixture(autouse=True)
    def _otp_on(self, settings):
        settings.ENABLE_SMS_OTP = True

    @pytest.mark.parametrize(
        ('url', 'payload'),
        [
            ('/api/auth/otp/send/', {'phone': 998901234567}),
            ('/api/auth/otp/send/', ['phone']),
            ('/api/auth/otp/send/', '"just a string"'),
            ('/api/auth/otp/verify/', {'phone': '+998901234567', 'code': 123456}),
            ('/api/auth/otp/verify/', {'phone': None, 'code': '123456'}),
            ('/api/auth/telegram/', {'init_data': {'a': 1}}),
            ('/api/auth/telegram/', [1]),
            ('/api/auth/tg/complete/', {'token': 123}),
            ('/api/auth/tg/complete/', ['token']),
        ],
    )
    def test_wrong_json_types_are_400(self, url, payload):
        resp = _post(_client(), url, payload)
        assert resp.status_code == 400
