"""Shared builders for the engagement feature tests."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import ExamAttempt, MockExam, MockExamQuestion, UserAnswer
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context

User = get_user_model()


def member(org, username, role_name, **extra):
    u = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='pass123', **extra
    )
    role = OrgRole.objects.get(organization=org, name=role_name)
    m = Membership.objects.create(
        user=u, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return u


def client(user):
    c = Client()
    c.force_login(user)
    return c


def make_versions(org, subject, n, qtype=Question.Type.SINGLE_CHOICE):
    """n single-choice question versions in `org`; option 1 is the correct one."""
    out = []
    with tenant_context(org):
        for _ in range(n):
            q = Question.objects.create(organization=org, subject=subject, type=qtype)
            out.append(
                QuestionVersion.objects.create(
                    question=q,
                    version_number=1,
                    content={'text': 'q'},
                    options=[
                        {'id': 1, 'text': 'A', 'is_correct': True},
                        {'id': 2, 'text': 'B', 'is_correct': False},
                    ],
                )
            )
    return out


def make_exam(org, creator, qvs=(), *, minutes=60, title='Exam'):
    now = timezone.now()
    with tenant_context(org):
        mock = MockExam.objects.create(
            organization=org,
            title=title,
            duration_minutes=minutes,
            scheduled_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            created_by=creator,
        )
        for i, qv in enumerate(qvs, start=1):
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i)
    return mock


def make_attempt(org, mock, user, *, status=ExamAttempt.Status.IN_PROGRESS, score=None, **extra):
    with tenant_context(org):
        return ExamAttempt.objects.create(
            organization=org,
            exam=mock,
            user=user,
            status=status,
            score=score,
            submitted_at=timezone.now() if status == ExamAttempt.Status.SUBMITTED else None,
            **extra,
        )


def answer(org, attempt, qv, correct=True):
    with tenant_context(org):
        return UserAnswer.objects.create(
            organization=org,
            attempt=attempt,
            question_version=qv,
            selected={'id': 1 if correct else 2},
            is_correct=correct,
        )
