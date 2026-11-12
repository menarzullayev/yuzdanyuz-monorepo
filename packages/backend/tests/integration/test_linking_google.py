"""Account linking API, Telegram link-through-bot, and the Google (allauth) adapter."""

import json
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory

from apps.accounts.adapters import AccountAdapter, SocialAccountAdapter
from apps.accounts.models import CustomUser, DeviceSession
from apps.accounts.services import telegram_deeplink as dl
from apps.accounts.services.token_service import create_token_pair

STRONG = 'Tr0ub4dor&horse'


def authed(user, device='device-aaaaaaaaaaaaaaaa'):
    c = Client()
    c.cookies['access_token'] = create_token_pair(user, device)[0]
    return c


def jpost(c, url, payload=None):
    return c.post(url, data=json.dumps(payload or {}), content_type='application/json')


def tg_only(username='tg.person', tg_id=555):
    u = CustomUser(username=username, telegram_id=tg_id, telegram_username='handle')
    u.set_unusable_password()
    u.save()
    return u


def link_google(user, email='g@example.com'):
    return SocialAccount.objects.create(
        user=user, provider='google', uid=f'g-{user.pk}', extra_data={'email': email}
    )


# ── /api/auth/linked/ ───────────────────────────────────────


@pytest.mark.integration
class TestLinkedOverview:
    def test_overview_for_password_user(self, db):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        body = authed(u).get('/api/auth/linked/').json()
        assert body['has_password'] is True
        assert body['telegram'] == {'linked': False, 'username': None}
        assert body['google']['linked'] is False
        assert body['can_unlink'] == {'telegram': False, 'google': False}

    def test_requires_login(self, db):
        assert Client().get('/api/auth/linked/').status_code == 401


@pytest.mark.integration
class TestUnlinkLastMethod:
    def test_cannot_unlink_the_only_method_telegram(self, db):
        u = tg_only()
        resp = authed(u).delete('/api/auth/linked/telegram/')
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'last_method'
        u.refresh_from_db()
        assert u.telegram_id == 555

    def test_cannot_unlink_the_only_method_google(self, db):
        u = CustomUser(username='g.only')
        u.set_unusable_password()
        u.save()
        link_google(u)
        resp = authed(u).delete('/api/auth/linked/google/')
        assert resp.status_code == 400
        assert SocialAccount.objects.filter(user=u).count() == 1

    def test_can_unlink_telegram_when_a_password_remains(self, db):
        u = CustomUser.objects.create_user(username='both.user', password=STRONG, telegram_id=9)
        resp = authed(u).delete('/api/auth/linked/telegram/')
        assert resp.status_code == 200
        u.refresh_from_db()
        assert u.telegram_id is None and u.telegram_username is None

    def test_can_unlink_google_when_telegram_remains(self, db):
        u = tg_only()
        link_google(u)
        assert authed(u).delete('/api/auth/linked/google/').status_code == 200
        assert not SocialAccount.objects.filter(user=u).exists()

    def test_after_setting_a_password_the_last_telegram_can_go(self, db):
        u = tg_only()
        c = authed(u)
        assert (
            jpost(c, '/api/auth/password/change/', {'new_password': 'N3w-passw0rd!x'}).status_code
            == 200
        )
        assert c.delete('/api/auth/linked/telegram/').status_code == 200

    def test_unlinking_something_not_linked_is_404(self, db):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        assert authed(u).delete('/api/auth/linked/google/').status_code == 404
        assert authed(u).delete('/api/auth/linked/telegram/').status_code == 404

    def test_google_account_of_another_user_is_untouched(self, db):
        a = CustomUser.objects.create_user(username='user.a', password=STRONG)
        b = CustomUser.objects.create_user(username='user.b', password=STRONG)
        link_google(b)
        assert authed(a).delete('/api/auth/linked/google/').status_code == 404
        assert SocialAccount.objects.filter(user=b).exists()


# ── Link Telegram through the bot ───────────────────────────


@pytest.fixture
def bot():
    with (
        patch('apps.accounts.views.telegram_deeplink.send_message') as send,
        patch('apps.accounts.views.telegram_deeplink.send_link_confirmation') as confirm,
        patch('apps.accounts.views.telegram_deeplink.send_contact_request') as contact,
        patch('apps.accounts.views.telegram_deeplink.answer_callback'),
    ):
        yield type('Bot', (), {'send': send, 'confirm': confirm, 'contact': contact})


def webhook(update):
    return Client().post(
        '/api/bot/webhook/',
        data=json.dumps(update),
        content_type='application/json',
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-webhook-secret',
    )


def start(token, tg_id):
    return webhook({'message': {'text': f'/start tgauth_{token}', 'from': {'id': tg_id}}})


def press(data, from_id, first_name='X'):
    return webhook(
        {
            'callback_query': {
                'id': 'cb1',
                'data': data,
                'from': {'id': from_id, 'first_name': first_name},
            }
        }
    )


