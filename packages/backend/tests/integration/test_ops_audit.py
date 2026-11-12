"""Audit journal over simple-history: scope (staff vs org admin), filters, diff, counter, CSV."""

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.ops.models import SecurityEvent
from apps.ops.services import audit
from apps.organizations.models import Membership, MembershipStatus, Organization, OrgRole
from tests.integration._ops import client_for, staff_user

pytestmark = pytest.mark.integration
User = get_user_model()
URL = '/api/v1/ops/audit/'
EXPORT = '/api/v1/ops/audit/export/'


def _add_member(org, username, role_name):
    u = User.objects.create_user(username=username, password='pass123')
    role = OrgRole.objects.get(organization=org, name=role_name)
    m = Membership.objects.create(
        user=u, organization=org, role=role, status=MembershipStatus.ACTIVE, is_primary=True
    )
    m.activate()
    return u, m


@pytest.fixture
def staff(db):
    return staff_user()


@pytest.fixture
def two_orgs(db):
    a = Organization.objects.create(name='Org A', slug='org-a')
    b = Organization.objects.create(name='Org B', slug='org-b')
    return a, b


def _rows(resp):
    assert resp.status_code == 200, resp.content
    return resp.json()['results']


class TestDiscovery:
    def test_all_history_models_are_discovered(self, db):
        labels = set(audit.audited_models())
        assert {
            'accounts.customuser',
            'catalog.question',
            'commerce.wallet',
            'commerce.subscriptionplan',
            'commerce.organizationsubscription',
            'exams.examattempt',
            'organizations.organization',
            'organizations.orgrole',
            'organizations.membership',
        } <= labels
        assert 'organizations.orgmodelwithoutaudit' not in labels


class TestStaffJournal:
    def test_actions_diff_and_actor(self, staff, two_orgs):
        org, _ = two_orgs
        org._history_user = staff
        org.name = 'Org A renamed'
        org.save()
        role = OrgRole.objects.create(organization=org, name='tmp', display_name='Tmp')
        role._history_user = staff
        role.delete()
        rows = _rows(client_for(staff).get(URL))
        changed = next(
            r
            for r in rows
            if r['action'] == 'changed' and r['model'] == 'organizations.organization'
        )
        assert changed['user'] == {'id': str(staff.pk), 'username': staff.username}
        assert changed['object'] == 'Org A renamed'
        assert {'field': 'name', 'old': 'Org A', 'new': 'Org A renamed'} in changed['changes']
        assert changed['ip'] is None and changed['when'].endswith('+05:00')
        assert {r['action'] for r in rows} >= {'created', 'changed', 'deleted'}
        deleted = next(r for r in rows if r['action'] == 'deleted')
        assert deleted['model'] == 'organizations.orgrole' and deleted['changes'] == []

    def test_sensitive_fields_are_hidden(self, staff, two_orgs):
        org, _ = two_orgs
        u = User.objects.create_user(username='sens', password='pass123')
        u.telegram_id = 5
        u.save()
        rows = _rows(client_for(staff).get(URL, {'model': 'accounts.customuser'}))
        text = str(rows)
        assert 'pbkdf2' not in text  # password hashes are never exposed

    def test_filters(self, staff, two_orgs):
        a, b = two_orgs
        a._history_user = staff
        a.name = 'A2'
        a.save()
        c = client_for(staff)
        assert all(
            r['model'] == 'organizations.organization'
            for r in _rows(c.get(URL, {'model': 'organizations.organization'}))
        )
        assert all(r['action'] == 'changed' for r in _rows(c.get(URL, {'action': 'changed'})))
        assert all(r['user']['id'] == str(staff.pk) for r in _rows(c.get(URL, {'user': staff.pk})))
        only_a = _rows(c.get(URL, {'org': str(a.id), 'model': 'organizations.organization'}))
        assert {r['object_id'] for r in only_a} == {str(a.id)}
        assert _rows(c.get(URL, {'date_from': '2999-01-01'})) == []
        assert _rows(c.get(URL, {'date_to': '2000-01-01'})) == []
        assert _rows(c.get(URL, {'date_from': '2000-01-01', 'date_to': '2999-01-01'})) != []

    @pytest.mark.parametrize(
        'params',
        [
            {'model': 'nope.nope'},
            {'action': 'exploded'},
            {'user': '123'},
            {'org': 'not-a-uuid'},
            {'date_from': '12/31/2026'},
            {'date_to': '2026-13-45'},
            {'page': 'x'},
        ],
    )
    def test_bad_filters_rejected(self, staff, params):
        assert client_for(staff).get(URL, params).status_code == 400

    def test_pagination_merges_models_newest_first(self, staff, two_orgs):
        a, _ = two_orgs
        for i in range(5):
            a.name = f'N{i}'
            a.save()
        resp = client_for(staff).get(URL, {'page_size': 3}).json()
        assert len(resp['results']) == 3 and resp['count'] >= 7
        whens = [r['when'] for r in resp['results']]
        assert whens == sorted(whens, reverse=True)
        page2 = client_for(staff).get(URL, {'page_size': 3, 'page': 2}).json()['results']
        assert not {r['id'] for r in page2} & {r['id'] for r in resp['results']}

    def test_non_staff_non_admin_refused(self, db, user, member):
        assert client_for(user).get(URL).status_code == 403  # plain student
        from django.test import Client

        assert Client().get(URL).status_code in (401, 403)


