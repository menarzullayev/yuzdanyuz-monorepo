"""
Question quality statistics (savol sifati).

Covers: flag boundaries (7 vs 8 attempts, 19 vs 20, 59% vs 60% skips), the skip definition
(explicit empty selections + implicit skips in submitted attempts), idempotency, cross-tenant
isolation and the staff-only API (students are rejected).
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import JSONField, Value
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.analytics import question_quality as qq
from apps.analytics.models import QuestionQualityStat
from apps.analytics.tasks import refresh_question_quality
from apps.catalog.models import Question, QuestionVersion, Subject
from apps.exams.models import ExamAttempt, MockExam, MockExamQuestion, PracticeSession, UserAnswer
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context, unscoped_context

User = get_user_model()
Flag = QuestionQualityStat.Flag

# ─── Helpers ──────────────────────────────────────────────────────────────────

_UNSET = object()


def _qv(org, subject, qtype=Question.Type.SINGLE_CHOICE):
    with tenant_context(org):
        q = Question.objects.create(organization=org, subject=subject, type=qtype)
        return QuestionVersion.objects.create(
            question=q,
            version_number=1,
            content={'text': 'q'},
            options=[{'id': 1, 'text': 'A', 'is_correct': True}],
        )


def _answers(org, user, qv, *, right=0, wrong=0, skip=0, skip_value=_UNSET, **extra):
    """Create practice-session answers: one session (= one attempt of the question) each."""

    def make(selected, is_correct):
        with tenant_context(org):
            s = PracticeSession.objects.create(organization=org, user=user, blueprint=[])
            UserAnswer.objects.create(
                organization=org,
                session=s,
                question_version=qv,
                # None must be stored as JSON null (plain None would be SQL NULL).
                selected=Value(None, JSONField()) if selected is None else selected,
                is_correct=is_correct,
                **extra,
            )

    for _ in range(right):
        make({'id': 1}, True)
    for _ in range(wrong):
        make({'id': 2}, False)
    for _ in range(skip):
        make({} if skip_value is _UNSET else skip_value, False)


def _refresh(org):
    with tenant_context(org):
        return qq.refresh_for_org(org)


def _stat(org, qv):
    with unscoped_context():
        return QuestionQualityStat.objects.get(organization=org, question_version=qv)


def _stat_count(org):
    with unscoped_context():
        return QuestionQualityStat.objects.filter(organization=org).count()


def _member(user, org, role_name):
    role = OrgRole.objects.get(organization=org, name=role_name)
    m = Membership.objects.create(
        user=user, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return m


@pytest.fixture
def admin_member(db, user, org):
    return _member(user, org, 'owner')


# ─── Pure flag logic ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestEvaluateFlags:
    @pytest.mark.parametrize(
        ('attempts', 'correct', 'skipped', 'expected'),
        [
            (8, 0, 0, ['ALL_WRONG']),
            (8, 8, 0, ['ALL_RIGHT']),
            (7, 0, 0, []),  # one short of the minimum
            (7, 7, 0, []),
            (7, 0, 7, []),
            (8, 1, 0, []),  # one right answer -> not ALL_WRONG
            (8, 7, 0, []),  # one wrong answer -> not ALL_RIGHT
            (100, 50, 59, []),  # 59% skipped -> below threshold
            (100, 40, 60, ['OFTEN_SKIPPED']),  # exactly 60%
            (10, 4, 5, []),  # 50%
            (10, 4, 6, ['OFTEN_SKIPPED']),  # 60%
            (8, 0, 8, ['OFTEN_SKIPPED']),  # all skipped: wording issue, not answer key
            (8, 0, 5, ['ALL_WRONG', 'OFTEN_SKIPPED']),  # 3 real wrong answers + 62% skipped
            (8, 3, 4, []),  # 50% skipped, mixed results
        ],
    )
    def test_flags(self, attempts, correct, skipped, expected):
        assert qq.evaluate_flags(attempts, correct, skipped) == expected

    def test_thresholds_are_module_constants(self):
        assert qq.MIN_ATTEMPTS_FOR_FLAGS == 8
        assert qq.PRELIMINARY_BELOW_ATTEMPTS == 20
        assert qq.SKIP_RATE_THRESHOLD_PERCENT == 60


# ─── Computation from UserAnswer ──────────────────────────────────────────────


@pytest.mark.integration
class TestComputation:
    def test_all_wrong_fires_at_8_not_at_7(self, db, org, user, subject):
        qv8, qv7 = _qv(org, subject), _qv(org, subject)
        _answers(org, user, qv8, wrong=8)
        _answers(org, user, qv7, wrong=7)
        _refresh(org)
        assert _stat(org, qv8).flags == ['ALL_WRONG']
        s7 = _stat(org, qv7)
        assert s7.flags == []
        assert s7.attempts == 7  # still computed, just not flagged

    def test_all_right(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, right=8)
        _refresh(org)
        s = _stat(org, qv)
        assert s.flags == ['ALL_RIGHT']
        assert s.correct_rate == 1.0

    def test_mixed_results_no_flag(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, right=5, wrong=5)
        _refresh(org)
        s = _stat(org, qv)
        assert s.flags == []
        assert s.correct_rate == 0.5

    def test_skip_59_vs_60_percent(self, db, org, user, subject):
        qv59, qv60 = _qv(org, subject), _qv(org, subject)
        _answers(org, user, qv59, right=41, skip=59)
        _answers(org, user, qv60, right=40, skip=60)
        _refresh(org)
        assert _stat(org, qv59).flags == []
        s60 = _stat(org, qv60)
        assert s60.flags == ['OFTEN_SKIPPED']
        assert s60.skip_rate == 0.6

    @pytest.mark.parametrize('empty', [{}, [], '', None])
    def test_every_empty_selection_form_is_a_skip(self, db, org, user, subject, empty):
        qv = _qv(org, subject)
        _answers(org, user, qv, right=3, skip=5, skip_value=empty)
        _refresh(org)
        s = _stat(org, qv)
        assert (s.attempts, s.skipped_count) == (8, 5)
        assert s.flags == ['OFTEN_SKIPPED']

    def test_non_empty_selection_is_not_a_skip(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, wrong=8)  # selected={'id': 2}
        _refresh(org)
        assert _stat(org, qv).skipped_count == 0

    def test_all_skipped_is_not_all_wrong(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, skip=8)
        _refresh(org)
        assert _stat(org, qv).flags == ['OFTEN_SKIPPED']

    def test_preliminary_19_vs_20(self, db, org, user, subject):
        qv19, qv20 = _qv(org, subject), _qv(org, subject)
        _answers(org, user, qv19, right=19)
        _answers(org, user, qv20, right=20)
        _refresh(org)
        assert _stat(org, qv19).is_preliminary is True
        assert _stat(org, qv20).is_preliminary is False
        assert _stat(org, qv19).flags == ['ALL_RIGHT']  # flagged but preliminary

    def test_ungraded_answers_excluded(self, db, org, user, subject):
        qv = _qv(org, subject)
        with tenant_context(org):
            for _ in range(8):
                s = PracticeSession.objects.create(organization=org, user=user, blueprint=[])
                UserAnswer.objects.create(
                    organization=org,
                    session=s,
                    question_version=qv,
                    selected='essay text',
                    is_correct=None,
                )
        _refresh(org)
        assert _stat_count(org) == 0

    def test_auto_correct_and_soft_deleted_excluded(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, wrong=8, auto_correct=True)
        _answers(org, user, qv, wrong=8, is_deleted=True)
        _refresh(org)
        assert _stat_count(org) == 0

    def test_quarantined_version_excluded(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, wrong=8)
        QuestionVersion.objects.filter(pk=qv.pk).update(is_quarantined=True)
        _refresh(org)
        assert _stat_count(org) == 0

    def test_implicit_skip_in_submitted_attempts(self, db, org, subject):
        """No UserAnswer row at all for a pinned question in a SUBMITTED attempt = skip."""
        qv = _qv(org, subject)
        creator = User.objects.create_user(username='creator', email='creator@x.uz', password='x')
        now = timezone.now()
        with tenant_context(org):
            exam = MockExam.objects.create(
                organization=org,
                title='M',
                duration_minutes=60,
                scheduled_at=now - timedelta(hours=1),
                closes_at=now + timedelta(hours=1),
                status=MockExam.Status.PUBLISHED,
                created_by=creator,
            )
            MockExamQuestion.objects.create(mock_exam=exam, question_version=qv, order=1)
            for i in range(8):
                u = User.objects.create_user(username=f'stu{i}', email=f'stu{i}@x.uz', password='x')
                ExamAttempt.objects.create(
                    organization=org, exam=exam, user=u, status=ExamAttempt.Status.SUBMITTED
                )
            # In-progress attempt must not count.
            u = User.objects.create_user(username='busy', email='busy@x.uz', password='x')
            ExamAttempt.objects.create(organization=org, exam=exam, user=u)
        _refresh(org)
        s = _stat(org, qv)
        assert (s.attempts, s.skipped_count, s.correct_count) == (8, 8, 0)
        assert s.flags == ['OFTEN_SKIPPED']  # not ALL_WRONG: nobody actually answered

    def test_explicit_answer_in_attempt_is_not_implicit_skip(self, db, org, subject):
        qv = _qv(org, subject)
        creator = User.objects.create_user(username='creator', email='creator@x.uz', password='x')
        now = timezone.now()
        with tenant_context(org):
            exam = MockExam.objects.create(
                organization=org,
                title='M',
                duration_minutes=60,
                scheduled_at=now - timedelta(hours=1),
                closes_at=now + timedelta(hours=1),
                status=MockExam.Status.PUBLISHED,
                created_by=creator,
            )
            MockExamQuestion.objects.create(mock_exam=exam, question_version=qv, order=1)
            for i in range(8):
                u = User.objects.create_user(username=f'stu{i}', email=f'stu{i}@x.uz', password='x')
                a = ExamAttempt.objects.create(
                    organization=org, exam=exam, user=u, status=ExamAttempt.Status.SUBMITTED
                )
                UserAnswer.objects.create(
                    organization=org,
                    attempt=a,
                    question_version=qv,
                    selected={'id': 1},
                    is_correct=True,
                )
        _refresh(org)
        s = _stat(org, qv)
        assert (s.attempts, s.skipped_count, s.correct_count) == (8, 0, 8)
        assert s.flags == ['ALL_RIGHT']


# ─── Task / command / idempotency / tenancy ───────────────────────────────────


@pytest.mark.integration
class TestTask:
    def test_task_is_idempotent(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, wrong=8)
        first = refresh_question_quality()
        snap1 = (_stat_count(org), _stat(org, qv).flags, _stat(org, qv).attempts)
        second = refresh_question_quality()
        snap2 = (_stat_count(org), _stat(org, qv).flags, _stat(org, qv).attempts)
        assert snap1 == snap2 == (1, ['ALL_WRONG'], 8)
        assert first['versions'] == second['versions'] == 1

    def test_rerun_picks_up_new_data_and_drops_stale_rows(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, right=8)
        refresh_question_quality()
        assert _stat(org, qv).flags == ['ALL_RIGHT']
        _answers(org, user, qv, wrong=1)
        refresh_question_quality()
        assert _stat(org, qv).flags == []
        UserAnswer.global_objects.filter(question_version=qv).update(is_deleted=True)
        refresh_question_quality()
        assert _stat_count(org) == 0

    def test_org_id_argument_limits_scope(self, db, org, org2, user, subject):
        qv1, qv2 = _qv(org, subject), _qv(org2, subject)
        _answers(org, user, qv1, wrong=8)
        _answers(org2, user, qv2, wrong=8)
        refresh_question_quality(str(org.id))
        assert _stat_count(org) == 1
        assert _stat_count(org2) == 0

    def test_management_command(self, db, org, user, subject):
        qv = _qv(org, subject)
        _answers(org, user, qv, wrong=8)
        out = StringIO()
        call_command('refresh_question_quality', stdout=out)
        assert 'flagged=1' in out.getvalue()
        assert _stat(org, qv).flags == ['ALL_WRONG']

    def test_cross_tenant_isolation(self, db, org, org2, user, subject):
        """The same question version answered in two orgs gets independent stats."""
        shared = _qv(org, subject)
        _answers(org, user, shared, wrong=8)  # org: all wrong
        _answers(org2, user, shared, right=8)  # org2: all right
        refresh_question_quality()
        assert _stat(org, shared).flags == ['ALL_WRONG']
        assert _stat(org2, shared).flags == ['ALL_RIGHT']
        assert _stat(org, shared).attempts == 8
        assert _stat(org2, shared).attempts == 8
        # Per-org scoped manager sees only its own rows.
        with tenant_context(org):
            assert [s.organization_id for s in QuestionQualityStat.objects.all()] == [org.id]


# ─── API ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestAPI:
    URL = 'analytics:question-quality'

    def _client(self, user):
        c = Client()
        c.force_login(user)
        return c

    def _seed(self, org, user, subject):
        wrong = _qv(org, subject)
        right = _qv(org, subject, Question.Type.MULTIPLE_CHOICE)
        skip = _qv(org, Subject.objects.create(name='Other', slug='other-subj'))
        fine = _qv(org, subject)
        _answers(org, user, wrong, wrong=8)
        _answers(org, user, right, right=25)
        _answers(org, user, skip, right=3, skip=7)
        _answers(org, user, fine, right=5, wrong=5)
        refresh_question_quality()
        return wrong, right, skip, fine

    def test_admin_lists_flagged_with_counts(self, db, org, user, admin_member, subject):
        wrong, right, skip, fine = self._seed(org, user, subject)
        resp = self._client(user).get(reverse(self.URL))
        assert resp.status_code == 200
        body = resp.json()
        ids = {r['question_version_id'] for r in body['results']}
        assert ids == {str(wrong.id), str(right.id), str(skip.id)}  # 'fine' not listed
        assert body['counts'] == {'ALL_WRONG': 1, 'ALL_RIGHT': 1, 'OFTEN_SKIPPED': 1}
        row = next(r for r in body['results'] if r['question_version_id'] == str(wrong.id))
        assert row['attempts'] == 8
        assert row['correct_rate'] == 0.0
        assert row['is_preliminary'] is True
        assert row['subject_name'] == subject.name

    def test_filter_by_flag(self, db, org, user, admin_member, subject):
        wrong, *_ = self._seed(org, user, subject)
        body = self._client(user).get(reverse(self.URL), {'flag': 'ALL_WRONG'}).json()
        assert [r['question_version_id'] for r in body['results']] == [str(wrong.id)]
        # counts ignore the flag filter itself
        assert body['counts']['ALL_RIGHT'] == 1

    def test_filter_by_preliminary(self, db, org, user, admin_member, subject):
        _, right, *_ = self._seed(org, user, subject)  # 25 attempts -> not preliminary
        c = self._client(user)
        body = c.get(reverse(self.URL), {'preliminary': 'false'}).json()
        assert [r['question_version_id'] for r in body['results']] == [str(right.id)]
        body = c.get(reverse(self.URL), {'preliminary': 'true'}).json()
        assert len(body['results']) == 2

    def test_filter_by_subject_and_type(self, db, org, user, admin_member, subject):
        wrong, right, skip, _ = self._seed(org, user, subject)
        c = self._client(user)
        body = c.get(reverse(self.URL), {'subject': str(skip.question.subject_id)}).json()
        assert [r['question_version_id'] for r in body['results']] == [str(skip.id)]
        body = c.get(reverse(self.URL), {'question_type': Question.Type.MULTIPLE_CHOICE}).json()
        assert [r['question_version_id'] for r in body['results']] == [str(right.id)]

    @pytest.mark.parametrize(
        'params', [{'flag': 'BOGUS'}, {'preliminary': 'maybe'}, {'subject': 'not-a-uuid'}]
    )
    def test_invalid_filters_rejected(self, db, org, user, admin_member, params):
        assert self._client(user).get(reverse(self.URL), params).status_code == 400

    def test_other_org_data_not_visible(self, db, org, org2, user, admin_member, subject):
        qv2 = _qv(org2, subject)
        _answers(org2, user, qv2, wrong=8)
        refresh_question_quality()
        body = self._client(user).get(reverse(self.URL)).json()
        assert body['results'] == []
        assert sum(body['counts'].values()) == 0

    def test_student_forbidden(self, db, org, user, member, subject):
        """NEGATIVE: a student must not read question quality data."""
        self._seed(org, user, subject)
        resp = self._client(user).get(reverse(self.URL))
        assert resp.status_code == 403

    def test_anonymous_rejected(self, db):
        assert Client().get(reverse(self.URL)).status_code in (401, 403)

    def test_teacher_allowed(self, db, org, user, subject):
        _member(user, org, 'teacher')
        assert self._client(user).get(reverse(self.URL)).status_code == 200
