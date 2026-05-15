"""
Task 5 — Leaderboard integration tests.

Tekshiriladi:
  - record_attempt → ZADD ga to'g'ri yozadi (mock + best-of global/region/tenant)
  - GT semantics: yangi score eski'dan past bo'lsa global'da o'zgarmaydi
  - REST API: top-N + me payload
  - Signal: ExamAttempt SUBMITTED + score → leaderboard avtomat update
  - Tenant isolation: boshqa org leaderboard'i ko'rinmaydi (lb:tenant:<org>)

`mock_redis` autouse fixture (conftest.py) fakeredis bilan ishlaydi.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.engagement import leaderboard
from apps.exams.models import ExamAttempt, MockExam, MockExamQuestion
from core.tenant import tenant_context, unscoped_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_qv(org, subject):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[{'id': 1, 'text': 'A', 'is_correct': True}],
        )


def _make_published_mock(org, user, qvs):
    now = timezone.now()
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title='LB Mock',
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=False,
            created_by=user,
        )
        for i, qv in enumerate(qvs, start=1):
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i)
    return mock


# ─── Service-level tests ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestLeaderboardService:
    def test_record_attempt_writes_to_mock_global_tenant(self, db, org, user):
        leaderboard.record_attempt(user_id=user.id, score=85.0, mock_id='abc', org_id=org.id)
        assert leaderboard.score_of(leaderboard.key_mock('abc'), user.id) == 85.0
        assert leaderboard.score_of(leaderboard.key_global(), user.id) == 85.0
        assert leaderboard.score_of(leaderboard.key_tenant(org.id), user.id) == 85.0

    def test_global_keeps_best_score_across_mocks(self, db, org, user):
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m1', org_id=org.id)
        leaderboard.record_attempt(user_id=user.id, score=90, mock_id='m2', org_id=org.id)
        leaderboard.record_attempt(user_id=user.id, score=60, mock_id='m3', org_id=org.id)

        # Mock-specific har biri o'zining score'ini saqlaydi
        assert leaderboard.score_of(leaderboard.key_mock('m1'), user.id) == 70
        assert leaderboard.score_of(leaderboard.key_mock('m2'), user.id) == 90
        assert leaderboard.score_of(leaderboard.key_mock('m3'), user.id) == 60

        # Global = best (90)
        assert leaderboard.score_of(leaderboard.key_global(), user.id) == 90

    def test_top_returns_descending_order(self, db, org, user, user2):
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m', org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=85, mock_id='m', org_id=org.id)

        top = leaderboard.top(leaderboard.key_mock('m'), limit=10)
        assert len(top) == 2
        # Birinchi - eng yuqori
        assert top[0][0] == str(user2.id)
        assert top[0][1] == 85.0
        assert top[1][0] == str(user.id)

    def test_rank_zero_indexed(self, db, org, user, user2):
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m', org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=85, mock_id='m', org_id=org.id)

        assert leaderboard.rank(leaderboard.key_mock('m'), user2.id) == 0
        assert leaderboard.rank(leaderboard.key_mock('m'), user.id) == 1

    def test_rank_returns_none_if_not_in_zset(self, db, user):
        assert leaderboard.rank(leaderboard.key_global(), user.id) is None


# ─── Signal tests (ExamAttempt SUBMITTED → leaderboard) ──────────────────────


@pytest.mark.integration
class TestLeaderboardSignal:
    def test_submitting_attempt_updates_leaderboard(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, [qv])

        with tenant_context(org):
            attempt = ExamAttempt.objects.create(
                organization=org,
                exam=mock,
                user=user,
                status=ExamAttempt.Status.SUBMITTED,
                score=75.5,
            )

        # Leaderboard avtomat update bo'lgan
        assert leaderboard.score_of(leaderboard.key_mock(mock.id), user.id) == 75.5
        assert leaderboard.score_of(leaderboard.key_global(), user.id) == 75.5
        assert leaderboard.score_of(leaderboard.key_tenant(org.id), user.id) == 75.5

        # Save'ga tegmagan attempt sodir bo'ldi
        with unscoped_context():
            assert ExamAttempt.objects.filter(pk=attempt.id).exists()

    def test_in_progress_attempt_does_not_update(self, db, org, user, member, subject):
        qv = _make_qv(org, subject)
        mock = _make_published_mock(org, user, [qv])

        with tenant_context(org):
            ExamAttempt.objects.create(
                organization=org,
                exam=mock,
                user=user,
                status=ExamAttempt.Status.IN_PROGRESS,
                score=None,
            )

        # IN_PROGRESS bo'lgani uchun leaderboard'da yo'q
        assert leaderboard.score_of(leaderboard.key_global(), user.id) is None


# ─── REST API tests ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestLeaderboardAPI:
    def test_global_leaderboard_top(self, db, org, user, user2, member):
        leaderboard.record_attempt(user_id=user.id, score=70, mock_id='m', org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=85, mock_id='m', org_id=org.id)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-global'))

        assert resp.status_code == 200
        data = resp.json()
        assert data['scope'] == 'global'
        assert data['total'] == 2
        assert len(data['entries']) == 2
        # Birinchi - user2 (85 ball, eng yuqori) — UUID string sifatida
        assert data['entries'][0]['user_id'] == str(user2.id)
        assert data['entries'][0]['rank'] == 1
        assert data['entries'][0]['score'] == 85.0
        # me payload (user2 emas, user — joriy login qiluvchi)
        assert data['me']['in_leaderboard'] is True
        assert data['me']['rank'] == 2  # 1-indexed

    def test_tenant_leaderboard(self, db, org, user, member):
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id='m', org_id=org.id)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-tenant'))
        assert resp.status_code == 200
        data = resp.json()
        assert data['scope'] == f'tenant:{org.id}'
        assert data['me']['rank'] == 1

    def test_tenant_isolation_other_org_invisible(self, db, org, org2, user, user2, member):
        # User org'ga member, score yozilgan
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id='m', org_id=org.id)
        # User2 org2'ga score yozilgan
        leaderboard.record_attempt(user_id=user2.id, score=90, mock_id='m', org_id=org2.id)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-tenant'))
        data = resp.json()
        # Org tenant leaderboard'ida faqat user (org member)
        assert data['total'] == 1
        assert data['entries'][0]['user_id'] == str(user.id)

    def test_me_endpoint_with_scope(self, db, org, user, member):
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id='m', org_id=org.id)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-me'), {'scope': 'global'})
        assert resp.status_code == 200
        data = resp.json()
        assert data['in_leaderboard'] is True
        assert data['rank'] == 1
        assert data['score'] == 80.0

    def test_me_endpoint_unknown_scope(self, db, user, member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-me'), {'scope': 'invalid'})
        assert resp.status_code == 400

    def test_limit_validation(self, db, user, member, org):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-global'), {'limit': '99999'})
        assert resp.status_code == 400

    def test_mock_leaderboard(self, db, org, user, user2, member):
        import uuid

        mock_alpha = uuid.uuid4()
        mock_beta = uuid.uuid4()
        leaderboard.record_attempt(user_id=user.id, score=60, mock_id=mock_alpha, org_id=org.id)
        leaderboard.record_attempt(user_id=user2.id, score=95, mock_id=mock_alpha, org_id=org.id)
        leaderboard.record_attempt(user_id=user.id, score=80, mock_id=mock_beta, org_id=org.id)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('engagement:lb-mock', kwargs={'mock_id': mock_alpha}))
        data = resp.json()
        assert data['total'] == 2
        user_ids = {e['user_id'] for e in data['entries']}
        assert user_ids == {str(user.id), str(user2.id)}
