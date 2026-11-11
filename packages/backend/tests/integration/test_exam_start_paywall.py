"""Monetisation: exam-start hook (apps.exams.views.StartAttemptView)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.commerce.models import ExamAccessUse
from apps.exams.models import ExamAttempt, MockExam
from core.tenant import tenant_context
from tests.integration._monetisation import exam_type

pytestmark = pytest.mark.integration


def _mock(org, user, *, public=True, etype='dtm'):
    now = timezone.now()
    with tenant_context(org):
        return MockExam.objects.create(
            organization=org,
            title=f'm-{etype}-{MockExam.global_objects.count()}',
            exam_type=etype,
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=1),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=public,
            created_by=user,
        )


def _start(user, mock):
    c = Client()
    c.force_login(user)
    return c.post(reverse('exams:attempt-start', args=[mock.id]))


@pytest.fixture(autouse=True)
def types(db):
    exam_type('dtm')


def test_flag_off_by_default_changes_nothing(db, org, user, member, settings):
    settings.BILLING_FREE_ATTEMPTS = 0
    assert _start(user, _mock(org, user)).status_code == 201
    assert ExamAccessUse.objects.count() == 0


def test_paywall_402_after_free_attempts_and_no_attempt_left_behind(
    db, org, user, member, settings
):
    settings.BILLING_ENFORCE_EXAM_ACCESS = True
    settings.BILLING_FREE_ATTEMPTS = 2
    mocks = [_mock(org, user) for _ in range(3)]
    assert _start(user, mocks[0]).status_code == 201
    assert _start(user, mocks[1]).status_code == 201
    r = _start(user, mocks[2])
    assert r.status_code == 402
    assert r.json()['error']['code'] == 'payment_required'
    assert not ExamAttempt.objects.filter(exam=mocks[2], user=user).exists()
    assert ExamAccessUse.objects.filter(user=user).count() == 2


def test_resuming_attempt_does_not_consume_again(db, org, user, member, settings):
    settings.BILLING_ENFORCE_EXAM_ACCESS = True
    settings.BILLING_FREE_ATTEMPTS = 1
    m = _mock(org, user)
    assert _start(user, m).status_code == 201
    assert _start(user, m).status_code == 201  # idempotent re-entry
    assert ExamAccessUse.objects.filter(user=user).count() == 1


def test_private_org_mock_is_not_paywalled(db, org, user, member, settings):
    settings.BILLING_ENFORCE_EXAM_ACCESS = True
    settings.BILLING_FREE_ATTEMPTS = 0
    assert _start(user, _mock(org, user, public=False)).status_code == 201


def test_unknown_exam_type_on_mock_is_denied(db, org, user, member, settings):
    settings.BILLING_ENFORCE_EXAM_ACCESS = True
    r = _start(user, _mock(org, user, etype='mystery'))
    assert r.status_code == 402 and r.json()['error']['code'] == 'exam_type_unknown'
