"""
Task 6 — AI Diagnostika integration tests.

Tekshiriladi:
  - update_user_mastery: SkillTag profile yaratiladi va Bayesian update qilinadi
  - get_weak_skills, get_strong_skills, get_user_summary
  - Signal: UserAnswer post_save → mastery update (CELERY eager)
  - AI Tutor REST endpoint (mock LLM)
  - Open-ended REST endpoint (mock LLM)
  - QA workflow (admin only)
"""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import ExamAttempt, MockExam, MockExamQuestion, UserAnswer
from apps.intelligence import services, tasks
from apps.intelligence.models import AIFeedback, OpenEndedSubmission, SkillTag, UserSkillProfile
from core.tenant import tenant_context, unscoped_context

# ─── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def skill_algebra(db):
    return SkillTag.objects.create(name='Algebra', slug='algebra')


@pytest.fixture
def skill_geometry(db):
    return SkillTag.objects.create(name='Geometriya', slug='geo')


@pytest.fixture
def question_with_skills(db, org, subject, skill_algebra, skill_geometry):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q.skills.add(skill_algebra, skill_geometry)
        return q


@pytest.fixture
def question_version(db, org, question_with_skills):
    with tenant_context(org):
        return QuestionVersion.objects.create(
            question=question_with_skills,
            version_number=1,
            content={'text': 'Solve 2x + 3 = 7'},
            options=[{'id': 1, 'text': '2', 'is_correct': True}],
        )


def _make_published_mock(org, user, qv):
    now = timezone.now()
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title='M',
            duration_minutes=60,
            scheduled_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            is_public=False,
            created_by=user,
        )
        MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=1)
    return mock


# ─── Service: update_user_mastery ────────────────────────────────────────────


@pytest.mark.integration
class TestUpdateUserMastery:
    def test_correct_answer_increases_alpha(self, db, user, question_with_skills):
        services.update_user_mastery(user, question_with_skills, is_correct=True)
        # Skill 1 ta — 2 ta SkillTag bog'langan, ikkalasi update bo'ladi
        profiles = UserSkillProfile.objects.filter(user=user)
        assert profiles.count() == 2
        for p in profiles:
            assert p.alpha == 2
            assert p.beta == 1
            assert p.mastery == pytest.approx(2 / 3)

    def test_incorrect_answer_increases_beta(self, db, user, question_with_skills):
        services.update_user_mastery(user, question_with_skills, is_correct=False)
        for p in UserSkillProfile.objects.filter(user=user):
            assert p.alpha == 1
            assert p.beta == 2

    def test_idempotent_get_or_create(self, db, user, question_with_skills):
        services.update_user_mastery(user, question_with_skills, is_correct=True)
        services.update_user_mastery(user, question_with_skills, is_correct=True)
        # 2 ta correct → alpha=3
        p = UserSkillProfile.objects.filter(user=user).first()
        assert p.alpha == 3


# ─── Service: get_weak/strong/summary ────────────────────────────────────────


@pytest.mark.integration
class TestGetWeakStrong:
    def test_weak_skills_filtered_by_min_attempts(
        self, db, user, question_with_skills, skill_algebra
    ):
        # 1 ta correct — 1 attempt total (alpha+beta-2=1) — min_attempts=3 dan past
        services.update_user_mastery(user, question_with_skills, is_correct=False)
        weak = services.get_weak_skills(user, min_attempts=3)
        assert len(weak) == 0

        # 4 ta incorrect → alpha+beta-2 = 4 (min_attempts ga yetadi)
        for _ in range(3):
            services.update_user_mastery(user, question_with_skills, is_correct=False)
        weak = services.get_weak_skills(user, min_attempts=3)
        assert len(weak) >= 1
        # Mastery low (lots of incorrect)
        assert all(p.mastery < 0.5 for p in weak)

    def test_user_summary_structure(self, db, user, question_with_skills):
        for _ in range(5):
            services.update_user_mastery(user, question_with_skills, is_correct=True)
        summary = services.get_user_summary(user)
        assert 'weak' in summary
        assert 'strong' in summary
        assert 'stats' in summary
        assert summary['stats']['total_skills_practiced'] == 2


