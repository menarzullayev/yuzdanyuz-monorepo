"""Seed data for the browser end-to-end tests (packages/frontend-web/e2e).

Creates (idempotently) a B2C student with a complete onboarding profile and two fresh
public mocks on the platform organization: a 30-minute one and a 1-minute one for the
time-limit test. Prints the mock ids as JSON for the test runner.

Refuses to run unless DEBUG is on: it sets a known password, so it must never touch a
production database.

    E2E_PASSWORD=... python manage.py e2e_seed
"""

import datetime
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

USERNAME = 'e2e.student'
QUESTIONS = [
    ('SC', '2 + 3 = ?', [('4', False), ('5', True), ('6', False)]),
    ('SC', 'Oʻzbekiston poytaxti?', [('Samarqand', False), ('Toshkent', True), ('Buxoro', False)]),
    ('MC', 'Tub sonlarni belgilang', [('2', True), ('4', False), ('7', True), ('9', False)]),
    ('SC', '10 / 2 = ?', [('2', False), ('5', True)]),
]


class Command(BaseCommand):
    help = 'Seed a student and two mocks for the browser E2E tests (DEBUG only).'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('e2e_seed only runs with DEBUG=True (it sets a known password).')
        password = os.environ.get('E2E_PASSWORD', '')
        if len(password) < 12:
            raise CommandError('Set E2E_PASSWORD (12+ characters).')

        from apps.accounts.models import (
            CustomUser,
            District,
            ExamGoal,
            OnboardingProfile,
            Region,
        )
        from apps.catalog.models import Question, QuestionVersion, Subject
        from apps.exams.models import MockExam, MockExamQuestion
        from apps.organizations.services import ensure_platform_membership, get_platform_org
        from core.tenant import tenant_context, unscoped_context

        with unscoped_context():
            user, _ = CustomUser.objects.get_or_create(
                username=USERNAME, defaults={'first_name': 'Sinov'}
            )
            if not user.check_password(password):
                user.set_password(password)
                user.save()
            ensure_platform_membership(user)

            goal, _ = ExamGoal.objects.get_or_create(code='dtm', defaults={'name_uz': 'DTM'})
            region, _ = Region.objects.get_or_create(
                code=99,
                defaults={'name_uz': 'Sinov', 'name_ru': 'T', 'name_en': 'T', 'slug': 'e2e'},
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
            user.region, user.district = region, district
            user.save(update_fields=['region', 'district'])

            org = get_platform_org()
            subject, _ = Subject.objects.get_or_create(
                slug='e2e-matematika', defaults={'name': 'Matematika'}
            )

        mocks = {}
        now = timezone.now()
        stamp = now.strftime('%H%M%S%f')
        with tenant_context(org):
            for key, minutes in (('long', 30), ('short', 1)):
                mock = MockExam.objects.create(
                    organization=org,
                    title=f'E2E {key} {stamp}',
                    description='Brauzer sinovi',
                    duration_minutes=minutes,
                    scheduled_at=now - datetime.timedelta(minutes=5),
                    closes_at=now + datetime.timedelta(days=1),
                    status=MockExam.Status.PUBLISHED,
                    is_public=True,
                    created_by=user,
                )
                for order, (qtype, text, options) in enumerate(QUESTIONS, start=1):
                    q = Question.objects.create(organization=org, subject=subject, type=qtype)
                    qv = QuestionVersion.objects.create(
                        question=q,
                        version_number=1,
                        content={'text': text},
                        options=[
                            {'id': n, 'text': t, 'is_correct': ok}
                            for n, (t, ok) in enumerate(options, start=1)
                        ],
                    )
                    MockExamQuestion.objects.create(
                        mock_exam=mock, question_version=qv, order=order, points=1
                    )
                mocks[key] = str(mock.id)
        self.stdout.write(json.dumps({'username': USERNAME, 'mocks': mocks}))
