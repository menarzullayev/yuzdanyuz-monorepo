"""
Unit tests for `apps.intelligence.services` — Knowledge Graph mastery service.

Scope:
  - update_user_mastery: profile create + Bayesian update + per-skill semantics
  - get_weak_skills / get_strong_skills: ordering + min_attempts filter
  - get_user_summary: structure + rounding + empty-state stats
  - recommend_questions: weak-skill targeting + cold-start fallback + limit

Integration coverage (signal flow, REST, LLM) lives in
`tests/integration/test_intelligence.py`. This file focuses on pure service
behavior with a single DB dependency.
"""

import pytest

from apps.catalog.models import Question
from apps.intelligence import services
from apps.intelligence.models import SkillTag, UserSkillProfile
from core.tenant import tenant_context

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def skill_a(db):
    return SkillTag.objects.create(name='Algebra', slug='alg')


@pytest.fixture
def skill_b(db):
    return SkillTag.objects.create(name='Geometriya', slug='geom')


@pytest.fixture
def skill_c(db):
    return SkillTag.objects.create(name='Trigonometriya', slug='trig')


@pytest.fixture
def question_two_skills(db, org, subject, skill_a, skill_b):
    """Savol Algebra + Geometriya skill'lariga bog'langan."""
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q.skills.add(skill_a, skill_b)
        return q


@pytest.fixture
def question_one_skill(db, org, subject, skill_c):
    """Boshqa savol — faqat Trigonometriya skill'i bilan."""
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )
        q.skills.add(skill_c)
        return q


@pytest.fixture
def question_no_skills(db, org, subject):
    """Savol skill'siz — update_user_mastery hech narsa qilmasligi kerak."""
    with tenant_context(org):
        return Question.objects.create(
            organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
        )


# ─── update_user_mastery ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestUpdateUserMastery:
    def test_returns_one_profile_per_skill(self, db, user, question_two_skills):
        profiles = services.update_user_mastery(user, question_two_skills, is_correct=True)
        assert len(profiles) == 2
        # Har profile UserSkillProfile instance
        assert all(isinstance(p, UserSkillProfile) for p in profiles)

    def test_correct_bumps_alpha_only(self, db, user, question_two_skills):
        services.update_user_mastery(user, question_two_skills, is_correct=True)
        for p in UserSkillProfile.objects.filter(user=user):
            assert p.alpha == 2
            assert p.beta == 1
            assert p.mastery == pytest.approx(2 / 3, rel=1e-3)

    def test_incorrect_bumps_beta_only(self, db, user, question_two_skills):
        services.update_user_mastery(user, question_two_skills, is_correct=False)
        for p in UserSkillProfile.objects.filter(user=user):
            assert p.alpha == 1
            assert p.beta == 2
            assert p.mastery == pytest.approx(1 / 3, rel=1e-3)

    def test_no_skills_creates_no_profiles(self, db, user, question_no_skills):
        result = services.update_user_mastery(user, question_no_skills, is_correct=True)
        assert result == []
        assert UserSkillProfile.objects.filter(user=user).count() == 0

    def test_repeated_updates_accumulate(self, db, user, question_two_skills):
        for _ in range(3):
            services.update_user_mastery(user, question_two_skills, is_correct=True)
        services.update_user_mastery(user, question_two_skills, is_correct=False)
        # 3 correct + 1 incorrect → alpha=4, beta=2 (per skill)
        p = UserSkillProfile.objects.filter(user=user).first()
        assert p.alpha == 4
        assert p.beta == 2
        # confidence dolzarblanadi (last_updated_at o'zgaradi)
        assert p.confidence > 0


# ─── get_weak_skills / get_strong_skills ──────────────────────────────────────


