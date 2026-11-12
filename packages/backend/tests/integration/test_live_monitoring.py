"""Live exam monitoring: who is sitting now, progress, heartbeat age, strikes, isolation."""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.exams.models import AntiCheatEvent, ExamAttempt
from core.tenant import tenant_context

from .engagement_helpers import answer, client, make_attempt, make_exam, make_versions, member

pytestmark = pytest.mark.integration

URL = '/api/v1/engagement/monitoring/live-attempts/'


@pytest.fixture
def world(db, org, org2, subject):
    teacher = member(org, 'teach', 'teacher')
    manager = member(org, 'boss', 'manager')
    qvs = make_versions(org, subject, 4)
    exam = make_exam(org, teacher, qvs, minutes=60, title='Physics')
    return {
        'org': org,
        'org2': org2,
        'teacher': teacher,
        'manager': manager,
        'qvs': qvs,
        'exam': exam,
    }


def _student(org, name):
    return member(org, name, 'student', first_name=name.title(), last_name='Student')


class TestLiveAttempts:
    def test_exact_fields(self, world):
        org = world['org']
        s = _student(org, 'ali')
        att = make_attempt(
            org,
            world['exam'],
            s,
            heartbeat_last_at=timezone.now() - timedelta(seconds=30),
            strikes=2,
        )
        answer(org, att, world['qvs'][0])
        answer(org, att, world['qvs'][1], correct=False)
        base = timezone.now() - timedelta(minutes=5)
        with tenant_context(org):
            for i, kind in enumerate(
                ['tab_switch', 'window_blur', 'copy_attempt', 'devtools_open']
            ):
                ev = AntiCheatEvent.objects.create(organization=org, attempt=att, event_type=kind)
                AntiCheatEvent.global_objects.filter(pk=ev.pk).update(
                    occurred_at=base + timedelta(minutes=i)
                )

        body = client(world['teacher']).get(URL).json()
        assert body['count'] == 1
        row = body['results'][0]
        assert row['student']['username'] == 'ali' and row['student']['name'] == 'Ali Student'
        assert row['exam']['title'] == 'Physics' and row['exam']['duration_minutes'] == 60
        assert row['progress'] == {'answered': 2, 'total': 4}
        assert row['strikes'] == 2
        assert 29 <= row['heartbeat_age_seconds'] <= 40
        # newest three of four, newest first
        assert [e['event_type'] for e in row['latest_events']] == [
            'devtools_open',
            'copy_attempt',
            'window_blur',
        ]

    def test_no_heartbeat_yet(self, world):
        make_attempt(world['org'], world['exam'], _student(world['org'], 'bek'))
        row = client(world['teacher']).get(URL).json()['results'][0]
        assert row['heartbeat_age_seconds'] is None and row['latest_events'] == []
        assert row['progress'] == {'answered': 0, 'total': 4}

    def test_only_in_progress_and_within_deadline(self, world):
        org = world['org']
        live = make_attempt(org, world['exam'], _student(org, 'live'))
        done = make_attempt(
            org, world['exam'], _student(org, 'done'), status=ExamAttempt.Status.SUBMITTED, score=50
        )
        zombie = make_attempt(org, world['exam'], _student(org, 'zombie'))
        # 60 min exam + 5 min grace: started 66 min ago is a zombie, 64 min ago is still live
        ExamAttempt.global_objects.filter(pk=zombie.pk).update(
            started_at=timezone.now() - timedelta(minutes=66)
        )
        edge = make_attempt(org, world['exam'], _student(org, 'edge'))
        ExamAttempt.global_objects.filter(pk=edge.pk).update(
            started_at=timezone.now() - timedelta(minutes=64)
        )
        names = {
            r['student']['username'] for r in client(world['teacher']).get(URL).json()['results']
        }
        assert names == {'live', 'edge'}
        assert done.id and zombie.id and live.id

    def test_deleted_attempt_hidden(self, world):
        att = make_attempt(world['org'], world['exam'], _student(world['org'], 'gone'))
        ExamAttempt.global_objects.filter(pk=att.pk).update(is_deleted=True)
        assert client(world['teacher']).get(URL).json()['count'] == 0

    def test_filters(self, world):
        org = world['org']
        make_attempt(org, world['exam'], _student(org, 'calm'), strikes=0)
        make_attempt(org, world['exam'], _student(org, 'noisy'), strikes=3)
        c = client(world['manager'])
        assert [
            r['student']['username'] for r in c.get(URL, {'min_strikes': 2}).json()['results']
        ] == ['noisy']
        assert c.get(URL, {'exam_id': str(world['exam'].id)}).json()['count'] == 2
        import uuid

        assert c.get(URL, {'exam_id': str(uuid.uuid4())}).json()['count'] == 0
        assert c.get(URL, {'limit': 1}).json()['count'] == 1
        assert c.get(URL, {'limit': 0}).status_code == 400
        assert c.get(URL, {'exam_id': 'nope'}).status_code == 400

    def test_query_count_does_not_grow_with_attempts(self, world, subject):
        org = world['org']
        make_attempt(org, world['exam'], _student(org, 'first'))

        def count_queries():
            c = client(world['teacher'])
            with CaptureQueriesContext(connection) as ctx:
                assert c.get(URL).status_code == 200
            return len(ctx)

        few = count_queries()
        for i in range(8):
            att = make_attempt(org, world['exam'], _student(org, f'n{i}'), strikes=1)
            answer(org, att, world['qvs'][0])
            with tenant_context(org):
                AntiCheatEvent.objects.create(
                    organization=org, attempt=att, event_type='tab_switch'
                )
        many = count_queries()
        assert many == few


class TestPermissionsAndIsolation:
    def test_student_forbidden(self, world):
        s = _student(world['org'], 'stud')
        assert client(s).get(URL).status_code == 403

    def test_anonymous_forbidden(self, world):
        from django.test import Client

        assert Client().get(URL).status_code in (401, 403)

    def test_observer_cannot_monitor(self, world):
        obs = member(world['org'], 'watcher', 'observer')
        assert client(obs).get(URL).status_code == 403

    def test_cross_tenant_attempts_invisible(self, world, subject):
        org2 = world['org2']
        teacher2 = member(org2, 'teach-b', 'teacher')
        exam2 = make_exam(org2, teacher2, make_versions(org2, subject, 2), title='Chemistry')
        make_attempt(org2, exam2, _student(org2, 'foreign'), strikes=5)
        make_attempt(world['org'], world['exam'], _student(world['org'], 'mine'))

        mine = client(world['teacher']).get(URL).json()
        assert [r['student']['username'] for r in mine['results']] == ['mine']
        theirs = client(teacher2).get(URL).json()
        assert [r['student']['username'] for r in theirs['results']] == ['foreign']

    def test_cross_tenant_exam_filter_returns_nothing(self, world, subject):
        org2 = world['org2']
        teacher2 = member(org2, 'teach-b', 'teacher')
        exam2 = make_exam(org2, teacher2, make_versions(org2, subject, 2))
        make_attempt(org2, exam2, _student(org2, 'foreign'))
        r = client(world['teacher']).get(URL, {'exam_id': str(exam2.id)})
        assert r.status_code == 200 and r.json()['count'] == 0
