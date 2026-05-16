"""
E2E test uchun sample data — Organization + Subject + Topic + 5 ta Question
+ Mock Exam. Idempotent (qayta ishga tushirilishga xavfsiz).

Foydalanish:
    cd packages/backend
    DJANGO_SETTINGS_MODULE=core.settings.dev venv/bin/python scripts/seed_e2e.py
"""

import os
import sys
from pathlib import Path

# scripts/ ichidan ishga tushirilsa, backend root'ni sys.path'ga qo'shish
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from datetime import timedelta

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings.dev')
django.setup()

from django.utils import timezone

from apps.accounts.models import CustomUser
from apps.catalog.models import Question, QuestionVersion, Subject, Topic
from apps.exams.models import MockExam, MockExamQuestion
from apps.organizations.models import Membership, Organization, OrgRole
from core.tenant import tenant_context

# ── 1. Organization ─────────────────────────────────────────────────────────
org, created = Organization.objects.get_or_create(
    slug='demo-maktab',
    defaults={
        'name': 'Demo Maktab #1',
        'org_type': 'school',
        'status': 'active',
        'tier': 'free',
    },
)
print(f'{"+" if created else "="} Org: {org.name} ({org.id})')

# ── 2. Superuser membership (narzullayevme owner) ──────────────────────────
admin = CustomUser.objects.filter(is_superuser=True).first()
if admin:
    owner_role, _ = OrgRole.objects.get_or_create(
        organization=org,
        name='owner',
        defaults={'permissions': {'all': True}},
    )
    mem, mem_created = Membership.objects.get_or_create(
        user=admin,
        organization=org,
        defaults={'role': owner_role, 'status': 'active'},
    )
    print(f'{"+" if mem_created else "="} Membership: {admin.username} → owner@{org.slug}')

# ── 3. Subject + Topic ──────────────────────────────────────────────────────
subj, created = Subject.objects.get_or_create(
    slug='matematika',
    defaults={'name': 'Matematika', 'organization': org},
)
print(f'{"+" if created else "="} Subject: {subj.name}')

topic, created = Topic.objects.get_or_create(
    subject=subj,
    name='Algebra',
    defaults={'order': 1},
)
print(f'{"+" if created else "="} Topic: {topic.name}')

# ── 4. 5 ta Question (single choice) ────────────────────────────────────────
QUESTIONS = [
    {
        'text': '2 + 2 = ?',
        'options': [
            {'id': 1, 'text': '3', 'is_correct': False},
            {'id': 2, 'text': '4', 'is_correct': True},
            {'id': 3, 'text': '5', 'is_correct': False},
            {'id': 4, 'text': '22', 'is_correct': False},
        ],
    },
    {
        'text': '√16 = ?',
        'options': [
            {'id': 1, 'text': '2', 'is_correct': False},
            {'id': 2, 'text': '4', 'is_correct': True},
            {'id': 3, 'text': '8', 'is_correct': False},
            {'id': 4, 'text': '16', 'is_correct': False},
        ],
    },
    {
        'text': '7 × 8 = ?',
        'options': [
            {'id': 1, 'text': '54', 'is_correct': False},
            {'id': 2, 'text': '56', 'is_correct': True},
            {'id': 3, 'text': '64', 'is_correct': False},
            {'id': 4, 'text': '78', 'is_correct': False},
        ],
    },
    {
        'text': '100 / 4 = ?',
        'options': [
            {'id': 1, 'text': '20', 'is_correct': False},
            {'id': 2, 'text': '25', 'is_correct': True},
            {'id': 3, 'text': '40', 'is_correct': False},
            {'id': 4, 'text': '50', 'is_correct': False},
        ],
    },
    {
        'text': 'x² = 49 tenglamaning musbat ildizi qancha?',
        'options': [
            {'id': 1, 'text': '5', 'is_correct': False},
            {'id': 2, 'text': '6', 'is_correct': False},
            {'id': 3, 'text': '7', 'is_correct': True},
            {'id': 4, 'text': '9', 'is_correct': False},
        ],
    },
]

with tenant_context(org):
    versions = []
    for idx, q_data in enumerate(QUESTIONS, start=1):
        # idempotent: yaratilgan bo'lsa shu QV'ni topish
        existing = Question.objects.filter(
            organization=org,
            subject=subj,
            topic=topic,
            versions__content__text=q_data['text'],
        ).first()
        if existing:
            qv = existing.versions.first()
            print(f'= Question #{idx}: {q_data["text"][:30]}... (mavjud)')
        else:
            q = Question.objects.create(
                organization=org,
                subject=subj,
                topic=topic,
                type=Question.Type.SINGLE_CHOICE,
                initial_difficulty='easy',
            )
            qv = QuestionVersion.objects.create(
                question=q,
                version_number=1,
                content={'text': q_data['text']},
                options=q_data['options'],
                created_by=admin,
            )
            print(f'+ Question #{idx}: {q_data["text"][:30]}...')
        versions.append(qv)

    # ── 5. MockExam — bugundan boshlanadi, 30 daqiqa, 1 hafta yopiq ─────────
    now = timezone.now()
    mock, created = MockExam.objects.get_or_create(
        organization=org,
        title='Demo Test #1 — Matematika',
        defaults={
            'description': 'E2E test uchun sample mock exam (5 savol, 30 daqiqa)',
            'duration_minutes': 30,
            'scheduled_at': now - timedelta(hours=1),  # allaqachon ochiq
            'closes_at': now + timedelta(days=7),
            'is_public': True,
            'status': MockExam.Status.PUBLISHED,
            'created_by': admin,
        },
    )
    print(f'{"+" if created else "="} MockExam: {mock.title}')

    # Pin questions to mock
    for idx, qv in enumerate(versions, start=1):
        link, link_created = MockExamQuestion.objects.get_or_create(
            mock_exam=mock,
            question_version=qv,
            defaults={'order': idx, 'points': 10},
        )
        if link_created:
            print(f'  + Q{idx} pinned')

print()
print('═══ Hammasi tayyor ═══')
print(f'Org slug:       {org.slug}')
print(f'Org ID:         {org.id}')
print(f'Subject:        {subj.slug}')
print(f'Topic:          {topic.name}')
print(f'Mock ID:        {mock.id}')
print(f'Question count: {Question.objects.filter(organization=org).count()}')
print(f'Admin user:     {admin.username if admin else "—"}')
