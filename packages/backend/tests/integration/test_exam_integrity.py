"""Mock-exam integrity: no correctness oracle, server-side deadline, resumable attempts.

Before this, an answer response carried ``is_correct`` while the answer could still be
changed (try A, then B, ... until the API says true), and the time limit was only
enforced by the WebSocket heartbeat, so plain HTTP clients had unlimited time.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.exams.models import ANSWER_GRACE_SECONDS, ExamAttempt
from apps.exams.tasks import expire_overdue_attempts
from core.tenant import unscoped_context
from tests.integration._onboarding import complete_onboarding
from tests.integration.test_exam_api import _make_published_mock, _make_qv


def _start(client, mock):
    resp = client.post(reverse('exams:attempt-start', args=[mock.id]))
    assert resp.status_code == 201, resp.content
    return resp.json()


def _answer(client, attempt_id, qv, option_id):
    return client.post(
        reverse('exams:attempt-answer', args=[attempt_id]),
        data={'question_version': str(qv.id), 'selected': {'id': option_id}},
        content_type='application/json',
    )


def _age(attempt_id, minutes):
    """Move the attempt's start back in time (started_at is auto_now_add)."""
    with unscoped_context():
        ExamAttempt.objects.filter(pk=attempt_id).update(
            started_at=timezone.now() - timedelta(minutes=minutes)
        )


@pytest.fixture
def setup(db, org, user, member, subject):
    complete_onboarding(user)
    qv = _make_qv(org, subject)
    mock = _make_published_mock(org, user, qvs=[qv])
    client = Client()
    client.force_login(user)
    return client, mock, qv


@pytest.mark.integration
class TestNoCorrectnessOracle:
    def test_answer_response_never_says_if_correct(self, setup):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        for option in (2, 1):
            body = _answer(client, attempt_id, qv, option).json()
            assert 'is_correct' not in body
            assert 'auto_correct' not in body

    def test_resume_returns_selection_without_correctness(self, setup):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _answer(client, attempt_id, qv, 2)

        again = _start(client, mock)
        assert again['attempt']['id'] == attempt_id
        assert len(again['answers']) == 1
        assert again['answers'][0]['selected'] == {'id': 2}
        assert 'is_correct' not in again['answers'][0]

    def test_exam_payload_has_question_type_and_no_key(self, setup):
        client, mock, _ = setup
        q = _start(client, mock)['exam']['questions'][0]['question']
        assert q['type'] == 'SC'
        assert all('is_correct' not in o for o in q['options'])

    def test_score_still_counts_after_submit(self, setup):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _answer(client, attempt_id, qv, 2)
        _answer(client, attempt_id, qv, 1)  # changed mind: last answer counts
        body = client.post(reverse('exams:attempt-submit', args=[attempt_id])).json()
        assert float(body['score']) == 100.0


@pytest.mark.integration
class TestServerDeadline:
    def test_attempt_reports_deadline_and_seconds_left(self, setup):
        client, mock, _ = setup
        attempt = _start(client, mock)['attempt']
        assert attempt['deadline']
        assert 59 * 60 < attempt['seconds_left'] <= 60 * 60

    def test_answer_within_grace_is_accepted(self, setup):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        # 60-minute mock, started 60 min ago: past the deadline but inside the grace.
        _age(attempt_id, 60)
        assert ANSWER_GRACE_SECONDS >= 5
        assert _answer(client, attempt_id, qv, 1).status_code == 201

    def test_late_answer_rejected_and_attempt_scored(
        self, setup, django_capture_on_commit_callbacks
    ):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _answer(client, attempt_id, qv, 1)
        _age(attempt_id, 61)

        with django_capture_on_commit_callbacks(execute=True):
            resp = _answer(client, attempt_id, qv, 2)
        assert resp.status_code == 409
        assert resp.json()['error']['code'] == 'attempt_expired'

        with unscoped_context():
            attempt = ExamAttempt.objects.get(pk=attempt_id)
        assert attempt.status == ExamAttempt.Status.EXPIRED
        assert attempt.submitted_at is not None
        # The answer given in time is scored; the late change was not applied.
        assert float(attempt.score) == 100.0

    def test_attempt_detail_closes_overdue_attempt(self, setup, django_capture_on_commit_callbacks):
        client, mock, _ = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _age(attempt_id, 90)
        with django_capture_on_commit_callbacks(execute=True):
            body = client.get(reverse('exams:attempt-detail', args=[attempt_id])).json()
        assert body['status'] == 'expired'
        assert body['seconds_left'] == 0

    def test_submit_after_deadline_returns_expired_result(
        self, setup, django_capture_on_commit_callbacks
    ):
        client, mock, qv = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _answer(client, attempt_id, qv, 1)
        _age(attempt_id, 61)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(reverse('exams:attempt-submit', args=[attempt_id]))
        assert resp.status_code == 200
        assert resp.json()['status'] == 'expired'

    def test_restart_of_expired_attempt_refused(self, setup, django_capture_on_commit_callbacks):
        client, mock, _ = setup
        attempt_id = _start(client, mock)['attempt']['id']
        _age(attempt_id, 61)
        with django_capture_on_commit_callbacks(execute=True):
            resp = client.post(reverse('exams:attempt-start', args=[mock.id]))
        assert resp.status_code == 400


@pytest.mark.integration
class TestExpireOverdueTask:
    def test_closes_only_overdue_attempts(self, db, org, user, user2, subject):
        from apps.organizations.models import Membership, MembershipStatus, OrgRole

        role = OrgRole.objects.get(organization=org, name='student')
        for u in (user, user2):
            m = Membership.objects.create(
                user=u, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
            )
            m.activate()
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        ids = []
        for u in (user, user2):
            c = Client()
            c.force_login(u)
            ids.append(_start(c, mock)['attempt']['id'])
        _age(ids[0], 61)  # overdue
        _age(ids[1], 30)  # still running

        result = expire_overdue_attempts()
        assert result == {'expired': 1}
        with unscoped_context():
            statuses = {
                str(k): v
                for k, v in ExamAttempt.objects.filter(pk__in=ids).values_list('id', 'status')
            }
        assert statuses[ids[0]] == 'expired'
        assert statuses[ids[1]] == 'in_progress'

        assert expire_overdue_attempts() == {'expired': 0}  # idempotent

    def test_beat_schedule_runs_it(self):
        from core.celery import app

        tasks = {e['task'] for e in app.conf.beat_schedule.values()}
        assert 'exams.expire_overdue_attempts' in tasks
