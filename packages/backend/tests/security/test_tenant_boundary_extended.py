"""ISSUE-111 — Extended tenant boundary tests.

`test_tenant_boundary.py` faqat 5 ta test (Question + permission scoping). Audit
12+ ta tenant model qoplanmaganini topgan. Bu fayl regression cover qiladi:

  - QuestionBank, MockExam (+ is_public), ExamAttempt, WebhookEndpoint,
    OrganizationSubscription, Subject (global) cross-org isolation
  - C1 regression: SkillRecommendations cross-tenant leak yo'q (ISSUE-109)
  - W3 regression: Membership/OrgInvite clean() cross-org role validation (ISSUE-110)
"""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.catalog.models import Question, QuestionBank, Subject
from apps.commerce.models import (
    OrganizationSubscription,
    SubscriptionPlan,
)
from apps.exams.models import ExamAttempt, MockExam
from apps.intelligence import services as intelligence_services
from apps.intelligence.models import SkillTag, UserSkillProfile
from apps.organizations.models import (
    Membership,
    MembershipStatus,
    OrgInvite,
    OrgRole,
)
from apps.webhooks.models import WebhookEndpoint
from core.tenant import set_current_org, unscoped_context

pytestmark = pytest.mark.security


# ─── Cross-org isolation: tenant-scoped models ─────────────────────────────────


class TestQuestionBankBoundary:
    def test_questionbank_cross_org_blocked(self, db, org, org2):
        with unscoped_context():
            qb1 = QuestionBank.objects.create(organization=org, name='B1', slug='b1')
            qb2 = QuestionBank.objects.create(organization=org2, name='B2', slug='b2')

        set_current_org(org)
        ids = set(QuestionBank.objects.values_list('id', flat=True))
        assert qb1.id in ids
        assert qb2.id not in ids


class TestMockExamBoundary:
    def test_mockexam_cross_org_blocked(self, db, org, org2, user):
        with unscoped_context():
            m1 = MockExam.objects.create(
                organization=org,
                title='M1',
                created_by=user,
                scheduled_at='2026-06-01T10:00:00Z',
                closes_at='2026-06-01T12:00:00Z',
                duration_minutes=60,
            )
            m2 = MockExam.objects.create(
                organization=org2,
                title='M2',
                created_by=user,
                scheduled_at='2026-06-01T10:00:00Z',
                closes_at='2026-06-01T12:00:00Z',
                duration_minutes=60,
            )

        set_current_org(org)
        ids = set(MockExam.objects.values_list('id', flat=True))
        assert m1.id in ids
        assert m2.id not in ids

    def test_mockexam_is_public_visible_to_other_orgs(self, db, org, org2, user):
        """is_public=True mock har org'ga ko'rinishi kerak (PublicOrTenantManager)."""
        with unscoped_context():
            public_mock = MockExam.objects.create(
                organization=org,
                title='Public',
                created_by=user,
                scheduled_at='2026-06-01T10:00:00Z',
                closes_at='2026-06-01T12:00:00Z',
                duration_minutes=60,
                is_public=True,
            )

        set_current_org(org2)
        ids = set(MockExam.objects.values_list('id', flat=True))
        assert public_mock.id in ids


class TestExamAttemptBoundary:
    def test_examattempt_cross_org_blocked(self, db, org, org2, user, user2):
        with unscoped_context():
            m1 = MockExam.objects.create(
                organization=org,
                title='M1',
                created_by=user,
                scheduled_at='2026-06-01T10:00:00Z',
                closes_at='2026-06-01T12:00:00Z',
                duration_minutes=60,
            )
            m2 = MockExam.objects.create(
                organization=org2,
                title='M2',
                created_by=user,
                scheduled_at='2026-06-01T10:00:00Z',
                closes_at='2026-06-01T12:00:00Z',
                duration_minutes=60,
            )
            a1 = ExamAttempt.objects.create(organization=org, exam=m1, user=user)
            a2 = ExamAttempt.objects.create(organization=org2, exam=m2, user=user2)

        set_current_org(org)
        ids = set(ExamAttempt.objects.values_list('id', flat=True))
        assert a1.id in ids
        assert a2.id not in ids


class TestWebhookEndpointBoundary:
    def test_webhookendpoint_cross_org_blocked(self, db, org, org2):
        with unscoped_context():
            w1 = WebhookEndpoint.objects.create(
                organization=org, name='W1', url='https://a.example'
            )
            w2 = WebhookEndpoint.objects.create(
                organization=org2, name='W2', url='https://b.example'
            )

        set_current_org(org)
        ids = set(WebhookEndpoint.objects.values_list('id', flat=True))
        assert w1.id in ids
        assert w2.id not in ids