@pytest.mark.unit
class TestWeakStrongSkills:
    def test_min_attempts_filters_low_sample_profiles(self, db, user, question_two_skills):
        # 2 attempts (alpha+beta-2 = 2) — min_attempts=3 dan past
        services.update_user_mastery(user, question_two_skills, is_correct=False)
        services.update_user_mastery(user, question_two_skills, is_correct=False)
        assert services.get_weak_skills(user, min_attempts=3) == []
        # min_attempts=2 ga ko'tarsak — kiradi
        assert len(services.get_weak_skills(user, min_attempts=2)) == 2

    def test_weak_sorted_ascending_by_mastery(
        self, db, user, question_two_skills, question_one_skill
    ):
        # Skill A, B: 5 incorrect → mastery past
        for _ in range(5):
            services.update_user_mastery(user, question_two_skills, is_correct=False)
        # Skill C: 5 correct → mastery yuqori
        for _ in range(5):
            services.update_user_mastery(user, question_one_skill, is_correct=True)

        weak = services.get_weak_skills(user, limit=10, min_attempts=3)
        assert len(weak) == 3
        # Birinchi (eng zaif) mastery eng past bo'lishi kerak
        masteries = [p.mastery for p in weak]
        assert masteries == sorted(masteries)

    def test_strong_sorted_descending_by_mastery(
        self, db, user, question_two_skills, question_one_skill
    ):
        for _ in range(5):
            services.update_user_mastery(user, question_two_skills, is_correct=False)
        for _ in range(5):
            services.update_user_mastery(user, question_one_skill, is_correct=True)

        strong = services.get_strong_skills(user, limit=10, min_attempts=3)
        masteries = [p.mastery for p in strong]
        assert masteries == sorted(masteries, reverse=True)

    def test_limit_honored(self, db, user, question_two_skills, question_one_skill):
        for _ in range(4):
            services.update_user_mastery(user, question_two_skills, is_correct=False)
            services.update_user_mastery(user, question_one_skill, is_correct=False)
        # 3 ta skill total, lekin limit=1
        result = services.get_weak_skills(user, limit=1, min_attempts=3)
        assert len(result) == 1


# ─── get_user_summary ────────────────────────────────────────────────────────


@pytest.mark.unit
class TestGetUserSummary:
    def test_empty_user_returns_zero_stats(self, db, user):
        summary = services.get_user_summary(user)
        assert summary['weak'] == []
        assert summary['strong'] == []
        assert summary['stats']['total_skills_practiced'] == 0
        assert summary['stats']['avg_mastery'] == 0.0

    def test_summary_structure_and_rounding(self, db, user, question_two_skills):
        for _ in range(5):
            services.update_user_mastery(user, question_two_skills, is_correct=True)
        summary = services.get_user_summary(user)

        assert set(summary.keys()) == {'weak', 'strong', 'stats'}
        assert summary['stats']['total_skills_practiced'] == 2
        # 3 decimal rounding
        for p in summary['strong']:
            assert isinstance(p['mastery'], float)
            # round(x, 3) — eng ko'pi 3 decimal joy
            assert round(p['mastery'], 3) == p['mastery']
            assert {'skill_id', 'skill_name', 'mastery'} <= set(p.keys())
        for p in summary['weak']:
            assert {'skill_id', 'skill_name', 'mastery', 'confidence'} <= set(p.keys())

    def test_avg_mastery_reflects_population(self, db, user, question_two_skills):
        # 6 correct, 0 incorrect → mastery ≈ 7/8 = 0.875 per skill
        for _ in range(6):
            services.update_user_mastery(user, question_two_skills, is_correct=True)
        summary = services.get_user_summary(user)
        # avg ikkala skill ham bir xil mastery → avg ham shu
        assert summary['stats']['avg_mastery'] == pytest.approx(7 / 8, abs=1e-3)


# ─── recommend_questions ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestRecommendQuestions:
    """ISSUE-109 C1 fix: recommend_questions tenant context'da `Question.objects` ishlatadi.
    Test'lar `with tenant_context(org):` ichida bajariladi — fixture savollarining org'i.
    """

    def test_cold_start_returns_random_questions(
        self, db, user, org, question_one_skill, question_two_skills
    ):
        # User'ning mastery profile'i yo'q → cold-start path
        with tenant_context(org):
            result = services.recommend_questions(user, limit=10)
        assert len(result) >= 1
        assert all(isinstance(q, Question) for q in result)

    def test_weak_skill_targeting(self, db, user, org, question_one_skill, question_two_skills):
        # Skill C (question_one_skill) zaif qilamiz, A/B emas
        for _ in range(5):
            services.update_user_mastery(user, question_one_skill, is_correct=False)
        # Skill A/B ni kuchli qilamiz
        for _ in range(5):
            services.update_user_mastery(user, question_two_skills, is_correct=True)

        with tenant_context(org):
            result = services.recommend_questions(user, limit=10)
        ids = {q.id for q in result}
        # Zaif skill'ga bog'langan savol tavsiya etiladi
        assert question_one_skill.id in ids

    def test_limit_caps_results(self, db, user, org, question_two_skills):
        # Mastery profile yarat (cold-start path emas)
        for _ in range(5):
            services.update_user_mastery(user, question_two_skills, is_correct=False)
        with tenant_context(org):
            result = services.recommend_questions(user, limit=1)
        assert len(result) <= 1
