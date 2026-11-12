"""Group lessons: visibility, permissions, materials, exam reference."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.exams.models import ExamAttempt, MockExam
from apps.organizations.group_models import Group, GroupMember, Lesson
from core.tenant import tenant_context

from .b2b_helpers import client_for, make_user, patch_json, post_json

pytestmark = pytest.mark.integration


def url(name, *args):
    return reverse(f'org-api-v1:{name}', args=args)


@pytest.fixture
def world(db, org, org2):
    w = {
        'owner': make_user('owner1', org, 'owner'),
        'manager': make_user('mgr1', org, 'manager'),
        'observer': make_user('obs1', org, 'observer'),
        'teacher': make_user('teach1', org, 'teacher'),
        'teacher2': make_user('teach2', org, 'teacher'),
        's_in': make_user('stud_in', org, 'student'),
        's_out': make_user('stud_out', org, 'student'),
        'owner2': make_user('owner2', org2, 'owner'),
    }
    with tenant_context(org):
        w['g1'] = Group.objects.create(organization=org, name='G1')
        w['g2'] = Group.objects.create(organization=org, name='G2')
        for user, group, role in [
            (w['teacher'], w['g1'], 'teacher'),
            (w['teacher2'], w['g2'], 'teacher'),
            (w['s_in'], w['g1'], 'student'),
        ]:
            GroupMember.objects.create(organization=org, group=group, user=user, role=role)
        w['l1'] = Lesson.objects.create(
            organization=org, group=w['g1'], title='L1', scheduled_at=timezone.now()
        )
        w['l2'] = Lesson.objects.create(
            organization=org, group=w['g2'], title='L2', scheduled_at=timezone.now()
        )
    with tenant_context(org2):
        w['g_o2'] = Group.objects.create(organization=org2, name='Foreign')
        w['l_o2'] = Lesson.objects.create(
            organization=org2, group=w['g_o2'], title='Foreign lesson', scheduled_at=timezone.now()
        )
    return w


def titles(client, **params):
    return sorted(r['title'] for r in client.get(url('lesson-list'), params).json()['results'])


def lesson_body(group, **over):
    body = {
        'group': str(group.pk),
        'title': 'Yangi dars',
        'topic': 'Kvadrat tenglama',
        'scheduled_at': (timezone.now() + timedelta(days=1)).isoformat(),
        'materials': [
            {'kind': 'link', 'title': 'Video', 'url': 'https://youtu.be/abc'},
            {'kind': 'text', 'title': 'Eslatma', 'body': "Darslikning 12-betini o'qing"},
        ],
    }
    body.update(over)
    return body


class TestVisibility:
    def test_student_sees_only_own_group_lessons(self, world):
        assert titles(client_for(world['s_in'])) == ['L1']

    def test_student_outside_group_sees_nothing(self, world):
        c = client_for(world['s_out'])
        assert titles(c) == []
        assert c.get(url('lesson-detail', world['l1'].pk)).status_code == 404

    def test_student_in_group_cannot_open_other_groups_lesson(self, world):
        assert (
            client_for(world['s_in']).get(url('lesson-detail', world['l2'].pk)).status_code == 404
        )

    def test_teacher_sees_only_own_groups(self, world):
        assert titles(client_for(world['teacher'])) == ['L1']
        assert titles(client_for(world['teacher2'])) == ['L2']
        assert (
            client_for(world['teacher']).get(url('lesson-detail', world['l2'].pk)).status_code
            == 404
        )

    def test_manager_owner_observer_see_all_org_lessons(self, world):
        for key in ('owner', 'manager', 'observer'):
            assert titles(client_for(world[key])) == ['L1', 'L2'], key

    def test_group_filter(self, world):
        assert titles(client_for(world['owner']), group=str(world['g2'].pk)) == ['L2']

    def test_removed_student_loses_access(self, world):
        GroupMember.global_objects.filter(user=world['s_in']).delete()
        assert titles(client_for(world['s_in'])) == []


class TestWritePermissions:
    def test_manager_creates_with_materials(self, world):
        r = post_json(client_for(world['manager']), url('lesson-list'), lesson_body(world['g1']))
        assert r.status_code == 201, r.content
        assert len(r.json()['materials']) == 2

    def test_teacher_creates_in_own_group_only(self, world):
        c = client_for(world['teacher'])
        assert post_json(c, url('lesson-list'), lesson_body(world['g1'])).status_code == 201
        # group of another teacher: not visible to this teacher at all -> validation error
        r = post_json(c, url('lesson-list'), lesson_body(world['g2']))
        assert r.status_code in (400, 403)
        assert not Lesson.global_objects.filter(group=world['g2'], title='Yangi dars').exists()

    def test_teacher_cannot_create_in_visible_group_he_does_not_teach(self, world):
        # visible as a student of g2, but only teaches g1
        GroupMember.global_objects.create(
            organization=world['g2'].organization,
            group=world['g2'],
            user=world['teacher'],
            role='student',
        )
        r = post_json(client_for(world['teacher']), url('lesson-list'), lesson_body(world['g2']))
        assert r.status_code == 403

    @pytest.mark.parametrize('key', ['s_in', 's_out', 'observer'])
    def test_students_and_observers_cannot_write(self, world, key):
        c = client_for(world[key])
        assert post_json(c, url('lesson-list'), lesson_body(world['g1'])).status_code == 403
        assert (
            patch_json(c, url('lesson-detail', world['l1'].pk), {'title': 'x'}).status_code == 403
        )
        assert c.delete(url('lesson-detail', world['l1'].pk)).status_code == 403
        world['l1'].refresh_from_db()
        assert world['l1'].title == 'L1'

    def test_teacher_edit_and_delete_own_but_not_foreign(self, world):
        t = client_for(world['teacher'])
        assert (
            patch_json(t, url('lesson-detail', world['l1'].pk), {'title': 'Yangilandi'}).status_code
            == 200
        )
        assert (
            patch_json(t, url('lesson-detail', world['l2'].pk), {'title': 'Hack'}).status_code
            == 404
        )
        assert t.delete(url('lesson-detail', world['l2'].pk)).status_code == 404
        assert t.delete(url('lesson-detail', world['l1'].pk)).status_code == 204

    def test_lesson_cannot_be_moved_between_groups(self, world):
        r = patch_json(
            client_for(world['owner']),
            url('lesson-detail', world['l1'].pk),
            {'group': str(world['g2'].pk)},
        )
        assert r.status_code == 400


class TestMaterials:
    @pytest.mark.parametrize(
        'materials',
        [
            [{'kind': 'link', 'title': 'x', 'url': 'javascript:alert(1)'}],
            [{'kind': 'link', 'title': 'x', 'url': 'ftp://host/file'}],
            [{'kind': 'link', 'title': 'x'}],
            [{'kind': 'text', 'title': 'x', 'body': ''}],
            [{'kind': 'video', 'title': 'x'}],
            [{'kind': 'link', 'title': 'x', 'url': 'https://a.uz', 'extra': 1}],
            [{'kind': 'link', 'title': '', 'url': 'https://a.uz'}],
            [{'kind': 'link', 'title': 'x', 'url': 'https://a.uz'}] * 21,
            'not-a-list',
        ],
    )
    def test_invalid_materials_rejected(self, world, materials):
        r = post_json(
            client_for(world['owner']),
            url('lesson-list'),
            lesson_body(world['g1'], materials=materials),
        )
        assert r.status_code == 400
        assert not Lesson.global_objects.filter(title='Yangi dars').exists()


class TestExamReference:
    def _mock(self, org, owner, *, public=False):
        now = timezone.now()
        with tenant_context(org):
            return MockExam.objects.create(
                organization=org, title='Mock', duration_minutes=60, scheduled_at=now,
                closes_at=now + timedelta(hours=2), is_public=public, created_by=owner,
            )  # fmt: skip

    def test_assign_own_org_exam_creates_nothing_in_exams(self, world, org):
        mock = self._mock(org, world['owner'])
        mocks_before = MockExam.global_objects.count() if hasattr(MockExam, 'global_objects') else 1
        r = post_json(
            client_for(world['owner']),
            url('lesson-list'),
            lesson_body(world['g1'], exam=str(mock.pk)),
        )
        assert r.status_code == 201
        assert r.json()['exam'] == str(mock.pk) and r.json()['exam_title'] == 'Mock'
        assert ExamAttempt.objects.count() == 0
        if hasattr(MockExam, 'global_objects'):
            assert MockExam.global_objects.count() == mocks_before

    def test_public_platform_exam_allowed(self, world, org2, org):
        mock = self._mock(org2, world['owner2'], public=True)
        r = post_json(
            client_for(world['owner']),
            url('lesson-list'),
            lesson_body(world['g1'], exam=str(mock.pk)),
        )
        assert r.status_code == 201

    def test_other_orgs_private_exam_rejected(self, world, org2):
        mock = self._mock(org2, world['owner2'])
        r = post_json(
            client_for(world['owner']),
            url('lesson-list'),
            lesson_body(world['g1'], exam=str(mock.pk)),
        )
        assert r.status_code == 400

    def test_exam_can_be_cleared(self, world, org):
        mock = self._mock(org, world['owner'])
        c = client_for(world['owner'])
        patch_json(c, url('lesson-detail', world['l1'].pk), {'exam': str(mock.pk)})
        r = patch_json(c, url('lesson-detail', world['l1'].pk), {'exam': None})
        assert r.status_code == 200 and r.json()['exam'] is None


class TestCrossTenant:
    def test_other_org_owner_cannot_read_or_touch(self, world):
        c = client_for(world['owner2'])
        assert titles(c) == ['Foreign lesson']
        assert c.get(url('lesson-detail', world['l1'].pk)).status_code == 404
        assert (
            patch_json(c, url('lesson-detail', world['l1'].pk), {'title': 'x'}).status_code == 404
        )
        assert c.delete(url('lesson-detail', world['l1'].pk)).status_code == 404
        assert c.get(url('group-detail', world['g1'].pk)).status_code == 404

    def test_cannot_create_lesson_in_foreign_group(self, world):
        r = post_json(client_for(world['owner2']), url('lesson-list'), lesson_body(world['g1']))
        assert r.status_code == 400
        assert not Lesson.global_objects.filter(title='Yangi dars').exists()
