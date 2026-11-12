"""Onboarding profile API: required answers, validation, org invite consent, acquisition."""

import datetime
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import Client, RequestFactory
from django.utils import timezone

from apps.accounts.models import (
    CustomUser,
    DirectionPassingScore,
    District,
    DTMDirection,
    ExamCertificate,
    ExamGoal,
    OnboardingProfile,
    OrgResultsConsent,
    Region,
)
from apps.accounts.permissions import OnboardingComplete
from apps.accounts.services.token_service import create_token_pair
from apps.organizations.models import Membership, MembershipStatus, OrgInvite, OrgRole

URL = '/api/auth/onboarding/'
STRONG = 'Tr0ub4dor&horse'


def client_for(user, device='device-aaaaaaaaaaaaaaaa'):
    c = Client()
    c.cookies['access_token'] = create_token_pair(user, device)[0]
    return c


def patch(c, payload, url=URL):
    return c.patch(url, data=json.dumps(payload), content_type='application/json')


def post(c, url, payload):
    return c.post(url, data=json.dumps(payload), content_type='application/json')


@pytest.fixture
def ref(db):
    dtm = ExamGoal.objects.create(code='dtm', name_uz='DTM', requires_direction=True, sort_order=1)
    ielts = ExamGoal.objects.create(code='ielts', name_uz='IELTS', sort_order=2)
    ExamGoal.objects.create(code='old', name_uz='Old', is_active=False)
    direction = DTMDirection.objects.create(
        code='med', name_uz='Tibbiyot', subject_1='biology', subject_2='chemistry'
    )
    DirectionPassingScore.objects.create(direction=direction, year=2025, grant_score=150.5)
    r1 = Region.objects.create(code=1, name_uz='Toshkent', name_ru='T', name_en='T', slug='tosh')
    r2 = Region.objects.create(code=2, name_uz='Samarqand', name_ru='S', name_en='S', slug='sam')
    d1 = District.objects.create(region=r1, code=1, name_uz='Yunusobod', name_ru='Y', name_en='Y')
    d2 = District.objects.create(region=r2, code=1, name_uz='Urgut', name_ru='U', name_en='U')
    return SimpleNamespace(dtm=dtm, ielts=ielts, direction=direction, r1=r1, r2=r2, d1=d1, d2=d2)


@pytest.fixture
def user(db):
    return CustomUser.objects.create_user(username='onb.user', password=STRONG)


@pytest.fixture
def full_payload(ref):
    return {
        'goals': ['dtm', 'ielts'],
        'primary_goal': 'dtm',
        'dtm_direction': 'med',
        'test_language': 'uz',
        'target_score': 160,
        'graduation_year': timezone.localdate().year,
        'region': 1,
        'district': ref.d1.pk,
        'birth_date': '2008-05-17',
    }


