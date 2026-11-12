"""Arena quizzes: review workflow, visibility, timed play, scoring, tenant isolation."""

import uuid
from datetime import timedelta
from unittest import mock

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.engagement import arena_service
from apps.engagement.arena_models import Quiz, QuizPlay
from core.tenant import tenant_context

from .engagement_helpers import client, make_versions, member

pytestmark = pytest.mark.integration

BASE = '/api/v1/engagement/arena/quizzes/'


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()


@pytest.fixture
def setup(db, org, subject):
    teacher = member(org, 'teach', 'teacher')
    teacher2 = member(org, 'teach2', 'teacher')
    manager = member(org, 'boss', 'manager')
    student = member(org, 'stud', 'student')
    qvs = make_versions(org, subject, 4)
    return {
        'org': org,
        'teacher': teacher,
        'teacher2': teacher2,
        'manager': manager,
        'student': student,
        'qvs': qvs,
        'ids': [str(q.pk) for q in qvs],
    }


def _create(c, ids, **kw):
    r = c.post(
        BASE,
        {'title': 'Quiz', 'question_version_ids': ids, **kw},
        content_type='application/json',
    )
    assert r.status_code == 201, r.content
    return r.json()['id']


def _act(c, qid, action, body=None):
    return c.post(f'{BASE}{qid}/{action}/', body or {}, content_type='application/json')


def _approved_quiz(s, **kw):
    qid = _create(client(s['manager']), s['ids'], **kw)
    assert _act(client(s['manager']), qid, 'submit').status_code == 200
    return qid


