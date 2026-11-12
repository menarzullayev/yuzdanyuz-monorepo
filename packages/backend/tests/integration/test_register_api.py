"""Register / username / password-change API, including the P2-P4 regressions."""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser

STRONG = 'Tr0ub4dor&horse'
URL = '/api/auth/register/'


def reg(client=None, **overrides):
    payload = {
        'first_name': 'Ali',
        'last_name': 'Valiyev',
        'username': 'ali.valiyev',
        'password': STRONG,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return (client or Client()).post(URL, data=json.dumps(payload), content_type='application/json')


def err(resp, field):
    return [e['code'] for e in resp.json()['error']['details'].get(field, [])]


@pytest.mark.integration
class TestRegister:
    def test_success_creates_user_signs_in_and_stores_consent(self, db, settings):
        resp = reg()

        assert resp.status_code == 201, resp.content
        user = CustomUser.objects.get(username='ali.valiyev')
        assert user.first_name == 'Ali' and user.last_name == 'Valiyev'
        assert user.terms_version == settings.TERMS_VERSION
        assert user.terms_accepted_at is not None
        assert abs(timezone.now() - user.terms_accepted_at) < timedelta(minutes=1)
        assert resp.cookies['access_token'].value
        assert resp.json()['user']['username'] == 'ali.valiyev'

    def test_no_fake_email_is_stored_for_username_signups(self, db):
        reg()
        reg(username='second.user')
        assert CustomUser.objects.get(username='ali.valiyev').email is None
        assert CustomUser.objects.get(username='second.user').email is None  # no unique clash

    def test_username_is_lowercased(self, db):
        reg(username='Ali.Valiyev')
        assert CustomUser.objects.filter(username='ali.valiyev').exists()

    @pytest.mark.parametrize(
        'name', ['ab', 'a' * 33, 'ali valiyev', 'ali-valiyev', 'али', 'ali@x', 'ali/']
    )
    def test_invalid_usernames(self, db, name):
        assert err(reg(username=name), 'username') == ['username_invalid']

    @pytest.mark.parametrize(
        'name', ['admin', 'Support', 'root', 'api', 'staff', 'mashqgoh', 'help']
    )
    def test_reserved_usernames(self, db, name):
        assert err(reg(username=name), 'username') == ['username_reserved']
        assert not CustomUser.objects.filter(username=name.lower()).exists()

    def test_taken_username_returns_three_suggestions(self, db):
        reg()
        resp = reg(username='ALI.VALIYEV', first_name='Boshqa')

        assert resp.status_code == 400
        assert err(resp, 'username') == ['username_taken']
        suggestions = resp.json()['error']['suggestions']
        assert len(suggestions) == 3
        assert len(set(suggestions)) == 3
        assert all(not CustomUser.objects.filter(username=s).exists() for s in suggestions)

    @pytest.mark.parametrize('first,last', [('A', 'Valiyev'), ('Ali', ''), ('x' * 61, 'Valiyev')])
    def test_names_need_2_to_60_chars(self, db, first, last):
        resp = reg(first_name=first, last_name=last)
        assert resp.status_code == 400
        assert resp.json()['error']['details']

    def test_missing_names_rejected(self, db):
        assert reg(first_name=None, last_name=None).status_code == 400

    def test_email_is_optional_and_unverified(self, db):
        resp = reg(email='Ali@Example.COM')
        assert resp.status_code == 201
        user = CustomUser.objects.get(username='ali.valiyev')
        assert user.email == 'ali@example.com'
        assert resp.json()['user']['email_verified'] is False

    def test_invalid_email_rejected(self, db):
        assert err(reg(email='not-an-email'), 'email') == ['email_invalid']

    def test_p2_same_email_local_part_on_two_domains_is_fine(self, db):
        """P2: ali@a.uz then ali@b.uz used to 500 (derived username collision)."""
        assert reg(username='first.one', email='ali@a.uz').status_code == 201
        assert reg(username='second.one', email='ali@b.uz').status_code == 201

    def test_p2_duplicate_email_is_a_clean_400_without_db_text(self, db):
        reg(email='ali@a.uz')
        resp = reg(username='other.user', email='ali@a.uz')
        assert resp.status_code == 400
        assert err(resp, 'email') == ['email_taken']
        body = resp.content.decode().lower()
        assert 'duplicate key' not in body and 'constraint' not in body

    def test_p2_username_race_never_leaks_database_text(self, db, monkeypatch):
        """Both checks pass, the insert loses the race: still a clean 400."""
        reg()
        from apps.accounts.services import usernames

        monkeypatch.setattr(usernames, 'is_taken', lambda *a, **k: False)
        resp = reg()
        assert resp.status_code == 400
        assert err(resp, 'username') == ['username_taken']
        assert 'duplicate key' not in resp.content.decode().lower()

    def test_p4_no_verification_email_is_promised_or_sent(self, db, mailoutbox):
        resp = reg(email='ali@a.uz')
        assert 'link' not in resp.content.decode().lower()
        assert mailoutbox == []

    def test_register_response_has_no_secrets(self, db):
        body = reg().content.decode()
        assert 'password' not in body.lower().replace('has_password', '')

    def test_duplicate_username_differing_only_in_case_blocked_by_db(self, db):
        """Case-insensitive uniqueness is enforced by the database, not just the form."""
        from django.db import IntegrityError, transaction

        CustomUser.objects.create_user(username='dup.user', password=STRONG)
        with pytest.raises(IntegrityError), transaction.atomic():
            CustomUser.objects.create_user(username='DUP.user', password=STRONG)


@pytest.mark.integration
class TestRegisterPasswordPolicy:
    """P3: 'password1' used to be accepted because validators were never called."""

    @pytest.mark.parametrize(
        'pw',
        [
            'password1',  # common list
            'short1',  # too short
            'a' * 129,  # too long
            'я' * 40,  # 80 bytes in UTF-8
            'aaaaaaaaaa',  # one repeated char
            '12345678',  # numeric / 123456 variant
            '1234567890',
            '123456abc',
            'milliy2026',
            'MashqGoh!!99',
            'sertifikat1',
            'yuzdanyuz-1',
            'mock',
            'ali.valiyev',  # equals username
            'ali.valiyev1',  # similar to username
        ],
    )
    def test_weak_passwords_are_rejected(self, db, pw):
        resp = reg(password=pw)
        assert resp.status_code == 400, pw
        assert err(resp, 'password'), pw
        assert not CustomUser.objects.filter(username='ali.valiyev').exists()

    def test_strong_password_is_accepted(self, db):
        assert reg(password=STRONG).status_code == 201

    def test_exactly_72_bytes_ok_73_rejected(self, db):
        ok = ('x7Q#' * 18)[:72]
        assert len(ok.encode()) == 72
        assert reg(password=ok).status_code == 201
        too_long = ok + 'z'
        assert reg(username='another.one', password=too_long).status_code == 400

    def test_password_required(self, db):
        assert err(reg(password=None), 'password') == ['password_required']


@pytest.mark.integration
class TestUsernameCheck:
    def test_available(self, db):
        body = Client().get('/api/auth/username/check/?username=fresh.name').json()
        assert body == {'username': 'fresh.name', 'valid': True, 'available': True}

    def test_taken_with_suggestions(self, db):
        reg()
        body = Client().get('/api/auth/username/check/?username=Ali.Valiyev').json()
        assert body['available'] is False and body['reason'] == 'username_taken'
        assert len(body['suggestions']) == 3

    def test_reserved_and_invalid(self, db):
        c = Client()
        assert (
            c.get('/api/auth/username/check/?username=admin').json()['reason']
            == 'username_reserved'
        )
        assert c.get('/api/auth/username/check/?username=a').json()['reason'] == 'username_invalid'


@pytest.mark.integration
class TestUsernameChange:
    def _auto_user(self, db):
        user = CustomUser(username='ali.v', username_is_auto=True)
        user.set_unusable_password()
        user.save()
        from apps.accounts.services.token_service import create_token_pair

        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        return c, user

    def test_one_free_change_for_auto_usernames(self, db):
        c, user = self._auto_user(db)
        resp = c.post(
            '/api/auth/username/', {'username': 'ali.valiyev'}, content_type='application/json'
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.username == 'ali.valiyev' and user.username_changed_at is not None
        # the free change is spent
        again = c.post(
            '/api/auth/username/', {'username': 'ali.third'}, content_type='application/json'
        )
        assert again.status_code == 403
        user.refresh_from_db()
        assert user.username == 'ali.valiyev'

    def test_chosen_usernames_cannot_be_changed(self, db):
        user = CustomUser.objects.create_user(username='chosen', password=STRONG)
        from apps.accounts.services.token_service import create_token_pair

        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        resp = c.post(
            '/api/auth/username/', {'username': 'newname'}, content_type='application/json'
        )
        assert resp.status_code == 403

    def test_change_to_reserved_or_taken_rejected(self, db):
        c, user = self._auto_user(db)
        CustomUser.objects.create_user(username='taken.one', password=STRONG)
        for name in ('admin', 'TAKEN.one'):
            resp = c.post(
                '/api/auth/username/', {'username': name}, content_type='application/json'
            )
            assert resp.status_code == 400
        user.refresh_from_db()
        assert user.username == 'ali.v' and user.username_changed_at is None


@pytest.mark.integration
class TestPasswordChange:
    def _signed_in(self, db, password=STRONG):
        user = CustomUser.objects.create_user(username='changer', password=password)
        c = Client()
        c.post(
            '/api/auth/login/',
            {'username': 'changer', 'password': password},
            content_type='application/json',
        )
        return c, user

    def _change(self, c, **payload):
        return c.post('/api/auth/password/change/', payload, content_type='application/json')

    def test_change_succeeds_and_kills_other_devices(self, db):
        c, user = self._signed_in(db)
        other = Client()
        other.post(
            '/api/auth/login/',
            {'username': 'changer', 'password': STRONG},
            content_type='application/json',
        )

        resp = self._change(c, current_password=STRONG, new_password='N3w-passw0rd!x')

        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.check_password('N3w-passw0rd!x')
        assert other.post('/api/auth/refresh/').status_code == 401
        assert c.post('/api/auth/refresh/').status_code == 200  # current device stays in

    def test_wrong_current_password(self, db):
        c, user = self._signed_in(db)
        resp = self._change(c, current_password='wrong-one-1', new_password='N3w-passw0rd!x')
        assert resp.status_code == 400
        user.refresh_from_db()
        assert user.check_password(STRONG)

    def test_new_password_goes_through_the_same_policy(self, db):
        c, user = self._signed_in(db)
        resp = self._change(c, current_password=STRONG, new_password='password1')
        assert resp.status_code == 400
        assert resp.json()['error']['details']['new_password']
        user.refresh_from_db()
        assert user.check_password(STRONG)

    def test_new_password_similar_to_username_rejected(self, db):
        c, _ = self._signed_in(db)
        resp = self._change(c, current_password=STRONG, new_password='changer1234')
        assert resp.status_code == 400

    def test_anonymous_cannot_change(self, db):
        assert self._change(Client(), current_password='x', new_password='y').status_code == 401

    def test_guessing_current_password_is_throttled(self, db):
        c, _ = self._signed_in(db)
        statuses = [
            self._change(
                c, current_password=f'wrong-guess-{i}', new_password='N3w-passw0rd!x'
            ).status_code
            for i in range(8)
        ]
        assert 429 in statuses, statuses
        assert (
            self._change(c, current_password=STRONG, new_password='N3w-passw0rd!x').status_code
            == 429
        )

    def test_telegram_only_account_can_set_first_password(self, db):
        user = CustomUser(username='tg.only', telegram_id=7)
        user.set_unusable_password()
        user.save()
        from apps.accounts.services.token_service import create_token_pair

        c = Client()
        c.cookies['access_token'] = create_token_pair(user, 'device-aaaaaaaaaaaaaaaa')[0]
        resp = self._change(c, new_password='N3w-passw0rd!x')
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.has_usable_password()
