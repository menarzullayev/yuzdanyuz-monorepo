"""Groups: CRUD permissions, membership rules, bulk add and CSV import."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.organizations.group_models import Group, GroupMember
from apps.organizations.models import MembershipStatus
from core.tenant import tenant_context

from .b2b_helpers import client_for, make_user, patch_json, post_json

pytestmark = pytest.mark.integration


@pytest.fixture
def world(db, org, org2):
    w = {
        'owner': make_user('owner1', org, 'owner'),
        'manager': make_user('mgr1', org, 'manager'),
        'teacher': make_user('teach1', org, 'teacher'),
        'teacher2': make_user('teach2', org, 'teacher'),
        's1': make_user('stud1', org, 'student'),
        's2': make_user('stud2', org, 'student'),
        's3': make_user('stud3', org, 'student'),
        'outsider': make_user('outsider1'),  # no org
        'other_org_student': make_user('other_stud', org2, 'student'),
    }
    with tenant_context(org):
        w['g1'] = Group.objects.create(organization=org, name='Matematika A')
        w['g2'] = Group.objects.create(organization=org, name='Fizika B')
    return w


def url(name, *args):
    return reverse(f'org-api-v1:{name}', args=args)


def add(c, group, username, role='student'):
    return post_json(c, url('member-list', group.pk), {'username': username, 'role': role})


def members_of(group):
    with tenant_context(group.organization):
        return set(GroupMember.objects.filter(group=group).values_list('user__username', flat=True))


class TestGroupCrudPermissions:
    def test_owner_and_manager_create(self, world):
        for key in ('owner', 'manager'):
            r = post_json(client_for(world[key]), url('group-list'), {'name': f'G-{key}'})
            assert r.status_code == 201, r.content

    @pytest.mark.parametrize('key', ['teacher', 's1', 'outsider'])
    def test_others_cannot_create(self, world, key):
        r = post_json(client_for(world[key]), url('group-list'), {'name': 'Nope'})
        assert r.status_code == 403
        assert not Group.global_objects.filter(name='Nope').exists()

    def test_duplicate_name_in_org_rejected(self, world):
        r = post_json(client_for(world['owner']), url('group-list'), {'name': 'matematika a'})
        assert r.status_code == 400

    def test_same_name_allowed_in_other_org(self, world, org2):
        o2 = make_user('own2', org2, 'owner')
        r = post_json(client_for(o2), url('group-list'), {'name': 'Matematika A'})
        assert r.status_code == 201

    def test_create_with_teachers_and_rollback_on_bad_teacher(self, world):
        c = client_for(world['owner'])
        ok = post_json(c, url('group-list'), {'name': 'T1', 'teacher_usernames': ['teach1']})
        assert ok.status_code == 201
        assert [t['username'] for t in ok.json()['teachers']] == ['teach1']
        bad = post_json(c, url('group-list'), {'name': 'T2', 'teacher_usernames': ['stud1']})
        assert bad.status_code == 400
        assert not Group.global_objects.filter(name='T2').exists()

    def test_edit_and_delete_need_permission(self, world):
        g = world['g1']
        t = client_for(world['teacher'])
        assert patch_json(t, url('group-detail', g.pk), {'name': 'X'}).status_code in (403, 404)
        assert t.delete(url('group-detail', g.pk)).status_code in (403, 404)
        m = client_for(world['manager'])
        assert patch_json(m, url('group-detail', g.pk), {'name': 'Yangi'}).status_code == 200
        assert m.delete(url('group-detail', g.pk)).status_code == 204

    def test_subject_of_other_org_rejected(self, world, org2):
        from apps.catalog.models import Subject

        foreign = Subject.objects.create(name='Priv', slug='priv', organization=org2)
        r = post_json(
            client_for(world['owner']),
            url('group-list'),
            {'name': 'S', 'subject': str(foreign.pk)},
        )
        assert r.status_code == 400


class TestGroupVisibility:
    def test_student_sees_only_own_groups(self, world):
        owner = client_for(world['owner'])
        add(owner, world['g1'], 'stud1')
        c = client_for(world['s1'])
        names = [g['name'] for g in c.get(url('group-list')).json()['results']]
        assert names == ['Matematika A']
        assert c.get(url('group-detail', world['g2'].pk)).status_code == 404

    def test_student_cannot_list_classmates(self, world):
        owner = client_for(world['owner'])
        add(owner, world['g1'], 'stud1')
        add(owner, world['g1'], 'stud2')
        assert client_for(world['s1']).get(url('member-list', world['g1'].pk)).status_code == 404

    def test_teacher_sees_own_group_members_only(self, world):
        owner = client_for(world['owner'])
        add(owner, world['g1'], 'teach1', 'teacher')
        add(owner, world['g1'], 'stud1')
        t = client_for(world['teacher'])
        assert len(t.get(url('member-list', world['g1'].pk)).json()) == 2
        assert t.get(url('member-list', world['g2'].pk)).status_code == 404

    def test_manager_sees_all(self, world):
        r = client_for(world['manager']).get(url('group-list'))
        assert r.json()['count'] == 2


class TestMembershipRules:
    def test_add_org_member_as_student(self, world):
        r = add(client_for(world['owner']), world['g1'], 'stud1')
        assert r.status_code == 201
        assert members_of(world['g1']) == {'stud1'}

    def test_student_in_several_groups_of_same_org(self, world):
        c = client_for(world['owner'])
        assert add(c, world['g1'], 'stud1').status_code == 201
        assert add(c, world['g2'], 'stud1').status_code == 201

    def test_duplicate_member_conflict(self, world):
        c = client_for(world['owner'])
        add(c, world['g1'], 'stud1')
        assert add(c, world['g1'], 'stud1').status_code == 409

    def test_non_member_of_org_cannot_be_added(self, world):
        c = client_for(world['owner'])
        assert add(c, world['g1'], 'outsider1').status_code == 400
        assert add(c, world['g1'], 'other_stud').status_code == 400  # member of ANOTHER org
        assert members_of(world['g1']) == set()

    def test_suspended_member_cannot_be_added(self, world, org):
        make_user('susp', org, 'student', status=MembershipStatus.SUSPENDED)
        assert add(client_for(world['owner']), world['g1'], 'susp').status_code == 400

    def test_unknown_username(self, world):
        assert add(client_for(world['owner']), world['g1'], 'nobody').status_code == 400

    def test_student_role_cannot_teach(self, world):
        c = client_for(world['owner'])
        assert add(c, world['g1'], 'stud1', 'teacher').status_code == 400
        assert add(c, world['g1'], 'teach1', 'teacher').status_code == 201

    @pytest.mark.parametrize('key', ['teacher', 's1'])
    def test_only_member_managers_can_add(self, world, key):
        assert add(client_for(world[key]), world['g1'], 'stud2').status_code == 403
        assert members_of(world['g1']) == set()

    def test_remove_member(self, world):
        c = client_for(world['owner'])
        add(c, world['g1'], 'stud1')
        m = GroupMember.global_objects.get(group=world['g1'])
        assert c.delete(url('member-detail', world['g1'].pk, m.pk)).status_code == 204
        assert members_of(world['g1']) == set()

    def test_cannot_add_to_other_orgs_group(self, world, org2):
        o2 = make_user('own2', org2, 'owner')
        make_user('stud_o2', org2, 'student')
        r = add(client_for(o2), world['g1'], 'stud_o2')
        assert r.status_code == 404
        assert members_of(world['g1']) == set()


class TestBulkUsernames:
    def test_partial_success_reports_each_row(self, world):
        c = client_for(world['owner'])
        r = post_json(
            c,
            url('member-bulk', world['g1'].pk),
            {'usernames': ['stud1', 'outsider1', 'ghost', 'STUD2', 'stud1']},
        )
        body = r.json()
        assert r.status_code == 200 and body['committed'] is True
        assert body['summary'] == {'total': 5, 'added': 2, 'already_member': 0, 'errors': 3}
        statuses = [x['status'] for x in body['rows']]
        assert statuses == ['added', 'error', 'error', 'added', 'error']
        assert members_of(world['g1']) == {'stud1', 'stud2'}

    def test_all_or_nothing_rolls_back(self, world):
        r = post_json(
            client_for(world['owner']),
            url('member-bulk', world['g1'].pk),
            {'usernames': ['stud1', 'ghost'], 'all_or_nothing': True},
        )
        assert r.status_code == 422
        assert r.json()['committed'] is False
        assert members_of(world['g1']) == set()

    def test_all_or_nothing_commits_when_clean(self, world):
        r = post_json(
            client_for(world['owner']),
            url('member-bulk', world['g1'].pk),
            {'usernames': ['stud1', 'stud2'], 'all_or_nothing': True},
        )
        assert r.status_code == 200 and members_of(world['g1']) == {'stud1', 'stud2'}

    def test_rerun_is_idempotent(self, world):
        c = client_for(world['owner'])
        body = {'usernames': ['stud1']}
        post_json(c, url('member-bulk', world['g1'].pk), body)
        r = post_json(c, url('member-bulk', world['g1'].pk), body).json()
        assert r['rows'][0]['status'] == 'already_member' and r['summary']['errors'] == 0

    def test_limits_and_permission(self, world):
        c = client_for(world['owner'])
        assert (
            post_json(c, url('member-bulk', world['g1'].pk), {'usernames': []}).status_code == 400
        )
        too_many = {'usernames': [f'u{i}' for i in range(501)]}
        assert post_json(c, url('member-bulk', world['g1'].pk), too_many).status_code == 400
        t = client_for(world['teacher'])
        assert (
            post_json(t, url('member-bulk', world['g1'].pk), {'usernames': ['stud1']}).status_code
            == 403
        )


def csv_file(text, name='students.csv', raw=None):
    return SimpleUploadedFile(name, raw if raw is not None else text.encode(), 'text/csv')


class TestCsvImport:
    def post(self, world, f, **extra):
        return client_for(world['owner']).post(
            url('member-import-csv', world['g1'].pk), {'file': f, **extra}
        )

    def test_happy_path(self, world):
        r = self.post(world, csv_file('username,full_name\nstud1,A\nstud2,B\nstud3,C\n'))
        body = r.json()
        assert r.status_code == 200
        assert body['summary'] == {'total': 3, 'added': 3, 'already_member': 0, 'errors': 0}
        assert members_of(world['g1']) == {'stud1', 'stud2', 'stud3'}

    def test_bom_semicolon_and_header_case(self, world):
        raw = '﻿Username;Name\r\nstud1;A\r\nstud2;B\r\n'.encode()
        r = self.post(world, csv_file('', raw=raw))
        assert r.status_code == 200 and r.json()['summary']['added'] == 2

    def test_per_row_errors_with_line_numbers(self, world):
        text = (
            'username\n'
            'stud1\n'  # line 2 ok
            'ghost\n'  # line 3 unknown
            'outsider1\n'  # line 4 not org member
            'bad name!\n'  # line 5 invalid
            'stud1\n'  # line 6 duplicate
            ',x\n'  # line 7 empty username with other columns
        )
        r = self.post(world, csv_file(text))
        body = r.json()
        assert r.status_code == 200
        by_row = {x['row']: x for x in body['rows']}
        assert by_row[2]['status'] == 'added'
        assert 'empty' in by_row[7]['error']
        assert 'not found' in by_row[3]['error']
        assert 'not an active member' in by_row[4]['error']
        assert 'invalid' in by_row[5]['error']
        assert 'duplicate of row 2' in by_row[6]['error']
        assert body['summary']['added'] == 1
        assert members_of(world['g1']) == {'stud1'}

    def test_all_or_nothing_with_one_bad_row_creates_nothing(self, world):
        r = self.post(world, csv_file('username\nstud1\nstud2\nghost\n'), all_or_nothing='true')
        assert r.status_code == 422
        body = r.json()
        assert body['committed'] is False
        assert [x['status'] for x in body['rows']] == ['skipped', 'skipped', 'error']
        assert members_of(world['g1']) == set()

    def test_already_member_is_not_an_error(self, world):
        post_json(
            client_for(world['owner']), url('member-bulk', world['g1'].pk), {'usernames': ['stud1']}
        )
        r = self.post(world, csv_file('username\nstud1\nstud2\n'), all_or_nothing='true')
        assert r.status_code == 200
        assert r.json()['summary'] == {'total': 2, 'added': 1, 'already_member': 1, 'errors': 0}

    @pytest.mark.parametrize(
        'f',
        [
            csv_file('login\nstud1\n'),  # no username header
            csv_file('', raw=b''),  # empty
            csv_file('', raw=b'username\n\xff\xfe\x00bad\n'),  # not utf-8
            csv_file('username\n'),  # header only
            csv_file('username\n' + '\n'.join(f'u{i}' for i in range(501))),  # too many rows
            csv_file('', raw=b'username\n' + b'a' * (600 * 1024)),  # too large
        ],
        ids=['no-header', 'empty', 'not-utf8', 'header-only', 'too-many', 'too-large'],
    )
    def test_file_level_errors_reject_everything(self, world, f):
        r = self.post(world, f)
        assert r.status_code == 400
        assert members_of(world['g1']) == set()

    def test_missing_file(self, world):
        r = client_for(world['owner']).post(url('member-import-csv', world['g1'].pk), {})
        assert r.status_code == 400

    def test_teacher_cannot_import(self, world):
        r = client_for(world['teacher']).post(
            url('member-import-csv', world['g1'].pk), {'file': csv_file('username\nstud1\n')}
        )
        assert r.status_code == 403
        assert members_of(world['g1']) == set()

    def test_csv_cannot_smuggle_outsiders_into_another_orgs_group(self, world, org2):
        o2 = make_user('own2', org2, 'owner')
        r = client_for(o2).post(
            url('member-import-csv', world['g1'].pk), {'file': csv_file('username\nother_stud\n')}
        )
        assert r.status_code == 404
