"""School / learning-centre rating: exact averages, thresholds, visibility, cache, isolation."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from apps.engagement import rating_service
from apps.engagement.rating_models import OrgRatingProfile
from apps.exams.models import ExamAttempt
from apps.organizations.models import Organization, OrgStatus
from core.tenant import tenant_context

from .engagement_helpers import client, make_attempt, make_exam, member

pytestmark = pytest.mark.integration

PUBLIC = '/api/v1/engagement/ratings/organizations/'
MINE = '/api/v1/engagement/ratings/mine/'
SETTINGS = '/api/v1/engagement/ratings/settings/'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


def build_org(slug, scores_by_exam, n_students, *, status=OrgStatus.ACTIVE):
    """Org with n_students students; each exam in `scores_by_exam` is taken by all of them
    (one attempt per student per exam) with that score."""
    org = Organization.objects.create(name=slug.upper(), slug=slug, status=status)
    owner = member(org, f'{slug}-owner', 'owner')
    students = [member(org, f'{slug}-s{i}', 'student') for i in range(n_students)]
    for i, score in enumerate(scores_by_exam):
        exam = make_exam(org, owner, title=f'{slug}-e{i}')
        for s in students:
            make_attempt(
                org, exam, s, status=ExamAttempt.Status.SUBMITTED, score=Decimal(str(score))
            )
    return org, owner


@pytest.fixture
def world(db):
    a, a_owner = build_org('alpha', [80, 90], 6)  # 6 students, 12 attempts, avg 85.00
    b, b_owner = build_org('bravo', [70, 70], 5)  # 5 students, 10 attempts, avg 70.00 (boundary)
    c, _ = build_org('charlie', [99, 99, 99], 4)  # 4 students -> not qualified
    d, _ = build_org('delta', [99], 5)  # 5 attempts -> not qualified
    e, e_owner = build_org('echo', [95, 95], 5)  # 10 attempts, avg 95.00, will be hidden
    OrgRatingProfile.objects.create(organization=a, kind='school')
    OrgRatingProfile.objects.create(organization=e, visibility='hidden')
    return {'a': a, 'b': b, 'c': c, 'd': d, 'e': e, 'a_owner': a_owner, 'e_owner': e_owner}


def _public(**params):
    return Client().get(PUBLIC, params)


class TestPublicRanking:
    def test_exact_order_and_numbers(self, world):
        body = _public().json()
        assert [
            (r['rank'], r['slug'], r['avg_score'], r['students'], r['attempts'])
            for r in body['results']
        ] == [
            (1, 'alpha', '85.00', 6, 12),
            (2, 'bravo', '70.00', 5, 10),
        ]
        assert body['min_students'] == 5 and body['min_attempts'] == 10

    def test_below_thresholds_are_excluded(self, world):
        slugs = {r['slug'] for r in _public().json()['results']}
        assert 'charlie' not in slugs  # 4 students, 12 attempts
        assert 'delta' not in slugs  # 5 students, 5 attempts

    def test_hidden_org_not_in_public_list(self, world):
        r = _public()
        assert 'echo' not in {x['slug'] for x in r.json()['results']}
        assert 'ECHO' not in r.content.decode()

    def test_kind_filter(self, world):
        rows = _public(kind='school').json()['results']
        assert [(r['rank'], r['slug']) for r in rows] == [(1, 'alpha')]
        rows = _public(kind='learning_centre').json()['results']
        assert [(r['rank'], r['slug']) for r in rows] == [(1, 'bravo')]

    def test_bad_params_rejected(self, world):
        assert _public(kind='zoo').status_code == 400
        assert _public(period='decade').status_code == 400

    def test_period_window(self, world):
        # age bravo's attempts to 40 days: gone from 'month', still in 'quarter'
        ExamAttempt.global_objects.filter(organization=world['b']).update(
            submitted_at=timezone.now() - timedelta(days=40)
        )
        assert [r['slug'] for r in _public(period='month').json()['results']] == ['alpha']
        assert [r['slug'] for r in _public(period='quarter').json()['results']] == [
            'alpha',
            'bravo',
        ]

    def test_non_submitted_attempts_ignored(self, world):
        ExamAttempt.global_objects.filter(organization=world['b']).update(
            status=ExamAttempt.Status.CANCELLED
        )
        assert [r['slug'] for r in _public().json()['results']] == ['alpha']

    def test_suspended_org_excluded(self, world):
        Organization.objects.filter(pk=world['a'].pk).update(status=OrgStatus.SUSPENDED)
        assert [r['slug'] for r in _public().json()['results']] == ['bravo']

    def test_result_is_cached(self, world):
        with mock.patch.object(
            rating_service, '_compute_stats', wraps=rating_service._compute_stats
        ) as spy:
            _public()
            _public()
            _public(kind='school')
        assert spy.call_count == 1


class TestOwnPosition:
    def test_hidden_org_sees_own_position(self, world):
        r = client(world['e_owner']).get(MINE)
        assert r.status_code == 200
        body = r.json()
        # avg 95.00 beats public 'learning_centre' org bravo (70.00) -> would be #1
        assert body['qualified'] is True and body['rank'] == 1
        assert body['avg_score'] == '95.00' and body['listed_publicly'] is False
        assert body['visibility'] == 'hidden'

    def test_public_org_position_matches_list(self, world):
        body = client(world['a_owner']).get(MINE).json()
        assert body['rank'] == 1 and body['listed_publicly'] is True and body['kind'] == 'school'

    def test_unqualified_org_gets_progress_not_rank(self, world):
        owner = member(world['c'], 'c-admin', 'manager')
        body = client(owner).get(MINE).json()
        assert body['qualified'] is False and body['rank'] is None
        assert (body['students'], body['attempts']) == (4, 12)

    def test_student_cannot_see_mine(self, world):
        from django.contrib.auth import get_user_model

        student = get_user_model().objects.get(username='alpha-s0')
        assert client(student).get(MINE).status_code == 403

    def test_anonymous_cannot_see_mine(self, world):
        assert Client().get(MINE).status_code in (401, 403)

    def test_hidden_org_does_not_shift_public_ranks(self, world):
        # echo (95) hidden: alpha must stay #1 in the public list
        assert _public().json()['results'][0]['slug'] == 'alpha'


class TestSettings:
    def test_admin_hides_org_and_cache_is_invalidated(self, world):
        assert 'alpha' in {r['slug'] for r in _public().json()['results']}  # warms the cache
        r = client(world['a_owner']).patch(
            SETTINGS, {'visibility': 'hidden'}, content_type='application/json'
        )
        assert r.status_code == 200 and r.json()['visibility'] == 'hidden'
        assert 'alpha' not in {x['slug'] for x in _public().json()['results']}

    def test_teacher_cannot_change_settings(self, world):
        teacher = member(world['a'], 'a-teach', 'teacher')
        r = client(teacher).patch(
            SETTINGS, {'visibility': 'hidden'}, content_type='application/json'
        )
        assert r.status_code == 403
        assert OrgRatingProfile.global_objects.get(organization=world['a']).visibility == 'public'

    def test_invalid_value_rejected(self, world):
        r = client(world['a_owner']).patch(
            SETTINGS, {'kind': 'university'}, content_type='application/json'
        )
        assert r.status_code == 400

    def test_settings_only_touch_own_org(self, world):
        client(world['a_owner']).patch(
            SETTINGS, {'kind': 'school'}, content_type='application/json'
        )
        assert OrgRatingProfile.global_objects.filter(organization=world['b']).count() == 0

    def test_profile_isolated_between_tenants(self, world):
        with tenant_context(world['b']):
            assert OrgRatingProfile.objects.count() == 0
        with tenant_context(world['a']):
            assert OrgRatingProfile.objects.count() == 1