class TestWorkflow:
    def test_teacher_quiz_goes_pending_not_live(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        r = _act(client(setup['teacher']), qid, 'submit')
        assert r.status_code == 200
        assert r.json()['status'] == 'pending_review'

    def test_admin_quiz_is_approved_immediately(self, setup):
        qid = _create(client(setup['manager']), setup['ids'])
        r = _act(client(setup['manager']), qid, 'submit')
        assert r.json()['status'] == 'approved'

    def test_admin_approves_teacher_quiz(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        r = _act(client(setup['manager']), qid, 'approve')
        assert r.status_code == 200 and r.json()['status'] == 'approved'

    def test_teacher_cannot_approve_own_quiz(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        r = _act(client(setup['teacher']), qid, 'approve')
        assert r.status_code == 403
        assert Quiz.global_objects.get(pk=qid).status == 'pending_review'

    def test_teacher_cannot_approve_other_teachers_quiz(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        # other teachers can't even see it (404), which is stricter than 403
        assert _act(client(setup['teacher2']), qid, 'approve').status_code in (403, 404)
        assert Quiz.global_objects.get(pk=qid).status == 'pending_review'

    def test_admin_cannot_approve_own_pending_quiz(self, setup):
        # an admin quiz that ended up pending (e.g. author was demoted at submit time)
        qid = _create(client(setup['manager']), setup['ids'])
        Quiz.global_objects.filter(pk=qid).update(status='pending_review')
        assert _act(client(setup['manager']), qid, 'approve').status_code == 403

    def test_reject_needs_note_and_returns_to_draft(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        assert _act(client(setup['manager']), qid, 'reject', {'note': ' '}).status_code == 400
        r = _act(client(setup['manager']), qid, 'reject', {'note': 'fix Q2'})
        assert r.json()['status'] == 'draft'

    def test_approve_requires_pending(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        assert _act(client(setup['manager']), qid, 'approve').status_code == 409

    def test_submit_needs_minimum_questions(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'][:2])
        assert _act(client(setup['teacher']), qid, 'submit').status_code == 400

    def test_student_cannot_create(self, setup):
        r = client(setup['student']).post(
            BASE,
            {'title': 'x', 'question_version_ids': setup['ids']},
            content_type='application/json',
        )
        assert r.status_code == 403

    def test_status_not_writable_via_patch(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        r = client(setup['teacher']).patch(
            f'{BASE}{qid}/',
            {'status': 'approved', 'title': 'new'},
            content_type='application/json',
        )
        assert r.status_code == 200
        quiz = Quiz.global_objects.get(pk=qid)
        assert quiz.status == 'draft' and quiz.title == 'new'

    def test_cannot_edit_after_submit(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        r = client(setup['teacher']).patch(
            f'{BASE}{qid}/', {'title': 'x'}, content_type='application/json'
        )
        assert r.status_code == 403

    def test_rejects_foreign_org_questions(self, setup, org2, subject):
        foreign = make_versions(org2, subject, 3)
        r = client(setup['teacher']).post(
            BASE,
            {'title': 'foreign', 'question_version_ids': [str(q.pk) for q in foreign]},
            content_type='application/json',
        )
        assert r.status_code == 400
        assert not Quiz.global_objects.filter(title='foreign').exists()


class TestVisibility:
    def test_student_cannot_see_pending_quiz(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        c = client(setup['student'])
        assert c.get(f'{BASE}{qid}/').status_code == 404
        assert qid not in [q['id'] for q in c.get(BASE).json()]
        assert c.post(f'{BASE}{qid}/play/').status_code == 404

    def test_student_and_other_teacher_cannot_see_draft(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        assert client(setup['student']).get(f'{BASE}{qid}/').status_code == 404
        assert client(setup['teacher2']).get(f'{BASE}{qid}/').status_code == 404

    def test_author_and_admin_see_pending(self, setup):
        qid = _create(client(setup['teacher']), setup['ids'])
        _act(client(setup['teacher']), qid, 'submit')
        assert client(setup['teacher']).get(f'{BASE}{qid}/').status_code == 200
        assert client(setup['manager']).get(f'{BASE}{qid}/').status_code == 200

    def test_student_sees_approved_without_answer_key(self, setup):
        qid = _approved_quiz(setup)
        r = client(setup['student']).get(f'{BASE}{qid}/')
        assert r.status_code == 200
        assert 'questions' not in r.json()
        assert client(setup['student']).get(BASE).json()[0]['id'] == qid

    def test_cross_tenant_quiz_is_404(self, setup, org2):
        qid = _approved_quiz(setup)
        outsider = member(org2, 'outsider', 'owner')
        assert client(outsider).get(f'{BASE}{qid}/').status_code == 404
        assert client(outsider).get(BASE).json() == []
        assert _act(client(outsider), qid, 'approve').status_code == 404

    def test_cross_tenant_isolation_on_manager(self, setup, org2):
        qid = _approved_quiz(setup)
        with tenant_context(org2):
            assert Quiz.objects.filter(pk=qid).count() == 0
        with tenant_context(setup['org']):
            assert Quiz.objects.filter(pk=qid).count() == 1


class TestPlay:
    def _start(self, s, qid):
        r = client(s['student']).post(f'{BASE}{qid}/play/')
        assert r.status_code == 200, r.content
        return r.json()

    def _answer(self, user, qid, question_id, selected):
        return client(user).post(
            f'{BASE}{qid}/play/answer/',
            {'question_id': question_id, 'selected': selected},
            content_type='application/json',
        )

    def test_start_hides_correct_answers(self, setup):
        qid = _approved_quiz(setup)
        state = self._start(setup, qid)
        assert state['total'] == 4 and state['index'] == 0
        assert all('is_correct' not in o for o in state['question']['options'])

    def test_instant_correct_answer_is_nearly_1000(self, setup):
        qid = _approved_quiz(setup, seconds_per_question=20)
        state = self._start(setup, qid)
        body = self._answer(setup['student'], qid, state['question']['id'], 1).json()
        assert body['is_correct'] is True
        assert 975 <= body['points_awarded'] <= 1000  # up to 1 s of test latency = 25 pts

    def test_points_at_half_time_is_exactly_750(self, setup):
        qid = _approved_quiz(setup, seconds_per_question=20)
        state = self._start(setup, qid)
        base = timezone.now()
        QuizPlay.global_objects.update(question_started_at=base)
        with mock.patch.object(arena_service, '_now', return_value=base + timedelta(seconds=10)):
            body = self._answer(setup['student'], qid, state['question']['id'], 1).json()
        assert body['points_awarded'] == 750  # 500 + 500 * (20 - 10) / 20

    def test_wrong_answer_zero_points_and_key_revealed(self, setup):
        qid = _approved_quiz(setup)
        state = self._start(setup, qid)
        body = self._answer(setup['student'], qid, state['question']['id'], 2).json()
        assert body['is_correct'] is False and body['points_awarded'] == 0
        assert body['correct_option_ids'] == [1]

    def test_late_answer_scores_zero_but_grace_is_honoured(self, setup):
        qid = _approved_quiz(setup, seconds_per_question=10)
        state = self._start(setup, qid)
        base = timezone.now()
        QuizPlay.global_objects.update(question_started_at=base)
        # 10 s limit + 1.5 s grace: 11 s is accepted (0 speed bonus), 12 s is late
        with mock.patch.object(arena_service, '_now', return_value=base + timedelta(seconds=11)):
            ok = self._answer(setup['student'], qid, state['question']['id'], 1).json()
        assert ok['timed_out'] is False and ok['points_awarded'] == 500
        QuizPlay.global_objects.update(question_started_at=base)
        with mock.patch.object(arena_service, '_now', return_value=base + timedelta(seconds=12)):
            late = self._answer(setup['student'], qid, ok['question']['id'], 1).json()
        assert late['timed_out'] is True and late['points_awarded'] == 0

    def test_skip_with_null_counts_as_timeout(self, setup):
        qid = _approved_quiz(setup)
        state = self._start(setup, qid)
        body = self._answer(setup['student'], qid, state['question']['id'], None).json()
        assert body['timed_out'] is True and body['points_awarded'] == 0

    def test_must_answer_current_question(self, setup):
        qid = _approved_quiz(setup)
        self._start(setup, qid)
        r = self._answer(setup['student'], qid, str(uuid.uuid4()), 1)
        assert r.status_code == 409

    def test_full_run_then_leaderboard_and_single_play(self, setup):
        qid = _approved_quiz(setup)
        state = self._start(setup, qid)
        for _ in range(4):
            state = (
                self._answer(setup['student'], qid, state['question']['id'], 1).json()
                if ('question' in state)
                else state
            )
        assert state['finished'] is True and state['correct_count'] == 4
        assert 3900 <= state['points'] <= 4000
        assert client(setup['student']).post(f'{BASE}{qid}/play/').status_code == 409
        lb = client(setup['student']).get(f'{BASE}{qid}/leaderboard/').json()
        assert lb['total'] == 1
        assert lb['entries'][0]['username'] == 'stud'
        assert lb['me']['rank'] == 1

    def test_leaderboard_orders_by_points(self, setup, org):
        qid = _approved_quiz(setup)
        other = member(org, 'stud2', 'student')
        for user, wrong_first in ((other, True), (setup['student'], False)):
            c = client(user)
            st = c.post(f'{BASE}{qid}/play/').json()
            first = True
            while 'question' in st:
                sel = 2 if (wrong_first and first) else 1
                first = False
                st = self._answer(user, qid, st['question']['id'], sel).json()
        entries = client(setup['student']).get(f'{BASE}{qid}/leaderboard/').json()['entries']
        assert [e['username'] for e in entries] == ['stud', 'stud2']
        assert entries[0]['points'] > entries[1]['points']

    def test_cannot_play_archived_quiz(self, setup):
        qid = _approved_quiz(setup)
        assert _act(client(setup['manager']), qid, 'archive').status_code == 200
        assert client(setup['student']).post(f'{BASE}{qid}/play/').status_code == 404

    def test_play_rows_isolated_between_tenants(self, setup, org2):
        qid = _approved_quiz(setup)
        self._start(setup, qid)
        with tenant_context(org2):
            assert QuizPlay.objects.count() == 0
        with tenant_context(setup['org']):
            assert QuizPlay.objects.count() == 1
