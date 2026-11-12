"""Results are gated behind onboarding; taking exams is not. Staff are never gated."""

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.exams.models import ExamAttempt
from tests.integration._onboarding import complete_onboarding
from tests.integration.test_exam_api import _make_published_mock, _make_qv
from tests.integration.test_sittings import Env

pytestmark = pytest.mark.integration


def _client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def attempt(db, org, user, member, subject):
    from core.tenant import tenant_context

    qv = _make_qv(org, subject)
    mock = _make_published_mock(org, user, qvs=[qv])
    with tenant_context(org):
        return ExamAttempt.objects.create(
            exam=mock,
            user=user,
            organization=org,
            status=ExamAttempt.Status.SUBMITTED,
            submitted_at=timezone.now(),
            score=77,
        )


class TestAttemptResult:
    def test_finished_attempt_blocked_before_onboarding(self, attempt, user):
        resp = _client(user).get(reverse('exams:attempt-detail', args=[attempt.id]))
        assert resp.status_code == 403
        body = resp.json()
        assert body['error']['code'] == 'onboarding_required'
        assert 'graduation_year' in body['error']['details']['missing']
        assert '77' not in resp.content.decode()

    def test_allowed_after_onboarding(self, attempt, user):
        complete_onboarding(user)
        resp = _client(user).get(reverse('exams:attempt-detail', args=[attempt.id]))
        assert resp.status_code == 200

    def test_staff_not_gated(self, attempt, user):
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        assert (
            _client(user).get(reverse('exams:attempt-detail', args=[attempt.id])).status_code == 200
        )

    def test_in_progress_attempt_stays_open(self, attempt, user):
        ExamAttempt.global_objects.filter(pk=attempt.pk).update(
            status=ExamAttempt.Status.IN_PROGRESS
        )
        resp = _client(user).get(reverse('exams:attempt-detail', args=[attempt.id]))
        assert resp.status_code == 200

    def test_submit_works_but_score_withheld_until_onboarding(self, db, org, user, member, subject):
        mock = _make_published_mock(org, user, qvs=[_make_qv(org, subject)])
        c = _client(user)
        start = c.post(reverse('exams:attempt-start', args=[mock.id]))
        assert start.status_code == 201  # starting an exam is not gated
        attempt_id = start.json()['attempt']['id']
        resp = c.post(reverse('exams:attempt-submit', args=[attempt_id]))
        assert resp.status_code == 200
        body = resp.json()
        assert body['status'] == 'submitted'
        assert body['score'] is None and body['onboarding_required'] is True
        complete_onboarding(user)
        body = c.post(reverse('exams:attempt-submit', args=[attempt_id])).json()
        assert 'onboarding_required' not in body


class TestSittingResults:
    @pytest.fixture
    def env(self, db, org, subject):
        e = Env(org, subject, onboarded=False)
        e.run_to('published')
        return e

    def test_blocked_then_allowed(self, env):
        student = env.students[0]
        urls = [
            reverse('exams:sitting-mine'),
            reverse('exams:sitting-my-result', args=[env.sitting_id]),
            reverse('exams:certificate-mine'),
        ]
        c = _client(student)
        for url in urls:
            resp = c.get(url)
            assert resp.status_code == 403, url
            assert resp.json()['error']['code'] == 'onboarding_required'
        complete_onboarding(student)
        for url in urls:
            assert c.get(url).status_code == 200, url

    def test_gate_is_per_user(self, env):
        complete_onboarding(env.students[1])
        assert _client(env.students[0]).get(reverse('exams:sitting-mine')).status_code == 403
        assert _client(env.students[1]).get(reverse('exams:sitting-mine')).status_code == 200

    def test_staff_unaffected(self, env):
        env.admin.is_staff = True
        env.admin.save(update_fields=['is_staff'])
        assert _client(env.admin).get(reverse('exams:sitting-mine')).status_code == 200

    def test_anonymous_gets_auth_error_not_onboarding(self, env):
        resp = Client().get(reverse('exams:sitting-mine'))
        assert resp.status_code in (401, 403)
        assert 'onboarding' not in resp.content.decode()