@pytest.mark.integration
class TestLinkTelegramThroughBot:
    def _begin(self, user):
        c = authed(user)
        return c, jpost(c, '/api/auth/linked/telegram/start/').json()

    def test_full_flow_links_after_the_owner_confirms(self, db, bot):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        c, body = self._begin(u)
        assert body['deep_link'].startswith('https://t.me/test_bot?start=tgauth_')

        start(body['token'], 4321)
        # the bot names the account being linked and asks for confirmation; no phone is requested
        assert bot.confirm.call_args.args[0] == 4321
        bot.contact.assert_not_called()
        assert Client().get(f'/api/auth/tg/status/?token={body["token"]}').json() == {
            'status': 'confirming'
        }
        u.refresh_from_db()
        assert u.telegram_id is None  # not linked before confirmation

        press(f'tglink:{body["token"]}', 4321)

        u.refresh_from_db()
        assert u.telegram_id == 4321
        assert Client().get(f'/api/auth/tg/status/?token={body["token"]}').json() == {
            'status': 'linked'
        }

    def test_decline_links_nothing(self, db, bot):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        _, body = self._begin(u)
        start(body['token'], 4321)
        press(f'tgcancel:{body["token"]}', 4321)
        u.refresh_from_db()
        assert u.telegram_id is None
        assert Client().get(f'/api/auth/tg/status/?token={body["token"]}').json() == {
            'status': 'expired'
        }

    def test_another_telegram_account_cannot_press_confirm(self, db, bot):
        """Someone else hitting the callback with the token must not link their id (or any)."""
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        _, body = self._begin(u)
        start(body['token'], 4321)
        press(f'tglink:{body["token"]}', 9999)
        u.refresh_from_db()
        assert u.telegram_id is None

    def test_confirm_without_start_does_nothing(self, db, bot):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        _, body = self._begin(u)
        press(f'tglink:{body["token"]}', 4321)
        u.refresh_from_db()
        assert u.telegram_id is None

    def test_telegram_of_another_account_conflicts(self, db, bot):
        tg_only('owner.one', tg_id=4321)
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        _, body = self._begin(u)
        start(body['token'], 4321)
        press(f'tglink:{body["token"]}', 4321)
        u.refresh_from_db()
        assert u.telegram_id is None
        assert Client().get(f'/api/auth/tg/status/?token={body["token"]}').json() == {
            'status': 'conflict'
        }

    def test_a_link_session_cannot_be_used_to_sign_in(self, db, bot):
        u = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        _, body = self._begin(u)
        start(body['token'], 4321)
        press(f'tglink:{body["token"]}', 4321)
        resp = jpost(Client(), '/api/auth/tg/complete/', {'token': body['token']})
        assert resp.status_code == 401

    def test_start_is_refused_when_telegram_is_already_linked(self, db):
        u = tg_only()
        assert jpost(authed(u), '/api/auth/linked/telegram/start/').status_code == 409

    def test_start_requires_login(self, db):
        assert jpost(Client(), '/api/auth/linked/telegram/start/').status_code == 401

    def test_login_sessions_still_ask_for_the_phone(self, db, bot):
        token = dl.create_session()['token']
        start(token, 4321)
        bot.contact.assert_called_once()
        bot.confirm.assert_not_called()


@pytest.mark.integration
class TestTelegramSignupCreatesNamedAccount:
    def test_deeplink_signup_uses_name_for_username(self, db, bot):
        token = dl.create_session()['token']
        start(token, 4321)
        resp = webhook(
            {
                'message': {
                    'from': {'id': 4321, 'first_name': 'Алишер', 'last_name': 'Қодиров'},
                    'contact': {'phone_number': '+998901234567', 'user_id': 4321},
                }
            }
        )
        assert resp.status_code == 200
        u = CustomUser.objects.get(telegram_id=4321)
        assert u.username == 'alisher.qodirov'
        assert u.username_is_auto is True
        assert (u.first_name, u.last_name) == ('Алишер', 'Қодиров')
        assert u.terms_accepted_at is not None


# ── Google (allauth adapter) ────────────────────────────────


def make_request(user=None):
    rf = RequestFactory()
    request = rf.get('/accounts/google/login/callback/', HTTP_USER_AGENT='Mozilla/5.0 Chrome/120')
    request.user = user or AnonymousUser()
    request.session = SessionStore()
    return request


def google_login(email='g@example.com', uid='g-1', verified=True, first='Gulnora', last='Karimova'):
    sl = SocialLogin(
        user=CustomUser(),
        account=SocialAccount(
            provider='google',
            uid=uid,
            extra_data={
                'email': email,
                'email_verified': verified,
                'given_name': first,
                'family_name': last,
            },
        ),
        email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
    )
    return sl


