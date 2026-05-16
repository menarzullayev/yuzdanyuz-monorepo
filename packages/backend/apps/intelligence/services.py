"""
Intelligence domain services — Knowledge Graph (recommendations + summaries).

Pure DB operations + bayesian math. LLM va Celery — alohida modullarda.
"""

from django.db.models import Avg

from apps.catalog.models import Question

from . import bayesian
from .models import UserSkillProfile


def update_user_mastery(user, question, is_correct: bool) -> list[UserSkillProfile]:
    """
    Bir question'ga javob bo'lganida, uning barcha skill'lari uchun
    UserSkillProfile'ni yangilash. Atomic per-skill (savollar ko'p
    bo'lmaganda DB lock muhim emas).
    """
    skills = list(question.skills.all())
    profiles = []
    for skill in skills:
        profile, _created = UserSkillProfile.objects.get_or_create(
            user=user, skill=skill, defaults={'alpha': 1, 'beta': 1}
        )
        new_alpha, new_beta = bayesian.update(profile.alpha, profile.beta, is_correct)
        profile.alpha = new_alpha
        profile.beta = new_beta
        profile.mastery = bayesian.mastery(new_alpha, new_beta)
        profile.confidence = bayesian.confidence(new_alpha, new_beta)
        profile.save(update_fields=['alpha', 'beta', 'mastery', 'confidence', 'last_updated_at'])
        profiles.append(profile)
    return profiles


def _profiles_with_min_attempts(user, min_attempts: int):
    """alpha+beta-2 = total attempts. Raw extra filter (DB-level)."""
    return (
        UserSkillProfile.objects.filter(user=user)
        .extra(where=['(alpha + beta - 2) >= %s'], params=[min_attempts])
        .select_related('skill')
    )


def get_weak_skills(user, *, limit: int = 10, min_attempts: int = 3) -> list[UserSkillProfile]:
    """
    Eng zaif skill'lar (mastery past). min_attempts noisy 1/2 holatlarni filterlaydi.
    """
    return list(_profiles_with_min_attempts(user, min_attempts).order_by('mastery')[:limit])


def get_strong_skills(user, *, limit: int = 10, min_attempts: int = 3) -> list[UserSkillProfile]:
    """Eng kuchli skill'lar (motivatsiya uchun)."""
    return list(_profiles_with_min_attempts(user, min_attempts).order_by('-mastery')[:limit])


def get_user_summary(user) -> dict:
    """
    AI Tutor uchun deterministik summary. LLM faqat shu xulosani matnga aylantiradi.
    """
    weak = get_weak_skills(user, limit=5)
    strong = get_strong_skills(user, limit=5)
    qs = UserSkillProfile.objects.filter(user=user)
    total_skills = qs.count()
    avg = qs.aggregate(avg=Avg('mastery'))['avg'] or 0.0

    return {
        'weak': [
            {
                'skill_id': str(p.skill_id),
                'skill_name': p.skill.name,
                'mastery': round(p.mastery, 3),
                'confidence': round(p.confidence, 3),
            }
            for p in weak
        ],
        'strong': [
            {
                'skill_id': str(p.skill_id),
                'skill_name': p.skill.name,
                'mastery': round(p.mastery, 3),
            }
            for p in strong
        ],
        'stats': {
            'total_skills_practiced': total_skills,
            'avg_mastery': round(avg, 3),
        },
    }


def recommend_questions(user, *, limit: int = 10) -> list[Question]:
    """
    Personalized study plan: zaif skill'lardan random savollar.
    Mock yoki Practice'da ishlatish uchun.
    """
    weak = get_weak_skills(user, limit=10)
    if not weak:
        # Yangi user — random questions (har subject'dan)
        return list(Question.global_objects.order_by('?')[:limit])

    weak_skill_ids = [p.skill_id for p in weak]
    return list(
        Question.global_objects.filter(skills__id__in=weak_skill_ids)
        .distinct()
        .order_by('?')[:limit]
    )
