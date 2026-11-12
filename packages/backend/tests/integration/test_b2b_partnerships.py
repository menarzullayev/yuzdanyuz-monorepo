"""Partnership applications CRM: public endpoint guards + staff workflow."""

from datetime import timedelta

import pytest
from django.dispatch import receiver
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.organizations.hooks import partnership_application_created
from apps.organizations.models import PartnershipApplication

from .b2b_helpers import client_for, make_user, patch_json, post_json

pytestmark = pytest.mark.integration

APPLY = 'partnerships-v1:apply'


def payload(**over):
    data = {
        'applicant_type': 'school',
        'name': '12-maktab',
        'contact_person': 'Aliyev Vali',
        'phone': '90 123 45 67',
        'region': 'Toshkent',
        'students_count': 450,
        'message': 'Hamkorlik qilmoqchimiz',
    }
    data.update(over)
    return data


@pytest.fixture
def anon():
    return Client()


@pytest.fixture
def staff(db):
    return make_user('staffer', is_staff=True)


def apply(client, **over):
    return post_json(client, reverse(APPLY), payload(**over))


class TestPublicApply:
    def test_creates_application_and_normalizes_phone(self, db, anon):
        resp = apply(anon)
        assert resp.status_code == 201
        app = PartnershipApplication.objects.get()
        assert app.phone == '+998901234567'
        assert app.status == 'new'

    def test_no_auth_needed_and_no_csrf(self, db):
        c = Client(enforce_csrf_checks=True)
        assert apply(c).status_code == 201

    @pytest.mark.parametrize(
        'over',
        [
            {'applicant_type': 'university'},
            {'phone': '12345'},
            {'name': 'x' * 201},
            {'message': 'x' * 2001},
            {'students_count': 0},
            {'email': 'not-an-email'},
            {'region': ''},
        ],
    )
    def test_invalid_input_rejected(self, db, anon, over):
        assert apply(anon, **over).status_code == 400
        assert PartnershipApplication.objects.count() == 0

    def test_honeypot_pretends_success_but_stores_nothing(self, db, anon):
        resp = apply(anon, website='http://spam.example')
        assert resp.status_code == 201
        assert PartnershipApplication.objects.count() == 0

    def test_same_phone_same_day_is_conflict(self, db, anon):
        assert apply(anon).status_code == 201
        resp = apply(anon, phone='+998901234567', name='Boshqa nom')
        assert resp.status_code == 409
        assert PartnershipApplication.objects.count() == 1

    def test_same_email_same_day_is_conflict(self, db, anon):
        assert apply(anon, email='a@b.uz').status_code == 201
        assert apply(anon, phone='+998911112233', email='A@B.uz').status_code == 409

    def test_same_phone_allowed_after_a_day(self, db, anon):
        apply(anon)
        PartnershipApplication.objects.update(created_at=timezone.now() - timedelta(hours=25))
        assert apply(anon).status_code == 201
        assert PartnershipApplication.objects.count() == 2

    def test_rate_limit_per_ip(self, db, anon):
        codes = [apply(anon, phone=f'+99890000000{i}').status_code for i in range(6)]
        assert codes[:5] == [201] * 5
        assert codes[5] == 429

    def test_hook_fires_once_for_new_application_only(
        self, db, anon, django_capture_on_commit_callbacks
    ):
        seen = []

        @receiver(partnership_application_created, weak=False)
        def _h(sender, application, **kw):
            seen.append(application.pk)

        try:
            with django_capture_on_commit_callbacks(execute=True):
                apply(anon)
            assert len(seen) == 1
            with django_capture_on_commit_callbacks(execute=True):
                apply(anon)  # duplicate
                apply(anon, phone='+998933334455', website='bot')  # honeypot
            assert len(seen) == 1
        finally:
            partnership_application_created.disconnect(_h)

    def test_failing_receiver_does_not_break_submission(
        self, db, anon, django_capture_on_commit_callbacks
    ):
        @receiver(partnership_application_created, weak=False)
        def _boom(sender, application, **kw):
            raise RuntimeError('telegram down')

        try:
            with django_capture_on_commit_callbacks(execute=True):
                assert apply(anon).status_code == 201
        finally:
            partnership_application_created.disconnect(_boom)