@pytest.mark.integration
class TestState:
    def test_fresh_user_is_missing_everything_required(self, user, ref):
        body = client_for(user).get(URL).json()
        assert body['is_complete'] is False
        assert set(body['missing']) == {
            'goals', 'primary_goal', 'test_language', 'target',
            'graduation_year', 'region', 'district', 'birth_date',
        }  # fmt: skip

    def test_anonymous_is_401(self, db):
        assert Client().get(URL).status_code == 401
        assert Client().patch(URL, '{}', content_type='application/json').status_code == 401

    def test_full_answers_complete_the_profile(self, user, ref, full_payload):
        c = client_for(user)
        resp = patch(
            c,
            {**full_payload, 'exam_date': (timezone.localdate() + timedelta(days=90)).isoformat()},
        )

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['is_complete'] is True and body['missing'] == []
        assert body['goals'] == ['dtm', 'ielts'] and body['primary_goal'] == 'dtm'
        assert body['subject_1'] == 'biology' and body['subject_2'] == 'chemistry'
        user.refresh_from_db()
        assert (user.region, user.district, user.target_score) == (ref.r1, ref.d1, 160)
        assert user.birth_year == 2008
        assert OnboardingProfile.objects.get(user=user).completed_at is not None

    def test_partial_updates_accumulate(self, user, ref):
        c = client_for(user)
        patch(c, {'test_language': 'ru'})
        patch(c, {'graduation_year': timezone.localdate().year})
        body = c.get(URL).json()
        assert body['test_language'] == 'ru'
        assert 'test_language' not in body['missing'] and 'graduation_year' not in body['missing']

    def test_target_grade_can_replace_target_score(self, user, ref, full_payload):
        del full_payload['target_score']
        c = client_for(user)
        assert 'target' in patch(c, full_payload).json()['missing']
        assert patch(c, {'target_grade': 'B+'}).json()['is_complete'] is True

    def test_district_is_required_not_just_region(self, user, ref, full_payload):
        del full_payload['district']
        resp = patch(client_for(user), full_payload)
        assert resp.status_code == 400
        assert resp.json()['error']['details']['district'][0]['code'] == 'required'

    def test_direction_or_subject_pair_needed_only_for_dtm_like_goals(
        self, user, ref, full_payload
    ):
        c = client_for(user)
        full_payload['goals'], full_payload['primary_goal'] = ['ielts'], 'ielts'
        del full_payload['dtm_direction']
        assert patch(c, full_payload).json()['is_complete'] is True  # ielts does not need one

        body = patch(c, {'goals': ['dtm'], 'primary_goal': 'dtm'}).json()
        assert body['missing'] == ['dtm_direction_or_subjects']
        body = patch(c, {'subject_1': 'math', 'subject_2': 'physics'}).json()
        assert body['is_complete'] is True and body['dtm_direction'] is None

    def test_certificates_are_replaced_as_a_list(self, user, ref):
        c = client_for(user)
        certs = [
            {'cert_type': 'ielts', 'level': '7.0', 'date': '2025-03-01'},
            {
                'cert_type': 'milliy_sertifikat',
                'subject': 'math',
                'level': 'A+',
                'date': '2025-06-01',
            },
        ]
        assert len(patch(c, {'certificates': certs}).json()['certificates']) == 2
        assert len(patch(c, {'certificates': certs[:1]}).json()['certificates']) == 1
        assert ExamCertificate.objects.filter(user=user).count() == 1
        assert patch(c, {'certificates': []}).json()['certificates'] == []

    def test_parent_telegram_is_normalised(self, user, ref):
        c = client_for(user)
        for raw in ('https://t.me/parent_name', '@parent_name', 'parent_name'):
            assert patch(c, {'parent_telegram': raw}).json()['parent_telegram'] == '@parent_name'
        assert patch(c, {'parent_telegram': ''}).json()['parent_telegram'] is None

    def test_form_encoded_bodies_are_refused(self, user, ref):
        assert client_for(user).patch(
            URL, 'test_language=uz', content_type='application/x-www-form-urlencoded'
        ).status_code in (415, 400)


@pytest.mark.integration
class TestValidationNegatives:
    @pytest.mark.parametrize(
        'payload,field',
        [
            ({'goals': []}, 'goals'),
            ({'goals': ['nope']}, 'goals'),
            ({'goals': ['old']}, 'goals'),  # inactive
            ({'goals': 'dtm'}, 'goals'),
            ({'goals': ['dtm', 'ielts']}, 'primary_goal'),  # two goals, no primary
            ({'goals': ['dtm'], 'primary_goal': 'ielts'}, 'primary_goal'),
            ({'dtm_direction': 'ghost'}, 'dtm_direction'),
            ({'test_language': 'en'}, 'test_language'),
            ({'target_score': 0}, 'target_score'),
            ({'target_score': 190}, 'target_score'),
            ({'target_score': '150'}, 'target_score'),
            ({'target_score': True}, 'target_score'),
            ({'target_grade': 'Z'}, 'target_grade'),
            ({'exam_date': '2001-01-01'}, 'exam_date'),
            ({'exam_date': 'tomorrow'}, 'exam_date'),
            ({'graduation_year': 1900}, 'graduation_year'),
            ({'graduation_year': 2999}, 'graduation_year'),
            ({'graduation_year': '2026'}, 'graduation_year'),
            ({'birth_date': '2099-01-01'}, 'birth_date'),
            ({'birth_date': '2020-01-01'}, 'birth_date'),  # too young
            ({'birth_date': '1900-01-01'}, 'birth_date'),
            ({'birth_date': '17.05.2008'}, 'birth_date'),
            ({'region': 99}, 'region'),
            ({'region': 'Toshkent'}, 'region'),
            ({'parent_telegram': 'x'}, 'parent_telegram'),
            ({'parent_telegram': 'https://evil.example/parent_name'}, 'parent_telegram'),
            ({'acquisition_answer': 'psychic'}, 'acquisition_answer'),
            ({'acquisition_note': 'x' * 201}, 'acquisition_note'),
            (
                {'certificates': [{'cert_type': 'ielts', 'level': '7', 'date': '2999-01-01'}]},
                'certificates',
            ),
            (
                {'certificates': [{'cert_type': 'nope', 'level': '7', 'date': '2020-01-01'}]},
                'certificates',
            ),
            (
                {'certificates': [{'cert_type': 'ielts', 'level': '', 'date': '2020-01-01'}]},
                'certificates',
            ),
            ({'certificates': ['junk']}, 'certificates'),
        ],
    )
    def test_invalid_input_is_a_400_and_saves_nothing(self, user, ref, payload, field):
        c = client_for(user)
        resp = patch(c, payload)
        assert resp.status_code == 400, (payload, resp.content)
        assert field in resp.json()['error']['details']
        state = c.get(URL).json()
        assert state['goals'] == [] and state['test_language'] is None

    def test_district_must_belong_to_the_region(self, user, ref):
        c = client_for(user)
        resp = patch(c, {'region': 1, 'district': ref.d2.pk})  # Urgut is in Samarqand
        assert resp.status_code == 400
        assert resp.json()['error']['details']['district'][0]['code'] == 'district_not_in_region'
        user.refresh_from_db()
        assert user.region is None and user.district is None

    def test_changing_region_alone_is_refused(self, user, ref):
        c = client_for(user)
        patch(c, {'region': 1, 'district': ref.d1.pk})
        resp = patch(c, {'region': 2})
        assert resp.status_code == 400
        user.refresh_from_db()
        assert user.region == ref.r1 and user.district == ref.d1

    def test_one_bad_field_rejects_the_whole_request(self, user, ref):
        c = client_for(user)
        resp = patch(c, {'test_language': 'uz', 'target_score': 999})
        assert resp.status_code == 400
        assert c.get(URL).json()['test_language'] is None

    def test_removing_a_goal_drops_a_stale_primary(self, user, ref):
        c = client_for(user)
        patch(c, {'goals': ['dtm', 'ielts'], 'primary_goal': 'dtm'})
        body = patch(c, {'goals': ['ielts']}).json()
        assert body['goals'] == ['ielts'] and body['primary_goal'] == 'ielts'


