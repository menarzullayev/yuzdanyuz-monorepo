"""
Mistake notebook (xatolar daftari): spaced repetition 1 -> 3 -> 7 -> 21 days.

freezegun is not a dev requirement, so time is passed explicitly (`now=`) to
the service layer; API tests shift `due_at` in the DB instead.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import ExamAttempt, MockExam, PracticeSession, UserAnswer
from apps.intelligence import mistakes
from apps.intelligence.models import MistakeCard
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context, unscoped_context

TASHKENT = ZoneInfo('Asia/Tashkent')
# 2026-03-10 22:30 Tashkent == 17:30 UTC; late evening so day arithmetic is exercised
T0 = datetime(2026, 3, 10, 22, 30, tzinfo=TASHKENT)

pytestmark = pytest.mark.integration


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_question(org, subject, *, qtype=Question.Type.SINGLE_CHOICE, correct=1):
    with tenant_context(org):
        q = Question.objects.create(organization=org, subject=subject, type=qtype)
        qv = QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[
                {'id': 1, 'text': 'A', 'is_correct': correct == 1},
                {'id': 2, 'text': 'B', 'is_correct': correct == 2},
            ],
            explanation={'text': 'because'},
        )
    return q, qv


def make_attempt(org, user):
    with tenant_context(org):
        exam = MockExam.objects.create(
            organization=org,
            title='M',
            duration_minutes=10,
            scheduled_at=timezone.now(),
            closes_at=timezone.now() + timedelta(hours=1),
            created_by=user,
        )
        return ExamAttempt.objects.create(organization=org, exam=exam, user=user)


def answer(org, attempt, qv, *, correct, **kw):
    with tenant_context(org):
        return UserAnswer.objects.create(
            organization=org,
            attempt=attempt,
            question_version=qv,
            selected={'id': 1},
            is_correct=correct,
            **kw,
        )


def card_of(user, q):
    return MistakeCard.global_objects.get(user=user, question=q)


def _version_of(card):
    with unscoped_context():
        return QuestionVersion.objects.get(question_id=card.question_id)


@pytest.fixture
def wrong_card(db, org, user, member, subject):
    """A card created through the real hook from a wrong exam answer."""
    q, qv = make_question(org, subject)
    answer(org, make_attempt(org, user), qv, correct=False)
    return card_of(user, q)


def review(card, user, selected, now):
    """Service call under the card's tenant context, as TenantMiddleware does in a request."""
    if isinstance(selected, int):
        selected = {'id': selected}
    with tenant_context(card.organization):
        return mistakes.review_card(card.id, user, selected, now=now)


# ─── Hook: wrong answers create cards ─────────────────────────────────────────


