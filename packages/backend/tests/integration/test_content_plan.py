"""Content plan (kontent rejasi): status math, counting rules, API permissions, tenant isolation."""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.catalog.models import ContentTarget, Question, QuestionVersion, Subject
from apps.catalog.services import content_plan as plan
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context

User = get_user_model()


def _q(org, subject, *, lang='uz', active=True, quarantined=False):
    with tenant_context(org):
        q = Question.objects.create(
            organization=org, subject=subject, language=lang, is_active=active
        )
        QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[],
            is_quarantined=quarantined,
        )
    return q


def _bulk(org, subject, n, **kw):
    for _ in range(n):
        _q(org, subject, **kw)


def _member(org, role, username):
    u = User.objects.create_user(username=username, email=f'{username}@x.uz', password='pass123')
    m = Membership.objects.create(
        user=u,
        organization=org,
        role=OrgRole.objects.get(organization=org, name=role),
        status=MembershipStatus.ACTIVE,
        is_primary=True,
    )
    m.activate()
    return u


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _row(resp, subject):
    return next(r for r in resp.json()['results'] if r['subject_id'] == str(subject.id))


LIST = 'catalog-api-v1:content-plan'
PATCH = 'catalog-api-v1:content-plan-target'


@pytest.mark.integration
class TestStatusMath:
    @pytest.mark.parametrize(
        ('count', 'target', 'expected'),
        [
            (0, 300, 'empty'),
            (1, 300, 'low'),
            (299, 300, 'low'),
            (300, 300, 'ready'),
            (301, 300, 'ready'),
            (0, 0, 'ready'),
        ],
    )
    def test_boundaries(self, count, target, expected):
        assert plan.compute_status(count, target) == expected

    def test_boundary_via_service(self, db, org, subject):
        ContentTarget.objects.create(organization=org, subject=subject, target_questions=3)
        _bulk(org, subject, 2)
        with tenant_context(org):
            assert plan.build_plan(org)['results'][0]['status'] == 'low'
        _q(org, subject)
        with tenant_context(org):
            assert plan.build_plan(org)['results'][0]['status'] == 'ready'


@pytest.mark.integration
class TestCounting:
    def test_defaults_without_target_row(self, db, org, subject):
        with tenant_context(org):
            row = plan.build_plan(org)['results'][0]
        assert row['target_questions'] == plan.DEFAULT_TARGET_QUESTIONS == 300
        assert row['target_variants'] == plan.DEFAULT_TARGET_VARIANTS == 10
        assert row['has_custom_target'] is False
        assert row['status'] == 'empty'

    def test_per_language_and_missing(self, db, org, subject):
        _bulk(org, subject, 2, lang='uz')
        _q(org, subject, lang='ru')
        _q(org, subject, lang='kaa')
        with tenant_context(org):
            row = plan.build_plan(org)['results'][0]
        assert row['approved_by_language'] == {'uz': 2, 'ru': 1, 'kaa': 1}
        assert row['approved_questions'] == 4
        assert row['missing_languages'] == []

    def test_missing_language_reported(self, db, org, subject):
        _q(org, subject, lang='uz')
        with tenant_context(org):
            assert plan.build_plan(org)['results'][0]['missing_languages'] == ['ru', 'kaa']

    def test_uz_ru_only_lists_karakalpak_missing(self, db, org, subject):
        _q(org, subject, lang='uz')
        _q(org, subject, lang='ru')
        with tenant_context(org):
            assert plan.build_plan(org)['results'][0]['missing_languages'] == ['kaa']

    def test_unknown_code_does_not_satisfy_karakalpak(self, db, org, subject):
        # 'qq' / 'kk' are not the Karakalpak code ('kaa'); they must not clear it.
        _q(org, subject, lang='uz')
        _q(org, subject, lang='ru')
        _q(org, subject, lang='qq')
        _q(org, subject, lang='kk')
        with tenant_context(org):
            assert plan.build_plan(org)['results'][0]['missing_languages'] == ['kaa']
        assert plan.CONTENT_LANGUAGES == ('uz', 'ru', 'kaa')

    def test_inactive_and_quarantined_do_not_count(self, db, org, subject):
        _q(org, subject, active=False)
        _q(org, subject, quarantined=True)
        with tenant_context(org):
            row = plan.build_plan(org)['results'][0]
        assert row['approved_questions'] == 0
        assert row['status'] == 'empty'

    def test_question_without_version_does_not_count(self, db, org, subject):
        with tenant_context(org):
            Question.objects.create(organization=org, subject=subject)
            assert plan.build_plan(org)['results'][0]['approved_questions'] == 0

    def test_unpublished_drafts_do_not_count(self, db, org, subject):
        from apps.catalog.models import ImportBatch, QuestionDraft

        with tenant_context(org):
            batch = ImportBatch.objects.create(organization=org, file_type='docx')
            QuestionDraft.objects.create(batch=batch, data={'text': 'x'}, subject=subject)
            assert plan.build_plan(org)['results'][0]['approved_questions'] == 0

    def test_summary_label(self, db, org):
        ready = Subject.objects.create(name='A', slug='a')
        low = Subject.objects.create(name='B', slug='b')
        Subject.objects.create(name='C', slug='c')
        for s in (ready, low):
            ContentTarget.objects.create(organization=org, subject=s, target_questions=2)
        _bulk(org, ready, 2)
        _q(org, low)
        with tenant_context(org):
            out = plan.build_plan(org)
        assert out['summary']['label'] == '3 total · 1 ready · 1 low · 1 empty'
        with tenant_context(org):
            only_low = plan.build_plan(org, status='low')
        assert [r['subject_name'] for r in only_low['results']] == ['B']
        assert only_low['summary']['total'] == 3