@pytest.mark.integration
class TestGoogleAdapter:
    def test_new_google_user_gets_name_based_username_and_verified_email(self, db):
        adapter = SocialAccountAdapter()
        request = make_request()
        sl = google_login()
        adapter.pre_social_login(request, sl)
        adapter.populate_user(
            request,
            sl,
            {'email': 'g@example.com', 'first_name': 'Gulnora', 'last_name': 'Karimova'},
        )
        adapter.save_user(request, sl)

        user = CustomUser.objects.get(email='g@example.com')
        assert user.username == 'gulnora.karimova' and user.username_is_auto
        assert not user.has_usable_password()
        assert user.terms_accepted_at is not None
        assert EmailAddress.objects.get(user=user).verified is True

    def test_pre_hijacking_unverified_email_is_not_auto_linked(self, db):
        """Attacker pre-registers the victim's address; the victim's Google login must not
        land in the attacker's account."""
        attacker = CustomUser.objects.create_user(
            username='attacker', email='victim@example.com', password=STRONG
        )
        adapter = SocialAccountAdapter()
        request = make_request()
        sl = google_login(email='victim@example.com', uid='g-victim', first='Vik', last='Tim')

        adapter.pre_social_login(request, sl)

        assert not SocialAccount.objects.filter(user=attacker).exists()
        assert not sl.is_existing  # still a brand-new account
        attacker.refresh_from_db()
        assert attacker.email is None  # the squatter lost the address

        adapter.populate_user(
            request, sl, {'email': 'victim@example.com', 'first_name': 'Vik', 'last_name': 'Tim'}
        )
        adapter.save_user(request, sl)
        victim = CustomUser.objects.get(email='victim@example.com')
        assert victim.pk != attacker.pk
        assert not attacker.check_password('') and attacker.check_password(STRONG)
        assert not SocialAccount.objects.filter(user=attacker).exists()

    def test_verified_email_is_auto_linked(self, db):
        owner = CustomUser.objects.create_user(
            username='real.owner', email='owner@example.com', password=STRONG
        )
        EmailAddress.objects.create(
            user=owner, email='owner@example.com', verified=True, primary=True
        )
        adapter = SocialAccountAdapter()
        sl = google_login(email='owner@example.com', uid='g-owner')

        adapter.pre_social_login(make_request(), sl)

        assert SocialAccount.objects.filter(user=owner, provider='google', uid='g-owner').exists()
        assert CustomUser.objects.count() == 1

    def test_inactive_verified_account_is_not_linked(self, db):
        owner = CustomUser.objects.create_user(
            username='blocked.one', email='owner@example.com', password=STRONG, is_active=False
        )
        EmailAddress.objects.create(
            user=owner, email='owner@example.com', verified=True, primary=True
        )
        sl = google_login(email='owner@example.com', uid='g-owner')
        SocialAccountAdapter().pre_social_login(make_request(), sl)
        assert not SocialAccount.objects.filter(user=owner).exists()

    def test_unverified_google_email_never_matches(self, db):
        other = CustomUser.objects.create_user(
            username='someone', email='x@example.com', password=STRONG
        )
        EmailAddress.objects.create(user=other, email='x@example.com', verified=True, primary=True)
        sl = google_login(email='x@example.com', uid='g-x', verified=False)
        SocialAccountAdapter().pre_social_login(make_request(), sl)
        assert not SocialAccount.objects.filter(user=other).exists()
        other.refresh_from_db()
        assert other.email == 'x@example.com'

    def test_connect_flow_from_the_profile_is_left_to_allauth(self, db):
        user = CustomUser.objects.create_user(
            username='pw.user', email='mine@example.com', password=STRONG
        )
        sl = google_login(email='someone.else@example.com', uid='g-me')
        SocialAccountAdapter().pre_social_login(make_request(user), sl)
        assert not SocialAccount.objects.exists()

    def test_login_issues_a_device_session_and_drops_the_django_session(self, db):
        user = CustomUser.objects.create_user(username='pw.user', password=STRONG)
        request = make_request()
        request.session['marker'] = 'x'
        AccountAdapter().login(request, user)

        assert request._jwt_access and request._jwt_refresh
        assert DeviceSession.objects.filter(user=user, remember=True).count() == 1
        assert 'marker' not in request.session  # no Django-session login survives


@pytest.mark.integration
class TestAllauthSurface:
    """Only Google sign-in is exposed; allauth's own pages (incl. email reset) are 404."""

    @pytest.mark.parametrize(
        'path',
        [
            '/accounts/login/',
            '/accounts/signup/',
            '/accounts/password/reset/',
            '/accounts/password/change/',
            '/accounts/logout/',
            '/accounts/email/',
            '/accounts/confirm-email/abc/',
            '/accounts/3rdparty/',
            '/accounts/yandex/login/',
            '/accounts/apple/login/',
        ],
    )
    def test_blocked(self, db, client, path):
        assert client.get(path).status_code == 404
        assert client.post(path, {}).status_code == 404

    def test_google_login_is_still_routed(self, db, client):
        resp = client.get('/accounts/google/login/')
        assert resp.status_code != 404

    def test_yandex_and_apple_are_not_installed(self, settings):
        assert not any('yandex' in a or 'apple' in a for a in settings.INSTALLED_APPS)
        assert set(settings.SOCIALACCOUNT_PROVIDERS) == {'google'}
