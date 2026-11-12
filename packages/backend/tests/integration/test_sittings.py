"""Exam sittings: state machine, Rasch scoring via the API, publish/certificates, isolation."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Question, QuestionVersion
from apps.exams.models import (
    Certificate,
    ExamAttempt,
    ExamSitting,
    MockExam,
    MockExamQuestion,
    SittingResult,
    UserAnswer,
)
from apps.exams.sitting_models import add_years
from apps.exams.sitting_service import certificate_issued
from apps.organizations.models import Membership, MembershipStatus, OrgRole
from core.tenant import tenant_context, unscoped_context
from tests.integration._onboarding import complete_onboarding

User = get_user_model()
N_QUESTIONS = 6
# correct answers per student: spread of raw scores incl. zero and perfect
CORRECT_COUNTS = [6, 5, 5, 4, 4, 3, 2, 1, 0]

pytestmark = pytest.mark.integration


def _member(org, username, role_name, onboarded=False, **extra):
    u = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='pass123', **extra
    )
    role = OrgRole.objects.get(organization=org, name=role_name)
    m = Membership.objects.create(
        user=u, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    if onboarded:
        complete_onboarding(u)
    return u


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _make_exam(org, creator, subject):
    qvs = []
    with tenant_context(org):
        for _i in range(N_QUESTIONS):
            q = Question.objects.create(
                organization=org, subject=subject, type=Question.Type.SINGLE_CHOICE
            )
            qvs.append(
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
        now = timezone.now()
        mock = MockExam.objects.create(
            organization=org,
            title='Sitting mock',
            duration_minutes=60,
            scheduled_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=2),
            status=MockExam.Status.PUBLISHED,
            created_by=creator,
        )
        for i, qv in enumerate(qvs, start=1):
            MockExamQuestion.objects.create(mock_exam=mock, question_version=qv, order=i)
    return mock, qvs


def _submit_attempt(org, mock, qvs, user, n_correct, *, status=ExamAttempt.Status.SUBMITTED):
    with tenant_context(org):
        attempt = ExamAttempt.objects.create(
            exam=mock, user=user, organization=org, status=status, submitted_at=timezone.now()
        )
        for i, qv in enumerate(qvs):
            UserAnswer.objects.create(
                organization=org,
                attempt=attempt,
                question_version=qv,
                selected={'id': 1 if i < n_correct else 2},
                is_correct=i < n_correct,
            )
    return attempt


class Env:
    """org + admin + one student per entry of CORRECT_COUNTS + an open->closed sitting."""

    def __init__(self, org, subject, onboarded=True):
        self.org = org
        self.admin = _member(org, 'admin1', 'owner', first_name='Ad', last_name='Min')
        self.mock, self.qvs = _make_exam(org, self.admin, subject)
        self.students = [
            _member(
                org,
                f'stud{i}',
                'student',
                onboarded=onboarded,
                first_name=f'Name{i}',
                last_name=f'Fam{i}',
            )
            for i in range(len(CORRECT_COUNTS))
        ]
        self.attempts = [
            _submit_attempt(org, self.mock, self.qvs, s, k)
            for s, k in zip(self.students, CORRECT_COUNTS)
        ]
        self.admin_client = _client(self.admin)

    def create_sitting(self):
        now = timezone.now()
        resp = self.admin_client.post(
            reverse('exams:sitting-list'),
            {
                'mock_exam': str(self.mock.id),
                'title': 'Nov sitting',
                'opens_at': (now - timedelta(hours=1)).isoformat(),
                'closes_at': (now + timedelta(hours=1)).isoformat(),
            },
            content_type='application/json',
        )
        assert resp.status_code == 201, resp.content
        self.sitting_id = resp.json()['id']
        return self.sitting_id

    def act(self, action, client=None):
        return (client or self.admin_client).post(
            reverse(f'exams:sitting-{action}', args=[self.sitting_id])
        )

    def run_to(self, target):
        self.create_sitting()
        for step in ('open', 'close', 'calculate', 'publish'):
            if target == 'draft':
                break
            assert self.act(step).status_code == 200
            if step == target:
                break


@pytest.fixture
def env(db, org, subject):
    return Env(org, subject)


class TestStateMachine:
    def test_full_happy_path(self, env):
        env.create_sitting()
        statuses = []
        for step in ('open', 'close', 'calculate', 'publish'):
            resp = env.act(step)
            assert resp.status_code == 200, resp.content
            statuses.append(resp.json()['status'])
        assert statuses == ['open', 'closed', 'calculated', 'published']

    @pytest.mark.parametrize(
        ('prepare_to', 'action'),
        [
            ('draft', 'calculate'),
            ('draft', 'close'),
            ('draft', 'publish'),
            ('open', 'calculate'),  # cannot calculate an open sitting
            ('open', 'open'),
            ('open', 'publish'),
            ('closed', 'publish'),  # cannot publish before calculate
            ('closed', 'open'),
            ('calculated', 'open'),
            ('calculated', 'close'),
            ('published', 'calculate'),
            ('published', 'publish'),
            ('published', 'close'),
        ],
    )
    def test_illegal_transitions_return_409(self, env, prepare_to, action):
        env.run_to(prepare_to)
        with tenant_context(env.org):
            before = ExamSitting.objects.get(pk=env.sitting_id).status
        resp = env.act(action)
        assert resp.status_code == 409
        with tenant_context(env.org):
            assert ExamSitting.objects.get(pk=env.sitting_id).status == before

    def test_calculate_without_attempts_is_409(self, db, org, subject):
        admin = _member(org, 'admin2', 'owner')
        mock, _ = _make_exam(org, admin, subject)
        client = _client(admin)
        now = timezone.now()
        resp = client.post(
            reverse('exams:sitting-list'),
            {
                'mock_exam': str(mock.id),
                'title': 'Empty',
                'opens_at': (now - timedelta(hours=1)).isoformat(),
                'closes_at': (now + timedelta(hours=1)).isoformat(),
            },
            content_type='application/json',
        )
        sid = resp.json()['id']
        for a in ('open', 'close'):
            client.post(reverse(f'exams:sitting-{a}', args=[sid]))
        assert client.post(reverse('exams:sitting-calculate', args=[sid])).status_code == 409

    def test_window_must_be_ordered(self, env):
        now = timezone.now()
        resp = env.admin_client.post(
            reverse('exams:sitting-list'),
            {
                'mock_exam': str(env.mock.id),
                'title': 'Bad',
                'opens_at': now.isoformat(),
                'closes_at': (now - timedelta(hours=1)).isoformat(),
            },
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_second_sitting_for_same_mock_rejected(self, env):
        env.create_sitting()
        now = timezone.now()
        resp = env.admin_client.post(
            reverse('exams:sitting-list'),
            {
                'mock_exam': str(env.mock.id),
                'title': 'Dup',
                'opens_at': now.isoformat(),
                'closes_at': (now + timedelta(hours=1)).isoformat(),
            },
            content_type='application/json',
        )
        assert resp.status_code == 400

    def test_student_cannot_manage_sittings(self, env):
        env.create_sitting()
        student = _client(env.students[0])
        assert student.get(reverse('exams:sitting-list')).status_code == 403
        assert env.act('open', client=student).status_code == 403
        assert env.act('publish', client=student).status_code == 403


class TestCalculate:
    def test_results_follow_raw_score(self, env):
        env.run_to('calculate')
        with tenant_context(env.org):
            results = list(SittingResult.objects.filter(sitting_id=env.sitting_id))
            sitting = ExamSitting.objects.get(pk=env.sitting_id)
            assert len(results) == len(CORRECT_COUNTS)
            by_raw = {}
            for r in results:
                assert r.max_raw_score == N_QUESTIONS
                by_raw.setdefault(r.raw_score, set()).add((r.ability, r.scaled_score, r.grade))
            assert sorted(by_raw) == sorted(set(CORRECT_COUNTS))
            assert all(len(v) == 1 for v in by_raw.values())
            ordered = [next(iter(by_raw[k])) for k in sorted(by_raw)]
            assert [o[0] for o in ordered] == sorted(o[0] for o in ordered)
            assert [o[1] for o in ordered] == sorted(o[1] for o in ordered)
            assert all(0 <= r.scaled_score <= 75 for r in results)
            assert sitting.items.count() == N_QUESTIONS
            assert sitting.calibration['converged'] is True
            for r in results:
                assert r.certificate_eligible == (r.scaled_score >= 46)

    def test_recalculate_is_idempotent(self, env):
        env.run_to('calculate')
        assert env.act('calculate').status_code == 200
        with tenant_context(env.org):
            assert SittingResult.objects.filter(sitting_id=env.sitting_id).count() == len(
                CORRECT_COUNTS
            )

    def test_only_submitted_in_window_attempts_count(self, db, org, subject):
        env = Env(org, subject)
        extra_in_progress = _member(org, 'late1', 'student')
        _submit_attempt(
            org, env.mock, env.qvs, extra_in_progress, 3, status=ExamAttempt.Status.IN_PROGRESS
        )
        early = _member(org, 'early1', 'student')
        att = _submit_attempt(org, env.mock, env.qvs, early, 3)
        with tenant_context(org):
            ExamAttempt.objects.filter(pk=att.pk).update(
                started_at=timezone.now() - timedelta(days=3)
            )
        env.run_to('calculate')
        with tenant_context(org):
            assert SittingResult.objects.filter(sitting_id=env.sitting_id).count() == len(
                CORRECT_COUNTS
            )

    def test_item_difficulty_reflects_correct_counts(self, env):
        env.run_to('calculate')
        with tenant_context(env.org):
            items = list(ExamSitting.objects.get(pk=env.sitting_id).items.all())
        # earlier questions were answered right by more students -> easier
        items.sort(key=lambda i: -i.correct_count)
        diffs = [i.difficulty for i in items]
        assert diffs == sorted(diffs)


class TestVisibilityAndCertificates:
    def test_results_hidden_before_publish(self, env):
        env.run_to('calculate')
        student = _client(env.students[0])
        assert (
            student.get(reverse('exams:sitting-my-result', args=[env.sitting_id])).status_code
            == 404
        )
        assert student.get(reverse('exams:sitting-mine')).json() == []
        assert student.get(reverse('exams:certificate-mine')).json() == []
        with tenant_context(env.org):
            assert Certificate.objects.count() == 0

    def test_results_visible_after_publish_without_internal_fields(self, env):
        env.run_to('publish')
        student = _client(env.students[0])  # 6/6
        resp = student.get(reverse('exams:sitting-my-result', args=[env.sitting_id]))
        assert resp.status_code == 200
        body = resp.json()
        assert body['raw_score'] == N_QUESTIONS
        assert body['grade'] != ''
        assert body['certificate_number']
        assert 'ability' not in body and 'ability_se' not in body
        assert len(student.get(reverse('exams:sitting-mine')).json()) == 1

    def test_student_sees_only_own_results(self, env):
        env.run_to('publish')
        body = _client(env.students[1]).get(reverse('exams:sitting-mine')).json()
        assert len(body) == 1 and body[0]['raw_score'] == CORRECT_COUNTS[1]

    def test_certificates_only_for_c_and_above(self, env):
        env.run_to('publish')
        with tenant_context(env.org):
            certs = list(Certificate.objects.all())
            eligible = SittingResult.objects.filter(
                sitting_id=env.sitting_id, scaled_score__gte=46
            ).count()
            assert certs and len(certs) == eligible
            assert len(certs) < len(CORRECT_COUNTS)  # the zero-score student got none
            for c in certs:
                assert c.scaled_score >= 46 and c.grade in {'C', 'C+', 'B', 'B+', 'A', 'A+'}
                assert re.fullmatch(r'YD-\d{4}-[A-Z2-9]{8}', c.number)
                assert c.revoked_at is None
            assert len({c.number for c in certs}) == len(certs)

    def test_certificate_valid_for_three_years(self, env):
        env.run_to('publish')
        with tenant_context(env.org):
            c = Certificate.objects.first()
        assert c.valid_until == add_years(c.issued_at, 3)
        assert c.valid_until.year == c.issued_at.year + 3
        assert c.status == 'valid'

    def test_zero_score_student_gets_no_certificate(self, env):
        env.run_to('publish')
        zero = _client(env.students[-1])
        assert zero.get(reverse('exams:certificate-mine')).json() == []
        body = zero.get(reverse('exams:sitting-my-result', args=[env.sitting_id])).json()
        assert body['grade'] == '' and body['certificate_number'] is None

    def test_publish_emits_signal_per_certificate_after_commit(
        self, env, django_capture_on_commit_callbacks
    ):
        received = []

        def handler(sender, certificate, **kw):
            received.append(certificate.number)

        certificate_issued.connect(handler)
        try:
            env.run_to('calculate')
            assert received == []
            with django_capture_on_commit_callbacks(execute=True):
                assert env.act('publish').status_code == 200
        finally:
            certificate_issued.disconnect(handler)
        with tenant_context(env.org):
            assert sorted(received) == sorted(Certificate.objects.values_list('number', flat=True))


class TestPublicVerification:
    def _first_cert(self, env):
        env.run_to('publish')
        with tenant_context(env.org):
            return Certificate.objects.select_related('user').order_by('-scaled_score').first()

    def test_valid_certificate(self, env):
        cert = self._first_cert(env)
        resp = Client().get(reverse('exams:certificate-verify', args=[cert.number]))
        assert resp.status_code == 200
        body = resp.json()
        assert body['valid'] is True and body['status'] == 'valid'
        assert body['grade'] == cert.grade
        assert body['holder_initials'] == 'N.F.'
        # no personal data beyond initials/grade/validity
        assert set(body) == {
            'valid',
            'status',
            'number',
            'holder_initials',
            'grade',
            'issued_at',
            'valid_until',
        }
        raw = resp.content.decode()
        for secret in (cert.user.email, cert.user.username, 'Name0', 'Fam0'):
            assert secret not in raw

    def test_number_lookup_is_case_insensitive(self, env):
        cert = self._first_cert(env)
        resp = Client().get(reverse('exams:certificate-verify', args=[cert.number.lower()]))
        assert resp.json()['valid'] is True

    def test_unknown_number_404(self, env):
        resp = Client().get(reverse('exams:certificate-verify', args=['YD-2026-ZZZZZZZZ']))
        assert resp.status_code == 404
        assert resp.json() == {'valid': False, 'status': 'not_found'}

    def test_revoked_certificate_fails_verification(self, env):
        cert = self._first_cert(env)
        resp = env.admin_client.post(
            reverse('exams:certificate-revoke', args=[cert.id]), {'reason': 'fraud'}
        )
        assert resp.status_code == 200
        body = Client().get(reverse('exams:certificate-verify', args=[cert.number])).json()
        assert body['valid'] is False and body['status'] == 'revoked'
        assert 'holder_initials' not in body and 'grade' not in body

    def test_expired_certificate_is_not_valid(self, env):
        cert = self._first_cert(env)
        with unscoped_context():
            Certificate.objects.filter(pk=cert.pk).update(
                valid_until=timezone.now() - timedelta(days=1)
            )
        body = Client().get(reverse('exams:certificate-verify', args=[cert.number])).json()
        assert body['valid'] is False and body['status'] == 'expired'

    def test_student_cannot_revoke(self, env):
        cert = self._first_cert(env)
        resp = _client(env.students[0]).post(reverse('exams:certificate-revoke', args=[cert.id]))
        assert resp.status_code == 403
        with tenant_context(env.org):
            assert Certificate.objects.get(pk=cert.pk).revoked_at is None


class TestCrossTenant:
    @pytest.fixture
    def other(self, org2):
        return _member(org2, 'otheradmin', 'owner'), _member(
            org2, 'otherstudent', 'student', onboarded=True
        )

    def test_other_org_cannot_see_or_drive_sitting(self, env, other):
        env.run_to('calculate')
        admin2, student2 = other
        c2 = _client(admin2)
        assert c2.get(reverse('exams:sitting-list')).json() == []
        for name in ('sitting-detail', 'sitting-results'):
            assert c2.get(reverse(f'exams:{name}', args=[env.sitting_id])).status_code == 404
        assert env.act('publish', client=c2).status_code == 404
        with tenant_context(env.org):
            assert ExamSitting.objects.get(pk=env.sitting_id).status == 'calculated'

    def test_other_org_cannot_create_sitting_for_foreign_mock(self, env, other):
        admin2, _ = other
        now = timezone.now()
        resp = _client(admin2).post(
            reverse('exams:sitting-list'),
            {
                'mock_exam': str(env.mock.id),
                'title': 'Steal',
                'opens_at': now.isoformat(),
                'closes_at': (now + timedelta(hours=1)).isoformat(),
            },
            content_type='application/json',
        )
        assert resp.status_code == 400
        with unscoped_context():
            assert ExamSitting.objects.count() == 0

    def test_other_org_student_cannot_read_results_or_certificates(self, env, other):
        env.run_to('publish')
        _, student2 = other
        c = _client(student2)
        assert c.get(reverse('exams:sitting-my-result', args=[env.sitting_id])).status_code == 404
        assert c.get(reverse('exams:sitting-mine')).json() == []
        assert c.get(reverse('exams:certificate-mine')).json() == []

    def test_orm_isolation(self, env, org2):
        env.run_to('publish')
        with tenant_context(org2):
            assert ExamSitting.objects.count() == 0
            assert SittingResult.objects.count() == 0
            assert Certificate.objects.count() == 0
        with tenant_context(env.org):
            assert ExamSitting.objects.count() == 1
            assert Certificate.objects.count() > 0

    def test_other_org_cannot_revoke_certificate(self, env, other):
        env.run_to('publish')
        with tenant_context(env.org):
            cert = Certificate.objects.first()
        resp = _client(other[0]).post(reverse('exams:certificate-revoke', args=[cert.id]))
        assert resp.status_code == 404
        with tenant_context(env.org):
            assert Certificate.objects.get(pk=cert.pk).revoked_at is None