class TestStaffCrm:
    def _app(self, **kw):
        base = {
            'applicant_type': 'school',
            'name': 'A',
            'contact_person': 'P',
            'phone': '+998900000001',
            'region': 'R',
            'students_count': 10,
        }
        base.update(kw)
        return PartnershipApplication.objects.create(**base)

    def test_anonymous_and_non_staff_cannot_read(self, db, org):
        self._app()
        url = reverse('partnerships-v1:application-list')
        assert Client().get(url).status_code == 403
        owner = make_user('own', org, 'owner')
        assert client_for(owner).get(url).status_code == 403

    def test_non_staff_cannot_patch(self, db, org):
        app = self._app()
        owner = make_user('own', org, 'owner')
        url = reverse('partnerships-v1:application-detail', args=[app.pk])
        assert patch_json(client_for(owner), url, {'status': 'contacted'}).status_code == 403

    def test_status_flow_and_forbidden_jump(self, db, staff):
        app = self._app()
        c = client_for(staff)
        url = reverse('partnerships-v1:application-detail', args=[app.pk])
        assert patch_json(c, url, {'status': 'agreed'}).status_code == 400  # NEW -> AGREED
        r = patch_json(c, url, {'status': 'contacted', 'notes': 'called'})
        assert r.status_code == 200
        app.refresh_from_db()
        assert app.contacted_at is not None and app.notes
        assert patch_json(c, url, {'status': 'agreed'}).status_code == 200
        assert patch_json(c, url, {'status': 'contacted'}).status_code == 400  # no going back
        assert patch_json(c, url, {'status': 'rejected'}).status_code == 200
        assert patch_json(c, url, {'status': 'new'}).status_code == 200  # reopen

    def test_applicant_fields_are_read_only(self, db, staff):
        app = self._app()
        url = reverse('partnerships-v1:application-detail', args=[app.pk])
        patch_json(client_for(staff), url, {'name': 'Hacked', 'phone': '+998999999999'})
        app.refresh_from_db()
        assert app.name == 'A' and app.phone == '+998900000001'

    def test_assign_only_to_staff(self, db, staff, org):
        app = self._app()
        outsider = make_user('stud', org, 'student')
        url = reverse('partnerships-v1:application-detail', args=[app.pk])
        c = client_for(staff)
        assert patch_json(c, url, {'assigned_to': str(outsider.pk)}).status_code == 400
        assert patch_json(c, url, {'assigned_to': str(staff.pk)}).status_code == 200

    def test_todays_calls_filter(self, db, staff):
        today = timezone.localdate()
        due = self._app(phone='+998900000010', next_call_date=today)
        self._app(phone='+998900000011', next_call_date=today + timedelta(days=1))
        self._app(phone='+998900000012', next_call_date=today - timedelta(days=1))
        self._app(phone='+998900000013', next_call_date=today, status='rejected')
        self._app(phone='+998900000014')
        c = client_for(staff)
        url = reverse('partnerships-v1:application-list')
        ids = [r['id'] for r in c.get(url, {'calls': 'today'}).json()['results']]
        assert ids == [str(due.pk)]
        assert len(c.get(url, {'calls': 'overdue'}).json()['results']) == 1

    def test_list_filters_and_summary(self, db, staff):
        self._app(phone='+998900000020', name='Alpha')
        self._app(
            phone='+998900000021', name='Beta', status='contacted', applicant_type='learning_centre'
        )
        c = client_for(staff)
        url = reverse('partnerships-v1:application-list')
        assert c.get(url, {'status': 'contacted'}).json()['count'] == 1
        assert c.get(url, {'type': 'learning_centre'}).json()['count'] == 1
        assert c.get(url, {'q': 'alph'}).json()['count'] == 1
        summary = c.get(reverse('partnerships-v1:application-summary')).json()
        assert summary['by_status']['new'] == 1 and summary['by_status']['contacted'] == 1
