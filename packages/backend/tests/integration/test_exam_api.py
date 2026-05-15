"""
Task 4 — Exam Engine REST API integration tests.

End-to-end Django Client orqali (TenantMiddleware to'liq ishlaydi):
  - Mock list, detail, start
  - Attempt: answer, submit (→ Celery score finalize), anticheat (3 strikes)
  - Dispute: file (signal trigger qiladi)
  - Practice: create, answer, finish
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    MockExamQuestion,
    PracticeSession,
    QuestionDispute,
    UserAnswer,
)
from core.tenant import tenant_context, unscoped_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_qv(org, subject, *, options=None):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=options
            or [
                {'id': 1, 'text': 'A', 'is_correct': True},
                {'id': 2, 'text': 'B', 'is_correct': False},
            ],
        )


def _make_published_mock(org, user, *, qvs=None):
    now = timezone.now()
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title='Test Mock',
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=1),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=False,
            created_by=user,
        )
        for i, qv in enumerate(qvs or [], start=1):
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i, points=1)
    return mock


# ─── Mock list / detail / start ───────────────────────────────────────────────


@pytest.mark.integration
class TestMockListAndStart:
    def test_list_published_mocks(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('exams:mock-list'))

        assert resp.status_code == 200
        data = resp.json()
        assert data['count'] == 1
        assert data['results'][0]['question_count'] == 1

    def test_start_attempt_creates_in_progress(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        resp = client.post(reverse('exams:attempt-start', args=[mock.id]))

        assert resp.status_code == 201
        body = resp.json()
        assert body['attempt']['status'] == 'in_progress'
        assert len(body['exam']['questions']) == 1
        # is_correct frontend'ga ko'rinmasligi kerak (XSS va cheat himoya)
        assert 'is_correct' not in body['exam']['questions'][0]['question']['options'][0]

    def test_start_attempt_idempotent(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        resp1 = client.post(reverse('exams:attempt-start', args=[mock.id]))
        resp2 = client.post(reverse('exams:attempt-start', args=[mock.id]))

        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()['attempt']['id'] == resp2.json()['attempt']['id']


# ─── Answer / submit / score finalize ─────────────────────────────────────────


@pytest.mark.integration
class TestAnswerAndSubmit:
    def test_answer_correct_then_submit_score_100(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        start_resp = client.post(reverse('exams:attempt-start', args=[mock.id]))
        attempt_id = start_resp.json()['attempt']['id']

        # To'g'ri javob (id=1 is_correct=True)
        ans_resp = client.post(
            reverse('exams:attempt-answer', args=[attempt_id]),
            data={'question_version': str(qv.id), 'selected': {'id': 1}},
            content_type='application/json',
        )
        assert ans_resp.status_code == 201
        assert ans_resp.json()['is_correct'] is True

        # Submit
        sub_resp = client.post(reverse('exams:attempt-submit', args=[attempt_id]))
        assert sub_resp.status_code == 200
        body = sub_resp.json()
        assert body['status'] == 'submitted'
        assert float(body['score']) == 100.0
        assert body['correct_count'] == 1

    def test_answer_wrong_score_zero(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt'][
            'id'
        ]

        client.post(
            reverse('exams:attempt-answer', args=[attempt_id]),
            data={'question_version': str(qv.id), 'selected': {'id': 2}},
            content_type='application/json',
        )
        sub = client.post(reverse('exams:attempt-submit', args=[attempt_id])).json()
        assert float(sub['score']) == 0.0


# ─── Anti-cheat (3 strikes) ───────────────────────────────────────────────────


@pytest.mark.integration
class TestAntiCheat:
    def test_three_strikes_cancels_attempt(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt'][
            'id'
        ]

        url = reverse('exams:attempt-anticheat', args=[attempt_id])
        for _ in range(3):
            client.post(url, data={'event_type': 'tab_switch'}, content_type='application/json')

        with unscoped_context():
            attempt = ExamAttempt.objects.get(pk=attempt_id)
            assert attempt.status == ExamAttempt.Status.CANCELLED
            assert attempt.strikes == 3
            assert attempt.cancel_reason == ExamAttempt.CancelReason.TAB_SWITCH
            assert AntiCheatEvent.objects.filter(attempt=attempt).count() == 3


# ─── Dispute ──────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestFileDispute:
    def test_file_dispute_creates_record(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt'][
            'id'
        ]

        resp = client.post(
            reverse('exams:attempt-dispute', args=[attempt_id]),
            data={'question_version': str(qv.id), 'reason': 'wrong_answer', 'note': "noto'g'ri"},
            content_type='application/json',
        )
        assert resp.status_code == 201
        with unscoped_context():
            assert QuestionDispute.objects.filter(question_version=qv).count() == 1

    def test_duplicate_dispute_rejected(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client = Client()
        client.force_login(user)
        attempt_id = client.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt'][
            'id'
        ]

        url = reverse('exams:attempt-dispute', args=[attempt_id])
        body = {'question_version': str(qv.id), 'reason': 'wrong_answer'}
        client.post(url, data=body, content_type='application/json')
        resp2 = client.post(url, data=body, content_type='application/json')
        assert resp2.status_code == 400


# ─── Practice ─────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestPractice:
    def test_create_and_finish_practice(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)

        client = Client()
        client.force_login(user)
        # Create
        cresp = client.post(
            reverse('exams:practice-create'),
            data={'blueprint': [{'count': 5}]},
            content_type='application/json',
        )
        assert cresp.status_code == 201
        sid = cresp.json()['id']

        # Answer
        client.post(
            reverse('exams:practice-answer', args=[sid]),
            data={'question_version': str(qv.id), 'selected': {'id': 1}},
            content_type='application/json',
        )

        # Finish
        fresp = client.post(reverse('exams:practice-finish', args=[sid]))
        assert fresp.status_code == 200
        body = fresp.json()
        assert body['status'] == 'completed'
        assert body['correct_count'] == 1
        assert body['total_count'] == 1

        # DB
        with unscoped_context():
            session = PracticeSession.objects.get(pk=sid)
            assert session.status == PracticeSession.Status.COMPLETED
            assert UserAnswer.objects.filter(session=session).count() == 1

    def test_invalid_blueprint_rejected(self, db, org, user, member):
        client = Client()
        client.force_login(user)

        resp = client.post(
            reverse('exams:practice-create'),
            data={'blueprint': []},  # empty
            content_type='application/json',
        )
        assert resp.status_code == 400


# ─── Cross-user permission (boshqa user attempt ko'rishi mumkin emas) ─────────


@pytest.mark.integration
class TestPermissions:
    def test_other_user_cannot_view_attempt(self, db, org, user, user2, member, subject):
        # user2 ni org'ga member qilish
        from apps.organizations.models import Membership, MembershipStatus, OrgRole

        role = OrgRole.objects.get(organization=org, name='student')
        m = Membership.objects.create(
            user=user2,
            organization=org,
            role=role,
            status=MembershipStatus.ACTIVE,
            is_primary=True,
        )
        m.activate()

        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, qvs=[qv])

        client1 = Client()
        client1.force_login(user)
        attempt_id = client1.post(reverse('exams:attempt-start', args=[mock.id])).json()['attempt'][
            'id'
        ]

        # user2 user'ning attempt'ini ko'rmoqchi
        client2 = Client()
        client2.force_login(user2)
        resp = client2.get(reverse('exams:attempt-detail', args=[attempt_id]))
        assert resp.status_code == 403
