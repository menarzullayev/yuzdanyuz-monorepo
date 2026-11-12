"""Seed data for loadtest/k6/core-flows.js (audit F20). Never run against production.

Creates, in the platform organization:
  - N students  lt-user-0001 … (password from LOADTEST_PASSWORD), each with the
    active STUDENT membership every B2C user gets;
  - one subject with Q single-choice questions;
  - M published mock exams (open now) that each pin all Q questions.
A user can attempt each mock once, so VU i walks mocks 1..M as user i.

Run inside the backend container (loadtest/compose.yml mounts this directory):
  python /loadtest/seed_loadtest.py --users 400 --mocks 40 --questions 10
"""

import argparse
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.prod')

import django  # noqa: E402

django.setup()

from django.contrib.auth.hashers import make_password  # noqa: E402
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.models import CustomUser  # noqa: E402
from apps.catalog.models import Question, QuestionVersion, Subject  # noqa: E402
from apps.exams.models import MockExam, MockExamQuestion  # noqa: E402
from apps.organizations.services import ensure_platform_membership, get_platform_org  # noqa: E402
from core.tenant import tenant_context  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--users', type=int, default=400)
    parser.add_argument('--mocks', type=int, default=40)
    parser.add_argument('--questions', type=int, default=10)
    args = parser.parse_args()
    password = os.environ['LOADTEST_PASSWORD']

    org = get_platform_org()
    admin, _ = CustomUser.objects.get_or_create(username='lt-author', defaults={'is_active': True})
    pw_hash = make_password(password)  # one hash for all: hashing 400x is minutes

    with transaction.atomic():
        existing = set(
            CustomUser.objects.filter(username__startswith='lt-user-').values_list(
                'username', flat=True
            )
        )
        new = [
            CustomUser(username=f'lt-user-{i:04d}', password=pw_hash, is_active=True)
            for i in range(1, args.users + 1)
            if f'lt-user-{i:04d}' not in existing
        ]
        CustomUser.objects.bulk_create(new)
        for user in CustomUser.objects.filter(username__startswith='lt-user-'):
            ensure_platform_membership(user)

    with tenant_context(org), transaction.atomic():
        subject, _ = Subject.objects.get_or_create(
            slug='loadtest', defaults={'name': 'Load test', 'organization': org}
        )
        versions = []
        for n in range(1, args.questions + 1):
            text = f'Load test question {n}: {n} + {n} = ?'
            qv = QuestionVersion.objects.filter(
                question__organization=org, content__text=text
            ).first()
            if qv is None:
                q = Question.objects.create(
                    organization=org,
                    subject=subject,
                    type=Question.Type.SINGLE_CHOICE,
                    initial_difficulty='easy',
                )
                qv = QuestionVersion.objects.create(
                    question=q,
                    version_number=1,
                    content={'text': text},
                    options=[
                        {'id': 1, 'text': str(2 * n), 'is_correct': True},
                        {'id': 2, 'text': str(2 * n + 1), 'is_correct': False},
                        {'id': 3, 'text': str(2 * n - 1), 'is_correct': False},
                        {'id': 4, 'text': str(n), 'is_correct': False},
                    ],
                    created_by=admin,
                )
            versions.append(qv)

        now = timezone.now()
        mock_ids = []
        for m in range(1, args.mocks + 1):
            mock, _ = MockExam.objects.get_or_create(
                organization=org,
                title=f'Load test mock {m:03d}',
                defaults={
                    'description': 'k6 load test',
                    'duration_minutes': 60,
                    'scheduled_at': now - timedelta(hours=1),
                    'closes_at': now + timedelta(days=2),
                    'is_public': False,
                    'status': MockExam.Status.PUBLISHED,
                    'created_by': admin,
                },
            )
            for order, qv in enumerate(versions, start=1):
                MockExamQuestion.objects.get_or_create(
                    mock_exam=mock, question_version=qv, defaults={'order': order, 'points': 1}
                )
            mock_ids.append(str(mock.id))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'mocks.json'), 'w', encoding='utf-8') as fh:
        json.dump(mock_ids, fh)

    print(
        f'seeded org={org.slug} users={CustomUser.objects.filter(username__startswith="lt-user-").count()} '
        f'mocks={args.mocks} questions={len(versions)}'
    )


if __name__ == '__main__':
    main()