class TestHook:
    def test_wrong_exam_answer_creates_stage1_card_due_tomorrow(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        attempt = make_attempt(org, user)
        ans = answer(org, attempt, qv, correct=False)

        card = card_of(user, q)
        assert (card.stage, card.lapses, card.mastered_at) == (1, 0, None)
        assert card.organization_id == org.id
        assert card.source_answer_id == ans.id
        assert card.source_attempt_id == attempt.id and card.source_session_id is None
        assert card.due_at == mistakes.start_of_day_after(1)
        assert card.due_at.astimezone(TASHKENT).time().isoformat() == '00:00:00'

    def test_wrong_practice_answer_creates_card(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        with tenant_context(org):
            session = PracticeSession.objects.create(organization=org, user=user, blueprint={})
            UserAnswer.objects.create(
                organization=org,
                session=session,
                question_version=qv,
                selected={'id': 2},
                is_correct=False,
            )
        card = card_of(user, q)
        assert card.source_session_id == session.id and card.source_attempt_id is None

    def test_correct_answer_never_creates_card(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        answer(org, make_attempt(org, user), qv, correct=True)
        assert not MistakeCard.global_objects.exists()

    def test_ungraded_answer_never_creates_card(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        answer(org, make_attempt(org, user), qv, correct=None)
        assert not MistakeCard.global_objects.exists()

    def test_quarantine_bonus_answer_never_creates_card(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        answer(org, make_attempt(org, user), qv, correct=False, auto_correct=True)
        assert not MistakeCard.global_objects.exists()

    def test_non_gradable_type_never_creates_card(self, org, user, member, subject):
        q, qv = make_question(org, subject, qtype=Question.Type.OPEN_ENDED)
        answer(org, make_attempt(org, user), qv, correct=False)
        assert not MistakeCard.global_objects.exists()

    def test_resaving_same_answer_is_noop_and_keeps_progress(self, wrong_card, user):
        review(wrong_card, user, 1, wrong_card.due_at)  # stage 1 -> 2
        with unscoped_context():
            wrong_card.source_answer.save()  # update_or_create re-saves the same row
        card = MistakeCard.global_objects.get(pk=wrong_card.pk)
        assert card.stage == 2 and card.lapses == 0

    def test_no_duplicates_across_attempts(self, org, user, member, subject):
        q, qv = make_question(org, subject)
        for _ in range(3):
            answer(org, make_attempt(org, user), qv, correct=False)
        assert MistakeCard.global_objects.filter(user=user, question=q).count() == 1

    def test_new_wrong_exam_answer_resets_progress(self, wrong_card, org, user):
        now = wrong_card.due_at
        review(wrong_card, user, 1, now)
        now = card_of(user, wrong_card.question_id).due_at
        review(wrong_card, user, 1, now)  # now stage 3
        assert card_of(user, wrong_card.question_id).stage == 3

        qv = _version_of(wrong_card)
        answer(org, make_attempt(org, user), qv, correct=False)

        card = card_of(user, wrong_card.question_id)
        assert (card.stage, card.lapses) == (1, 1)
        assert card.due_at == mistakes.start_of_day_after(1)

    def test_new_wrong_answer_revives_mastered_card(self, wrong_card, org, user):
        wrong_card.mastered_at = timezone.now()
        wrong_card.stage = 4
        wrong_card.save()
        qv = _version_of(wrong_card)
        answer(org, make_attempt(org, user), qv, correct=False)
        card = card_of(user, wrong_card.question_id)
        assert card.mastered_at is None and card.stage == 1

    def test_correct_later_answer_does_not_touch_card(self, wrong_card, org, user):
        qv = _version_of(wrong_card)
        answer(org, make_attempt(org, user), qv, correct=True)
        card = card_of(user, wrong_card.question_id)
        assert (card.stage, card.lapses) == (1, 0)


# ─── Schedule ─────────────────────────────────────────────────────────────────


class TestSchedule:
    def test_start_of_day_is_tashkent_not_utc(self):
        assert mistakes.start_of_day_after(1, T0) == datetime(2026, 3, 11, tzinfo=TASHKENT)
        assert mistakes.start_of_day_after(1, T0) == datetime(2026, 3, 10, 19, tzinfo=UTC)
        # 02:00 Tashkent on the 11th is still the 10th in UTC: must count from the 11th
        early = datetime(2026, 3, 11, 2, tzinfo=TASHKENT)
        assert mistakes.start_of_day_after(1, early) == datetime(2026, 3, 12, tzinfo=TASHKENT)

    def test_full_progression_1_3_7_21_then_mastered(self, wrong_card, user):
        now = wrong_card.due_at  # first review day
        for stage, days in [(2, 3), (3, 7), (4, 21)]:
            res = review(wrong_card, user, 1, now)
            assert res['correct'] is True
            card = card_of(user, wrong_card.question_id)
            assert card.stage == stage and card.mastered_at is None
            assert card.due_at == mistakes.start_of_day_after(days, now)
            assert card.last_reviewed_at == now
            now = card.due_at

        res = review(wrong_card, user, 1, now)  # the 21-day review
        card = card_of(user, wrong_card.question_id)
        assert res['correct'] is True and card.mastered_at == now
        assert card.lapses == 0

    def test_wrong_review_resets_to_stage1_due_tomorrow(self, wrong_card, user):
        review(wrong_card, user, 1, wrong_card.due_at)
        now = card_of(user, wrong_card.question_id).due_at
        res = review(wrong_card, user, 2, now)  # wrong option
        card = card_of(user, wrong_card.question_id)
        assert res['correct'] is False
        assert (card.stage, card.lapses, card.mastered_at) == (1, 1, None)
        assert card.due_at == mistakes.start_of_day_after(1, now)

    def test_cannot_review_before_due(self, wrong_card, user):
        with pytest.raises(mistakes.NotDueError):
            review(wrong_card, user, 1, wrong_card.due_at - timedelta(seconds=1))
        assert card_of(user, wrong_card.question_id).stage == 1

    def test_cannot_review_mastered_card(self, wrong_card, user):
        wrong_card.mastered_at = timezone.now()
        wrong_card.save()
        with pytest.raises(mistakes.AlreadyMasteredError):
            review(wrong_card, user, 1, wrong_card.due_at)

    def test_grading_matches_exams_logic_for_multiple_choice(self, org, user, member, subject):
        q, qv = make_question(org, subject, qtype=Question.Type.MULTIPLE_CHOICE)
        qv.options = [
            {'id': 1, 'text': 'A', 'is_correct': True},
            {'id': 2, 'text': 'B', 'is_correct': True},
            {'id': 3, 'text': 'C', 'is_correct': False},
        ]
        qv.save()
        answer(org, make_attempt(org, user), qv, correct=False)
        card = card_of(user, q)
        assert review(card, user, [1], card.due_at)['correct'] is False
        card = card_of(user, q)
        assert review(card, user, [2, 1], card.due_at)['correct'] is True


# ─── API ──────────────────────────────────────────────────────────────────────


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post_review(client, card_id, selected):
    return client.post(
        reverse('intelligence:mistakes-review', args=[card_id]),
        data={'selected': selected},
        content_type='application/json',
    )


def make_due(card):
    MistakeCard.global_objects.filter(pk=card.pk).update(due_at=timezone.now() - timedelta(hours=1))


class TestApi:
    def test_requires_authentication(self, db):
        c = Client()
        assert c.get(reverse('intelligence:mistakes-due')).status_code in (401, 403)
        assert c.get(reverse('intelligence:mistakes-summary')).status_code in (401, 403)

    def test_due_list_filters_by_date_and_hides_answers(self, wrong_card, org, user, subject):
        c = _client(user)
        url = reverse('intelligence:mistakes-due')
        assert c.get(url).json()['count'] == 0  # due tomorrow, not yet

        make_due(wrong_card)
        body = c.get(url).json()
        assert body['count'] == 1
        item = body['results'][0]
        assert item['question_id'] == str(wrong_card.question_id)
        assert item['question']['content'] == {'text': 'q'}
        assert all('is_correct' not in o for o in item['question']['options'])

        # a mastered card stays out even when overdue
        q3, qv3 = make_question(org, subject)
        answer(org, make_attempt(org, user), qv3, correct=False)
        c3 = card_of(user, q3)
        make_due(c3)
        MistakeCard.global_objects.filter(pk=c3.pk).update(mastered_at=timezone.now())
        assert c.get(url).json()['count'] == 1

    def test_due_list_is_paginated(self, wrong_card, org, user, subject):
        for _ in range(21):
            q, qv = make_question(org, subject)
            answer(org, make_attempt(org, user), qv, correct=False)
        MistakeCard.global_objects.filter(user=user).update(
            due_at=timezone.now() - timedelta(hours=1)
        )
        body = _client(user).get(reverse('intelligence:mistakes-due')).json()
        assert body['count'] == 22 and len(body['results']) == 20 and body['next']

    def test_summary_counts_per_stage(self, wrong_card, org, user, subject):
        make_due(wrong_card)
        q2, qv2 = make_question(org, subject)
        answer(org, make_attempt(org, user), qv2, correct=False)
        MistakeCard.global_objects.filter(question=q2).update(stage=3)
        q3, qv3 = make_question(org, subject)
        answer(org, make_attempt(org, user), qv3, correct=False)
        MistakeCard.global_objects.filter(question=q3).update(mastered_at=timezone.now())

        body = _client(user).get(reverse('intelligence:mistakes-summary')).json()
        by_stage = {s['stage']: s for s in body['stages']}
        assert [by_stage[i]['interval_days'] for i in (1, 2, 3, 4)] == [1, 3, 7, 21]
        assert (by_stage[1]['total'], by_stage[1]['due']) == (1, 1)
        assert (by_stage[3]['total'], by_stage[3]['due']) == (1, 0)
        assert by_stage[2]['total'] == by_stage[4]['total'] == 0
        assert (body['due_total'], body['active_total'], body['mastered']) == (1, 2, 1)

    def test_review_correct_advances_and_reveals_answer(self, wrong_card, user):
        make_due(wrong_card)
        resp = _post_review(_client(user), wrong_card.id, {'id': 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body['correct'] is True and body['stage'] == 2 and body['mastered'] is False
        assert body['correct_option_ids'] == [1] and body['explanation'] == {'text': 'because'}

    def test_review_wrong_resets(self, wrong_card, user):
        make_due(wrong_card)
        MistakeCard.global_objects.filter(pk=wrong_card.pk).update(stage=3)
        body = _post_review(_client(user), wrong_card.id, {'id': 2}).json()
        assert body['correct'] is False and body['stage'] == 1 and body['lapses'] == 1

    def test_review_not_due_is_400_and_unchanged(self, wrong_card, user):
        resp = _post_review(_client(user), wrong_card.id, {'id': 1})
        assert resp.status_code == 400
        assert card_of(user, wrong_card.question_id).stage == 1

    def test_review_requires_selected(self, wrong_card, user):
        make_due(wrong_card)
        resp = _client(user).post(
            reverse('intelligence:mistakes-review', args=[wrong_card.id]),
            data={},
            content_type='application/json',
        )
        assert resp.status_code == 400


# ─── Isolation (negative) ─────────────────────────────────────────────────────


def _member(user, org):
    role = OrgRole.objects.get(organization=org, name='student')
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


class TestIsolation:
    def test_other_user_same_org_cannot_see_or_review_card(self, wrong_card, org, user2):
        _member(user2, org)
        make_due(wrong_card)
        c2 = _client(user2)

        assert c2.get(reverse('intelligence:mistakes-due')).json()['count'] == 0
        assert c2.get(reverse('intelligence:mistakes-summary')).json()['active_total'] == 0

        assert _post_review(c2, wrong_card.id, {'id': 1}).status_code == 404
        card = card_of(wrong_card.user_id, wrong_card.question_id)
        assert (card.stage, card.last_reviewed_at) == (1, None)  # untouched

    def test_cross_tenant_card_is_invisible_and_not_reviewable(
        self, wrong_card, org, org2, user, user2, subject
    ):
        _member(user2, org2)
        q2, qv2 = make_question(org2, subject)
        answer(org2, make_attempt(org2, user2), qv2, correct=False)
        card2 = card_of(user2, q2)
        make_due(wrong_card)
        make_due(card2)

        c1 = _client(user)
        ids = {i['id'] for i in c1.get(reverse('intelligence:mistakes-due')).json()['results']}
        assert ids == {str(wrong_card.id)}
        assert _post_review(c1, card2.id, {'id': 1}).status_code == 404

        # even the same user does not see a card stored under another tenant
        MistakeCard.global_objects.filter(pk=wrong_card.pk).update(organization=org2)
        assert c1.get(reverse('intelligence:mistakes-due')).json()['count'] == 0
        assert _post_review(c1, wrong_card.id, {'id': 1}).status_code == 404

    def test_tenant_manager_scopes_cards(self, wrong_card, org, org2):
        with tenant_context(org):
            assert MistakeCard.objects.count() == 1
        with tenant_context(org2):
            assert MistakeCard.objects.count() == 0
        with unscoped_context():
            assert MistakeCard.objects.count() == 1
