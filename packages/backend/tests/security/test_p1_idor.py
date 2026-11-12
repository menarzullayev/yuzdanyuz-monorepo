"""C12 — IDOR: leaderboard snapshots/mocks of another tenant, practice answer oracle."""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.engagement import leaderboard
from apps.engagement.models import LeaderboardSnapshot
from apps.exams.models import MockExam, MockExamQuestion, UserAnswer
from core.tenant import tenant_context, unscoped_context

pytestmark = [pytest.mark.security, pytest.mark.django_db]


def _qv(org, subject):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[
                {'id': 1, 'text': 'A', 'is_correct': True},
                {'id': 2, 'text': 'B', 'is_correct': False},
            ],
        )


def _mock(org, author, *, qvs=(), is_public=False, starts_in=timedelta(minutes=-1)):
    now = timezone.now()
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title='Mock',
            duration_minutes=60,
            scheduled_at=now + starts_in,
            closes_at=now + starts_in + timedelta(hours=3),
            status=MockExam.Status.PUBLISHED,
            is_public=is_public,
            created_by=author,
        )
        for i, qv in enumerate(qvs, start=1):
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i, points=1)
    return mock


def _client(user):
    c = Client()
    c.force_login(user)
    return c


class TestLeaderboardTenantScope:
    def test_history_of_another_tenant_is_not_served(self, org, org2, user, member):
        LeaderboardSnapshot.objects.create(
            period='weekly',
            period_key='2026-W46',
            scope_kind='tenant',
            scope_id=str(org2.id),
            total=1,
            entries=[{'user_id': 'secret', 'score': 99.0, 'rank': 1}],
        )
        resp = _client(user).get(
            reverse('engagement:lb-history'),
            {'period': 'weekly', 'scope_kind': 'tenant', 'scope_id': str(org2.id)},
        )
        assert resp.status_code == 404

    def test_history_of_own_tenant_is_served(self, org, user, member):
        LeaderboardSnapshot.objects.create(
            period='weekly',
            period_key='2026-W46',
            scope_kind='tenant',
            scope_id=str(org.id),
            total=1,
            entries=[],
        )
        resp = _client(user).get(
            reverse('engagement:lb-history'), {'period': 'weekly', 'scope_kind': 'tenant'}
        )
        assert resp.status_code == 200
        assert resp.json()['scope_id'] == str(org.id)

    def test_history_of_private_mock_of_another_tenant(self, org, org2, user, user2, member):
        foreign = _mock(org2, user2)
        LeaderboardSnapshot.objects.create(
            period='weekly',
            period_key='2026-W46',
            scope_kind='mock',
            scope_id=str(foreign.id),
            total=1,
            entries=[],
        )
        resp = _client(user).get(
            reverse('engagement:lb-history'),
            {'period': 'weekly', 'scope_kind': 'mock', 'scope_id': str(foreign.id)},
        )
        assert resp.status_code == 404

    def test_live_mock_leaderboard_of_another_tenant(self, org, org2, user, user2, member):
        foreign = _mock(org2, user2)
        leaderboard.record_attempt(user_id=user2.id, score=90, mock_id=foreign.id, org_id=org2.id)
        c = _client(user)
        assert c.get(reverse('engagement:lb-mock', kwargs={'mock_id': foreign.id})).status_code == (
            404
        )
        me = c.get(reverse('engagement:lb-me'), {'scope': f'mock:{foreign.id}'})
        assert me.status_code == 404

    def test_public_mock_leaderboard_is_visible(self, org, org2, user, user2, member):
        public = _mock(org2, user2, is_public=True)
        leaderboard.record_attempt(user_id=user2.id, score=90, mock_id=public.id, org_id=org2.id)
        resp = _client(user).get(reverse('engagement:lb-mock', kwargs={'mock_id': public.id}))
        assert resp.status_code == 200
        assert resp.json()['total'] == 1


class TestPracticeAnswerOracle:
    def _session(self, client):
        resp = client.post(
            reverse('exams:practice-create'),
            data={'blueprint': [{'count': 5}]},
            content_type='application/json',
        )
        assert resp.status_code == 201
        return resp.json()['id']

    def _answer(self, client, sid, qv):
        return client.post(
            reverse('exams:practice-answer', args=[sid]),
            data={'question_version': str(qv.id), 'selected': {'id': 1}},
            content_type='application/json',
        )

    def test_question_of_a_live_exam_is_not_graded(self, org, user, member, subject):
        qv = _qv(org, subject)
        _mock(org, user, qvs=[qv])
        c = _client(user)
        resp = self._answer(c, self._session(c), qv)
        assert resp.status_code == 400
        assert 'is_correct' not in resp.json()
        with unscoped_context():
            assert not UserAnswer.objects.filter(question_version=qv).exists()

    def test_question_of_an_upcoming_exam_is_not_graded(self, org, user, member, subject):
        qv = _qv(org, subject)
        _mock(org, user, qvs=[qv], starts_in=timedelta(days=2))
        c = _client(user)
        assert self._answer(c, self._session(c), qv).status_code == 400

    def test_question_of_another_tenant_is_not_graded(self, org, org2, user, member, subject):
        qv = _qv(org2, subject)
        c = _client(user)
        resp = self._answer(c, self._session(c), qv)
        assert resp.status_code == 400
        assert 'is_correct' not in resp.json()

    def test_quarantined_question_is_not_graded(self, org, user, member, subject):
        qv = _qv(org, subject)
        with unscoped_context():
            QuestionVersion.objects.filter(pk=qv.pk).update(is_quarantined=True)
        c = _client(user)
        assert self._answer(c, self._session(c), qv).status_code == 400

    def test_question_of_a_closed_exam_can_be_practised(self, org, user, member, subject):
        qv = _qv(org, subject)
        _mock(org, user, qvs=[qv], starts_in=timedelta(days=-2))
        c = _client(user)
        resp = self._answer(c, self._session(c), qv)
        assert resp.status_code == 201
        assert resp.json()['is_correct'] is True