@pytest.mark.integration
class TestApi:
    def test_teacher_lists_with_counts(self, db, org, subject):
        _bulk(org, subject, 2)
        r = _client(_member(org, 'teacher', 't1')).get(reverse(LIST))
        assert r.status_code == 200
        assert _row(r, subject)['approved_questions'] == 2
        assert r.json()['summary']['total'] == 1

    def test_status_filter_and_invalid_status(self, db, org, subject):
        c = _client(_member(org, 'teacher', 't1'))
        assert c.get(reverse(LIST), {'status': 'ready'}).json()['results'] == []
        assert len(c.get(reverse(LIST), {'status': 'empty'}).json()['results']) == 1
        assert c.get(reverse(LIST), {'status': 'bogus'}).status_code == 400

    def test_patch_creates_then_updates_target(self, db, org, subject):
        c = _client(_member(org, 'manager', 'm1'))
        url = reverse(PATCH, args=[subject.id])
        r = c.patch(url, {'target_questions': 5}, content_type='application/json')
        assert r.status_code == 200
        assert r.json()['target_questions'] == 5
        assert r.json()['target_variants'] == 10
        r = c.patch(url, {'target_variants': 2}, content_type='application/json')
        assert r.json()['target_questions'] == 5
        assert r.json()['target_variants'] == 2
        assert ContentTarget.global_objects.filter(organization=org).count() == 1

    @pytest.mark.parametrize(
        'body',
        [
            {'target_questions': -1},
            {'target_questions': 'abc'},
            {'target_variants': 1.5},
            {'target_questions': True},
            {'target_questions': 10**9},
            {},
        ],
    )
    def test_patch_validation_rejects(self, db, org, subject, body):
        c = _client(_member(org, 'manager', 'm1'))
        r = c.patch(reverse(PATCH, args=[subject.id]), body, content_type='application/json')
        assert r.status_code == 400
        assert not ContentTarget.global_objects.exists()

    def test_patch_zero_allowed(self, db, org, subject):
        c = _client(_member(org, 'manager', 'm1'))
        r = c.patch(
            reverse(PATCH, args=[subject.id]),
            {'target_questions': 0},
            content_type='application/json',
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'ready'

    def test_student_cannot_read_or_update(self, db, org, subject):
        c = _client(_member(org, 'student', 's1'))
        assert c.get(reverse(LIST)).status_code == 403
        r = c.patch(
            reverse(PATCH, args=[subject.id]),
            {'target_questions': 1},
            content_type='application/json',
        )
        assert r.status_code == 403
        assert not ContentTarget.global_objects.exists()

    def test_anonymous_rejected(self, db, subject):
        assert Client().get(reverse(LIST)).status_code in (401, 403)

    def test_staff_without_org_can_read_but_not_patch(self, db, org, subject, superuser):
        c = _client(superuser)
        assert c.get(reverse(LIST)).status_code == 200


@pytest.mark.integration
class TestCrossTenant:
    def test_counts_and_targets_isolated(self, db, org, org2, subject):
        _bulk(org2, subject, 4)
        ContentTarget.global_objects.create(organization=org2, subject=subject, target_questions=2)
        r = _client(_member(org, 'teacher', 't1')).get(reverse(LIST))
        row = _row(r, subject)
        assert row['approved_questions'] == 0
        assert row['target_questions'] == 300
        assert row['has_custom_target'] is False

    def test_patch_does_not_touch_other_org(self, db, org, org2, subject):
        other = ContentTarget.global_objects.create(
            organization=org2, subject=subject, target_questions=7
        )
        c = _client(_member(org, 'manager', 'm1'))
        c.patch(
            reverse(PATCH, args=[subject.id]),
            {'target_questions': 9},
            content_type='application/json',
        )
        other.refresh_from_db()
        assert other.target_questions == 7
        assert ContentTarget.global_objects.filter(organization=org).count() == 1

    def test_private_subject_of_other_org_hidden_and_not_patchable(self, db, org, org2):
        s = Subject.objects.create(name='Secret', slug='secret', organization=org2)
        c = _client(_member(org, 'manager', 'm1'))
        assert c.get(reverse(LIST)).json()['results'] == []
        r = c.patch(
            reverse(PATCH, args=[s.id]), {'target_questions': 1}, content_type='application/json'
        )
        assert r.status_code == 404

    def test_manager_orm_scoped(self, db, org, org2, subject):
        ContentTarget.global_objects.create(organization=org2, subject=subject)
        with tenant_context(org):
            assert ContentTarget.objects.count() == 0


@pytest.mark.integration
class TestVariants:
    def test_only_published_mocks_with_subject_questions_count(self, db, org, subject, user):
        from datetime import timedelta

        from django.utils import timezone

        from apps.exams.models import MockExam, MockExamQuestion

        q = _q(org, subject)
        qv = q.versions.first()
        now = timezone.now()
        with tenant_context(org):
            for i, st in enumerate([MockExam.Status.PUBLISHED, MockExam.Status.DRAFT]):
                m = MockExam.objects.create(
                    organization=org,
                    title=f'm{i}',
                    duration_minutes=60,
                    scheduled_at=now,
                    closes_at=now + timedelta(hours=1),
                    status=st,
                    created_by=user,
                )
                MockExamQuestion.objects.create(mock_exam=m, question_version=qv, order=1)
            assert plan.build_plan(org)['results'][0]['variants'] == 1