# ─── Signal: UserAnswer post_save → mastery update ───────────────────────────


@pytest.mark.integration
class TestMasterySignal:
    def test_useranswer_triggers_mastery_update(
        self, db, org, user, member, question_with_skills, question_version
    ):
        mock = _make_published_mock(org, user, question_version)
        with tenant_context(org):
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=question_version,
                selected={'id': 1},
                is_correct=True,
            )

        with unscoped_context():
            profiles = UserSkillProfile.objects.filter(user=user)
            assert profiles.count() == 2
            for p in profiles:
                assert p.alpha == 2  # initial 1 + correct

    def test_useranswer_with_none_is_correct_skips(
        self, db, org, user, member, question_with_skills, question_version
    ):
        mock = _make_published_mock(org, user, question_version)
        with tenant_context(org):
            attempt = ExamAttempt.objects.create(organization=org, exam=mock, user=user)
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=question_version,
                selected={'id': 1},
                is_correct=None,  # not yet graded
            )

        with unscoped_context():
            assert UserSkillProfile.objects.filter(user=user).count() == 0


# ─── AI Tutor (mock LLM) ──────────────────────────────────────────────────────


@pytest.mark.integration
class TestAITutorAPI:
    def test_generate_creates_pending_then_eager_completes(
        self, db, user, member, question_with_skills, monkeypatch
    ):
        # Mock LLM
        monkeypatch.setattr(tasks, '_generate_text', lambda prompt, **kw: 'Davom et!')
        # Practice'da answer (mastery profile yaratish)
        services.update_user_mastery(user, question_with_skills, is_correct=True)

        client = Client()
        client.force_login(user)
        resp = client.post(reverse('intelligence:tutor-generate'))
        assert resp.status_code in (200, 202)
        data = resp.json()
        assert data['status'] in ('pending', 'ready')
        # Eager mode → ready
        if data['status'] == 'ready':
            assert 'Davom et!' in data['content']

    def test_cache_hit_returns_existing_feedback(
        self, db, user, member, question_with_skills, monkeypatch
    ):
        call_count = {'n': 0}

        def fake_llm(prompt, **kw):
            call_count['n'] += 1
            return 'cached!'

        monkeypatch.setattr(tasks, '_generate_text', fake_llm)
        services.update_user_mastery(user, question_with_skills, is_correct=True)

        client = Client()
        client.force_login(user)
        resp1 = client.post(reverse('intelligence:tutor-generate'))
        resp2 = client.post(reverse('intelligence:tutor-generate'))
        # Cache hit — LLM faqat bir marta chaqirilgan
        assert call_count['n'] == 1
        # Ikkala javob ham bir xil ID
        assert resp1.json()['id'] == resp2.json()['id']

    def test_latest_endpoint(self, db, user, member, question_with_skills, monkeypatch):
        monkeypatch.setattr(tasks, '_generate_text', lambda p, **kw: 'X')
        services.update_user_mastery(user, question_with_skills, is_correct=True)

        client = Client()
        client.force_login(user)
        client.post(reverse('intelligence:tutor-generate'))
        resp = client.get(reverse('intelligence:tutor-latest'))
        assert resp.status_code == 200
        assert resp.json()['content'] == 'X'

    def test_latest_404_when_no_feedback(self, db, user, member):
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('intelligence:tutor-latest'))
        assert resp.status_code == 404


# ─── Open-Ended (mock LLM) ────────────────────────────────────────────────────


