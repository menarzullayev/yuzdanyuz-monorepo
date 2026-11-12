"""Test helper: give a user a complete onboarding profile (required before seeing results)."""

import datetime

from apps.accounts.models import District, ExamGoal, OnboardingProfile, Region


def complete_onboarding(user):
    goal, _ = ExamGoal.objects.get_or_create(code='ielts', defaults={'name_uz': 'IELTS'})
    region, _ = Region.objects.get_or_create(
        code=1, defaults={'name_uz': 'T', 'name_ru': 'T', 'name_en': 'T', 'slug': 'tosh'}
    )
    district, _ = District.objects.get_or_create(
        region=region, code=1, defaults={'name_uz': 'Y', 'name_ru': 'Y', 'name_en': 'Y'}
    )
    profile, _ = OnboardingProfile.objects.get_or_create(user=user)
    profile.goals.add(goal)
    profile.primary_goal = goal
    profile.test_language = 'uz'
    profile.target_grade = 'B'
    profile.graduation_year = 2026
    profile.birth_date = datetime.date(2008, 1, 1)
    profile.save()
    user.region = region
    user.district = district
    user.save(update_fields=['region', 'district'])
    return user