class TestOrgAdminScope:
    def test_sees_only_own_org(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'owner-a', 'owner')
        _add_member(b, 'owner-b', 'owner')
        c = client_for(owner_a)
        rows = _rows(c.get(URL))
        assert rows
        for r in rows:
            if r['model'] == 'organizations.organization':
                assert r['object_id'] == str(a.id)
        text = str(rows)
        assert 'owner-b' not in text and 'Org B' not in text

    def test_other_org_and_garbage_org_refused(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'owner-a', 'owner')
        c = client_for(owner_a)
        assert c.get(URL, {'org': str(b.id)}).status_code == 403
        assert c.get(URL, {'org': str(uuid.uuid4())}).status_code == 403
        assert c.get(EXPORT, {'org': str(b.id)}).status_code == 403
        assert c.get(URL, {'org': str(a.id)}).status_code == 200

    def test_regular_member_and_teacher_refused(self, two_orgs):
        a, _ = two_orgs
        student, _ = _add_member(a, 'stud', 'student')
        teacher, _ = _add_member(a, 'teach', 'teacher')
        assert client_for(student).get(URL).status_code == 403
        assert client_for(teacher).get(URL).status_code == 403

    def test_platform_only_models_hidden(self, two_orgs):
        a, _ = two_orgs
        owner, _ = _add_member(a, 'owner-a', 'owner')
        models = {r['model'] for r in _rows(client_for(owner).get(URL, {'page_size': 100}))}
        assert not models & {'accounts.customuser', 'commerce.wallet', 'commerce.subscriptionplan'}
        assert client_for(owner).get(URL, {'model': 'accounts.customuser'}).json()['count'] == 0

    def test_membership_changes_scoped_to_org(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'owner-a', 'owner')
        _, mb = _add_member(b, 'stud-b', 'student')
        mb.role = OrgRole.objects.get(organization=b, name='teacher')
        mb.save()
        rows = _rows(
            client_for(owner_a).get(URL, {'model': 'organizations.membership', 'page_size': 100})
        )
        assert all('stud-b' not in str(r) for r in rows)
        assert len(rows) >= 1  # owner-a's own membership rows

    def test_multi_org_admin_must_choose(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'multi', 'owner')
        role_b = OrgRole.objects.get(organization=b, name='manager')
        Membership.objects.create(
            user=owner_a, organization=b, role=role_b, status=MembershipStatus.ACTIVE
        )
        c = client_for(owner_a)
        assert c.get(URL).status_code == 400
        assert c.get(URL, {'org': str(b.id)}).status_code == 200


class TestDangerousCounter:
    def test_counts_deletes_role_changes_password_resets(self, staff, two_orgs):
        a, _ = two_orgs
        base = client_for(staff).get(URL).json()['dangerous_today']
        assert base['total'] == 0
        role = OrgRole.objects.create(organization=a, name='tmp', display_name='Tmp')
        role.delete()
        _, m = _add_member(a, 'worker', 'student')
        m.role = OrgRole.objects.get(organization=a, name='teacher')
        m.save()
        u = User.objects.create_user(username='resetme', password='pass123')
        SecurityEvent.objects.create(event_type='password_reset', source='ip:1.1.1.1', user=u)
        d = client_for(staff).get(URL).json()['dangerous_today']
        assert d['deletes'] == 1
        assert d['role_changes'] == 1
        assert d['password_resets'] == 1
        assert d['payment_state_changes'] == 0
        assert d['total'] == 3

    def test_plain_edit_is_not_dangerous(self, staff, two_orgs):
        a, _ = two_orgs
        a.name = 'Just a rename'
        a.save()
        assert client_for(staff).get(URL).json()['dangerous_today']['total'] == 0

    def test_org_admin_counter_is_scoped(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'owner-a', 'owner')
        OrgRole.objects.create(organization=b, name='tmp', display_name='Tmp').delete()
        u = User.objects.create_user(username='elsewhere', password='pass123')
        SecurityEvent.objects.create(event_type='password_reset', source='ip:1.1.1.1', user=u)
        d = client_for(owner_a).get(URL).json()['dangerous_today']
        assert d['deletes'] == 0 and d['password_resets'] == 0


class TestCsvExport:
    def test_bom_utf8_and_headers(self, staff, two_orgs):
        a, _ = two_orgs
        a.name = 'Тошкент'
        a.save()
        resp = client_for(staff).get(EXPORT)
        assert resp.status_code == 200
        raw = resp.content
        assert raw.startswith(b'\xef\xbb\xbf')
        text = raw.decode('utf-8-sig')
        assert text.splitlines()[0] == 'when,user,action,model,object,object_id,changes,ip'
        assert 'Тошкент' in text
        assert 'attachment' in resp['Content-Disposition']

    @pytest.mark.parametrize('payload', ['=1+1', '+cmd|x', '-2+3', '@SUM(A1)'])
    def test_formula_injection_neutralised(self, staff, two_orgs, payload):
        import csv
        import io

        a, _ = two_orgs
        a.name = payload
        a.save()
        text = client_for(staff).get(EXPORT).content.decode('utf-8-sig')
        cells = [c for row in csv.reader(io.StringIO(text)) for c in row]
        assert not any(c.startswith(('=', '+', '-', '@')) for c in cells if payload in c)
        assert f"'{payload}" in cells

    def test_csv_safe_unit(self):
        assert audit.csv_safe('=x') == "'=x"
        assert audit.csv_safe('\tx') == "'\tx"
        assert audit.csv_safe('plain') == 'plain'
        assert audit.csv_safe(None) == ''

    def test_export_respects_scope(self, two_orgs):
        a, b = two_orgs
        owner_a, _ = _add_member(a, 'owner-a', 'owner')
        text = client_for(owner_a).get(EXPORT).content.decode('utf-8-sig')
        assert 'Org B' not in text and 'Org A' in text

    def test_export_forbidden_for_regular_user(self, db, user, member):
        assert client_for(user).get(EXPORT).status_code == 403