@pytest.mark.integration
class TestOpenEndedAPI:
    def test_submit_essay_triggers_evaluation(
        self, db, user, member, question_version, monkeypatch
    ):
        monkeypatch.setattr(
            tasks,
            '_generate_text',
            lambda p, **kw: json.dumps(
                {
                    'grammar': 80,
                    'content': 70,
                    'structure': 75,
                    'relevance': 85,
                    'rationale': 'Yaxshi javob',
                }
            ),
        )

        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('intelligence:openended-submit'),
            data={
                'question_version': str(question_version.id),
                'submission_type': 'essay',
                'content': 'Mening inshoyim juda muhim mavzu haqida.',
            },
            content_type='application/json',
        )
        assert resp.status_code == 202
        body = resp.json()
        # Eager Celery → AI_REVIEWED
        assert body['status'] == 'ai_reviewed'
        assert float(body['ai_score']) == pytest.approx((80 + 70 + 75 + 85) / 4, rel=1e-3)
        assert 'Yaxshi javob' in body['ai_feedback']['rationale']

    def test_qa_approve_by_staff(self, db, user, member, question_version, superuser, monkeypatch):
        monkeypatch.setattr(
            tasks,
            '_generate_text',
            lambda p, **kw: (
                '{"grammar":50,"content":50,"structure":50,"relevance":50,"rationale":"x"}'
            ),
        )
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('intelligence:openended-submit'),
            data={
                'question_version': str(question_version.id),
                'submission_type': 'essay',
                'content': 'Test essay',
            },
            content_type='application/json',
        )
        sid = resp.json()['id']

        # Staff QA
        admin_client = Client()
        admin_client.force_login(superuser)
        qa_resp = admin_client.post(
            reverse('intelligence:openended-qa', args=[sid]),
            data={'action': 'approve', 'score': 75, 'notes': 'OK'},
            content_type='application/json',
        )
        assert qa_resp.status_code == 200
        assert qa_resp.json()['status'] == 'human_approved'
        assert float(qa_resp.json()['human_score']) == 75.0

    def test_qa_non_staff_forbidden(self, db, user, user2, member, question_version, monkeypatch):
        monkeypatch.setattr(
            tasks,
            '_generate_text',
            lambda p, **kw: (
                '{"grammar":50,"content":50,"structure":50,"relevance":50,"rationale":"x"}'
            ),
        )
        client = Client()
        client.force_login(user)
        resp = client.post(
            reverse('intelligence:openended-submit'),
            data={
                'question_version': str(question_version.id),
                'submission_type': 'essay',
                'content': 'x',
            },
            content_type='application/json',
        )
        sid = resp.json()['id']

        # User2 staff emas
        client2 = Client()
        client2.force_login(user2)
        qa = client2.post(
            reverse('intelligence:openended-qa', args=[sid]),
            data={'action': 'approve'},
            content_type='application/json',
        )
        assert qa.status_code == 403

    def test_submit_validation(self, db, user, member, question_version):
        client = Client()
        client.force_login(user)
        # Empty essay
        resp = client.post(
            reverse('intelligence:openended-submit'),
            data={
                'question_version': str(question_version.id),
                'submission_type': 'essay',
                'content': '   ',
            },
            content_type='application/json',
        )
        assert resp.status_code == 400


# ─── REST: skills endpoints ──────────────────────────────────────────────────


@pytest.mark.integration
class TestSkillsAPI:
    def test_mastery_endpoint(self, db, user, member, question_with_skills):
        services.update_user_mastery(user, question_with_skills, is_correct=True)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse('intelligence:skills-mastery'))
        assert resp.status_code == 200
        data = resp.json()
        assert 'weak' in data and 'strong' in data and 'stats' in data
        assert data['stats']['total_skills_practiced'] == 2

    def test_recommendations_returns_questions(
        self, db, user, member, question_with_skills, question_version
    ):
        # Make user weak in this skill (4 incorrect attempts)
        for _ in range(4):
            services.update_user_mastery(user, question_with_skills, is_correct=False)

        client = Client()
        client.force_login(user)
        resp = client.get(reverse('intelligence:skills-recommendations'), {'limit': 5})
        assert resp.status_code == 200
        # Question_with_skills tavsiya etilishi kerak (zaif skill bo'yicha)
        body = resp.json()
        question_ids = {q['id'] for q in body['questions']}
        assert str(question_with_skills.id) in question_ids