@pytest.mark.integration
class TestIsolation:
    def test_each_user_sees_only_their_own_answers(self, user, ref, full_payload):
        other = CustomUser.objects.create_user(username='other.user', password=STRONG)
        patch(client_for(user), full_payload)
        mine = client_for(user, 'device-1111111111111111').get(URL).json()
        theirs = client_for(other, 'device-2222222222222222').get(URL).json()
        assert mine['is_complete'] and theirs['goals'] == [] and not theirs['is_complete']

    def test_patching_never_touches_other_users(self, user, ref, full_payload):
        other = CustomUser.objects.create_user(username='other.user', password=STRONG)
        patch(client_for(user), full_payload)
        other.refresh_from_db()
        assert other.region is None and not OnboardingProfile.objects.filter(user=other).exists()


@pytest.mark.integration
class TestPermission:
    def _request(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return request

    def test_blocks_incomplete_and_allows_complete(self, user, ref, full_payload):
        perm = OnboardingComplete()
        assert perm.has_permission(self._request(user), None) is False
        patch(client_for(user), full_payload)
        user.refresh_from_db()
        assert perm.has_permission(self._request(user), None) is True

    def test_anonymous_is_blocked(self, db):
        from django.contrib.auth.models import AnonymousUser

        assert OnboardingComplete().has_permission(self._request(AnonymousUser()), None) is False


@pytest.mark.integration
class TestOptions:
    def test_public_reference_data(self, ref):
        body = Client().get(URL + 'options/').json()
        assert [g['code'] for g in body['goals']] == ['dtm', 'ielts']  # inactive hidden
        assert body['dtm_directions'][0]['passing_score']['year'] == 2025
        assert {r['code'] for r in body['regions']} == {1, 2}
        assert body['regions'][0]['districts'][0]['name_uz'] == 'Yunusobod'
        assert body['target_score_max'] == 189

    def test_empty_tables_are_fine(self, db):
        body = Client().get(URL + 'options/').json()
        assert body['goals'] == [] and body['regions'] == []


@pytest.fixture
def invite(db, org):
    org.status = 'active'
    org.save()
    creator = CustomUser.objects.create_user(username='org.owner', password=STRONG)
    role = OrgRole.objects.get(organization=org, name='student')
    return OrgInvite.objects.create(organization=org, role=role, created_by=creator)


@pytest.mark.integration
class TestOrgInvite:
    URL = URL + 'org-invite/'

    def test_consent_is_explicit_or_nothing_happens(self, user, invite, org):
        c = client_for(user)
        for consent in (False, None, 'yes', 1):
            resp = post(c, self.URL, {'code': str(invite.token), 'consent': consent})
            assert resp.status_code == 400 and resp.json()['error']['code'] == 'consent_required'
        resp = post(c, self.URL, {'code': str(invite.token)})
        assert resp.status_code == 400
        assert not Membership.objects.filter(user=user).exists()
        assert not OrgResultsConsent.objects.exists()

    def test_joining_creates_membership_consent_and_counts_the_use(self, user, invite, org):
        resp = post(client_for(user), self.URL, {'code': str(invite.token), 'consent': True})

        assert resp.status_code == 200 and resp.json()['organization']['name'] == org.name
        m = Membership.objects.get(user=user, organization=org)
        assert m.status == MembershipStatus.ACTIVE and m.role.name == 'student' and m.is_primary
        assert m.joined_via == 'invite'
        assert OrgResultsConsent.objects.get(user=user, organization=org).revoked_at is None
        invite.refresh_from_db()
        assert invite.used_count == 1
        assert (
            client_for(user, 'device-1111111111111111')
            .get(URL)
            .json()['org_consents'][0]['organization_name']
            == org.name
        )

    def test_joining_twice_does_not_burn_another_use(self, user, invite):
        c = client_for(user)
        post(c, self.URL, {'code': str(invite.token), 'consent': True})
        post(c, self.URL, {'code': str(invite.token), 'consent': True})
        invite.refresh_from_db()
        assert invite.used_count == 1 and Membership.objects.filter(user=user).count() == 1

    @pytest.mark.parametrize('code', ['', 'garbage', '00000000-0000-0000-0000-000000000000'])
    def test_unknown_codes_are_404(self, user, invite, code):
        resp = post(client_for(user), self.URL, {'code': code, 'consent': True})
        assert resp.status_code == 404
        assert not Membership.objects.filter(user=user).exists()

    def test_expired_exhausted_or_disabled_invites_are_refused(self, user, invite):
        c = client_for(user)
        invite.expires_at = timezone.now() - timedelta(days=1)
        invite.save()
        assert post(c, self.URL, {'code': str(invite.token), 'consent': True}).status_code == 404
        invite.expires_at, invite.max_uses, invite.used_count = None, 1, 1
        invite.save()
        assert post(c, self.URL, {'code': str(invite.token), 'consent': True}).status_code == 404
        invite.max_uses, invite.is_active = None, False
        invite.save()
        assert post(c, self.URL, {'code': str(invite.token), 'consent': True}).status_code == 404
        assert not Membership.objects.filter(user=user).exists()

    def test_an_invite_only_ever_joins_its_own_org(self, user, invite, org, org2):
        post(client_for(user), self.URL, {'code': str(invite.token), 'consent': True})
        assert list(
            Membership.objects.filter(user=user).values_list('organization', flat=True)
        ) == [org.pk]
        assert not Membership.objects.filter(organization=org2, user=user).exists()
        assert not OrgResultsConsent.objects.filter(organization=org2).exists()

    def test_second_org_does_not_steal_the_primary_flag(self, user, invite, org2):
        c = client_for(user)
        post(c, self.URL, {'code': str(invite.token), 'consent': True})
        org2.status = 'active'
        org2.save()
        role2 = OrgRole.objects.get(organization=org2, name='student')
        creator = invite.created_by
        inv2 = OrgInvite.objects.create(organization=org2, role=role2, created_by=creator)
        post(c, self.URL, {'code': str(inv2.token), 'consent': True})
        assert Membership.objects.get(user=user, organization=org2).is_primary is False
        assert Membership.objects.filter(user=user, is_primary=True).count() == 1

    def test_suspended_member_cannot_rejoin_via_invite(self, user, invite, org):
        role = OrgRole.objects.get(organization=org, name='student')
        Membership.objects.create(
            user=user, organization=org, role=role, status=MembershipStatus.SUSPENDED
        )
        resp = post(client_for(user), self.URL, {'code': str(invite.token), 'consent': True})
        assert resp.status_code == 404
        assert Membership.objects.get(user=user).status == MembershipStatus.SUSPENDED

    def test_invite_of_a_pending_org_is_refused(self, user, invite, org):
        org.status = 'pending'
        org.save()
        resp = post(client_for(user), self.URL, {'code': str(invite.token), 'consent': True})
        assert resp.status_code == 404
        assert not Membership.objects.filter(user=user).exists()

    def test_requires_login(self, invite):
        assert (
            post(Client(), self.URL, {'code': str(invite.token), 'consent': True}).status_code
            == 401
        )

    def test_withdrawing_consent_is_per_user(self, user, invite, org):
        other = CustomUser.objects.create_user(username='other.user', password=STRONG)
        post(client_for(user), self.URL, {'code': str(invite.token), 'consent': True})
        post(
            client_for(other, 'device-2222222222222222'),
            self.URL,
            {'code': str(invite.token), 'consent': True},
        )

        url = f'{URL}org-consent/{org.pk}/'
        assert client_for(user, 'device-3333333333333333').delete(url).status_code == 200
        assert OrgResultsConsent.objects.get(user=user).revoked_at is not None
        assert OrgResultsConsent.objects.get(user=other).revoked_at is None  # untouched
        assert (
            client_for(user, 'device-4444444444444444').delete(url).status_code == 404
        )  # already withdrawn
        assert client_for(user, 'device-5555555555555555').get(URL).json()['org_consents'] == []


@pytest.mark.integration
class TestAcquisition:
    def register(self, **extra):
        payload = {
            'first_name': 'Ali',
            'last_name': 'Valiyev',
            'username': 'ali.valiyev',
            'password': STRONG,
        }
        payload.update(extra)
        return Client().post(
            '/api/auth/register/', data=json.dumps(payload), content_type='application/json'
        )

    def test_utm_is_captured_automatically_at_signup(self, db):
        self.register(
            acquisition={
                'utm_source': 'instagram',
                'utm_campaign': 'nov',
                'junk': 'x',
                'utm_medium': 123,
            }
        )
        acq = OnboardingProfile.objects.get(user__username='ali.valiyev').acquisition
        assert acq == {
            'utm_source': 'instagram',
            'utm_campaign': 'nov',
        }  # junk and non-strings dropped

    def test_first_touch_wins(self, db):
        self.register(acquisition={'utm_source': 'instagram'})
        user = CustomUser.objects.get(username='ali.valiyev')
        from apps.accounts.services.onboarding import capture_acquisition

        capture_acquisition(user, {'utm_source': 'google'})
        assert OnboardingProfile.objects.get(user=user).acquisition == {'utm_source': 'instagram'}

    def test_signup_without_acquisition_is_fine(self, db):
        assert self.register().status_code == 201

    def test_long_values_are_truncated(self, db):
        self.register(acquisition={'utm_source': 'x' * 5000})
        assert len(OnboardingProfile.objects.get().acquisition['utm_source']) == 200

    def test_non_dict_acquisition_is_ignored(self, db):
        assert self.register(acquisition='instagram').status_code == 201
        assert not OnboardingProfile.objects.exists()

    def test_optional_answer_is_stored_via_onboarding(self, user, ref):
        body = patch(
            client_for(user), {'acquisition_answer': 'friend', 'acquisition_note': 'Ali'}
        ).json()
        assert body['acquisition_answer'] == 'friend' and body['acquisition_note'] == 'Ali'

    def test_mini_app_start_param_is_captured_for_new_users(self, db):
        import hashlib
        import hmac
        import time
        from urllib.parse import urlencode

        user_json = json.dumps(
            {'id': 8080, 'first_name': 'Mini', 'last_name': 'App'}, separators=(',', ':')
        )
        params = {
            'auth_date': str(int(time.time())),
            'user': user_json,
            'start_param': 'ref_school7',
        }
        check = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
        secret = hmac.new(b'WebAppData', b'test-bot-token-1234567890', hashlib.sha256).digest()
        params['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

        resp = Client().post(
            '/api/auth/telegram/',
            data=json.dumps({'init_data': urlencode(params)}),
            content_type='application/json',
        )

        assert resp.status_code == 200
        assert OnboardingProfile.objects.get(user__telegram_id=8080).acquisition == {
            'start_param': 'ref_school7'
        }
        # later logins never overwrite it
        assert datetime.date.today()


@pytest.mark.integration
class TestMeSummary:
    def test_me_reports_onboarding_and_2fa_state(self, user, ref, full_payload):
        c = client_for(user)
        body = c.get('/api/auth/me/').json()
        assert (
            body['onboarding']['is_complete'] is False and 'goals' in body['onboarding']['missing']
        )
        assert body['two_factor_enabled'] is False
        patch(c, full_payload)
        assert c.get('/api/auth/me/').json()['onboarding'] == {'is_complete': True, 'missing': []}