class TestOrganizationSubscriptionBoundary:
    """ISSUE-110 W1: TenantManager fail-closed va explicit org filter."""

    def test_subscription_cross_org_blocked(self, db, org, org2):
        plan = SubscriptionPlan.objects.create(
            name='Pro',
            slug='pro',
            billing_period='monthly',
            pricing_model='flat',
            price_uzs=Decimal('100000'),
        )
        with unscoped_context():
            s1 = OrganizationSubscription.objects.create(
                organization=org,
                plan=plan,
                current_period_started_at='2026-05-01T00:00:00Z',
            )
            s2 = OrganizationSubscription.objects.create(
                organization=org2,
                plan=plan,
                current_period_started_at='2026-05-01T00:00:00Z',
            )

        set_current_org(org)
        ids = set(OrganizationSubscription.objects.values_list('id', flat=True))
        assert s1.id in ids
        assert s2.id not in ids


# ─── ISSUE-109 C3: Subject global, RLS yo'q ──────────────────────────────────


class TestSubjectGlobal:
    def test_subject_visible_across_orgs(self, db, org, org2, subject):
        """Subject `organization=null` (platform-global) — har org'ga ko'rinishi shart."""
        # Subject default manager `models.Manager` ishlatadi (TenantManager emas).
        # Hech qaysi tenant context filter qilmasligi kerak.
        for ctx_org in (org, org2):
            set_current_org(ctx_org)
            assert Subject.objects.filter(id=subject.id).exists()


# ─── ISSUE-109 C1 regression: recommend_questions cross-tenant leak yo'q ─────


class TestSkillRecommendationsBoundary:
    def test_recommendations_no_cross_tenant_leak(self, db, org, org2, user, subject):
        """C1 fix: tenant context faol bo'lsa, faqat o'sha org savollari ko'rinadi."""
        with unscoped_context():
            q1 = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            q2 = Question.objects.create(
                organization=org2, subject=subject, type=Question.Type.SINGLE_CHOICE
            )

        set_current_org(org)
        recs = intelligence_services.recommend_questions(user, limit=10)
        rec_ids = {q.id for q in recs}
        assert q1.id in rec_ids
        assert q2.id not in rec_ids, 'C1 regression: org2 savol cross-tenant chiqdi'

    def test_recommendations_b2c_only_public_banks(self, db, org, user, subject):
        """C1 fix: org context yo'q (B2C) → faqat is_public=True bank savollari."""
        with unscoped_context():
            qb_public = QuestionBank.objects.create(
                organization=org, name='Public Bank', slug='pub', is_public=True
            )
            qb_private = QuestionBank.objects.create(
                organization=org, name='Private Bank', slug='priv', is_public=False
            )
            q_pub = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            q_priv = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            qb_public.questions.add(q_pub)
            qb_private.questions.add(q_priv)

        # No tenant context — B2C path
        set_current_org(None)
        recs = intelligence_services.recommend_questions(user, limit=10)
        rec_ids = {q.id for q in recs}
        assert q_pub.id in rec_ids
        assert q_priv.id not in rec_ids, "B2C cross-tenant leak: private bank savol ko'rindi"


# ─── ISSUE-110 W3 regression: Membership/OrgInvite cross-org role ────────────


class TestCrossOrgRoleRejected:
    def test_membership_clean_rejects_cross_org_role(self, db, org, org2, user):
        # Org2 ning student role'ini Org1 membership'iga biriktirib ko'ramiz
        role_org2 = OrgRole.objects.get(organization=org2, name='student')
        m = Membership(
            user=user,
            organization=org,
            role=role_org2,
            status=MembershipStatus.ACTIVE,
        )
        with pytest.raises(ValidationError) as exc:
            m.clean()
        assert 'cross-org' in str(exc.value).lower() or 'boshqa tashkilot' in str(exc.value).lower()

    def test_membership_same_org_role_accepted(self, db, org, user):
        role_same = OrgRole.objects.get(organization=org, name='student')
        m = Membership(user=user, organization=org, role=role_same, status=MembershipStatus.ACTIVE)
        m.clean()  # raise qilmasin

    def test_orginvite_clean_rejects_cross_org_role(self, db, org, org2, user):
        role_org2 = OrgRole.objects.get(organization=org2, name='student')
        invite = OrgInvite(
            organization=org,
            role=role_org2,
            created_by=user,
        )
        with pytest.raises(ValidationError):
            invite.clean()


# ─── ISSUE-110 W7 regression: _profiles_with_min_attempts ORM annotate ──────


class TestProfilesAnnotateRefactor:
    def test_min_attempts_filter_works(self, db, user):
        """W7 fix: `.extra()` o'rniga `.annotate(F+F-2).filter(_attempts__gte=N)`."""
        skill = SkillTag.objects.create(name='Algebra', slug='algebra')
        # 1 ta correct + 1 ta incorrect → alpha=2, beta=2, attempts=2
        UserSkillProfile.objects.create(user=user, skill=skill, alpha=2, beta=2)
        # min_attempts=3 → bu profile yo'q (attempts=2 < 3)
        result = list(intelligence_services._profiles_with_min_attempts(user, 3))
        assert result == []
        # min_attempts=2 → bor
        result = list(intelligence_services._profiles_with_min_attempts(user, 2))
        assert len(result) == 1
        assert result[0].skill_id == skill.id
