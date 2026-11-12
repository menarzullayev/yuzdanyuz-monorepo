"""Bot admin: encrypted token (never returned), DB-over-env resolution, mocked Bot API actions."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from apps.ops.models import BotSettings
from apps.ops.services import bot_config
from tests.integration._ops import client_for, jpatch, jpost, staff_user

pytestmark = pytest.mark.integration

BOT = '/api/v1/ops/bot/'
TOKEN = '123456789:AAH-fake_token_for_tests_0123456789abcd'
POST = 'apps.accounts.services.bot_sender.requests.post'


@pytest.fixture(autouse=True)
def isolate(db):
    from django.core.cache import cache

    cache.clear()
    # No test may reach api.telegram.org.
    with patch(POST, side_effect=AssertionError('unmocked Bot API call')) as guard:
        yield guard


@pytest.fixture
def staff(db):
    return staff_user()


def _api(ok=True, result=None, description=''):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'ok': ok, 'result': result or {}, 'description': description}
    return resp


class TestSettings:
    def test_staff_only(self, db, user):
        assert Client().get(BOT).status_code in (401, 403)
        assert client_for(user).get(BOT).status_code == 403
        assert jpatch(client_for(user), BOT, {'token': TOKEN}).status_code == 403
        assert not BotSettings.objects.exists()

    def test_token_encrypted_and_never_returned(self, staff):
        c = client_for(staff)
        resp = jpatch(c, BOT, {'token': TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body['has_token'] and body['token_last4'] == TOKEN[-4:]
        assert body['token_source'] == 'db'
        assert TOKEN not in resp.content.decode()
        assert TOKEN not in c.get(BOT).content.decode()
        row = BotSettings.objects.get(pk=1)
        assert row.token_enc.startswith('enc1:') and TOKEN not in row.token_enc
        assert row.updated_by_id == staff.pk

    @pytest.mark.parametrize('bad', ['', 'short', '123:abc', 'x' * 50, 12345, None])
    def test_invalid_token_rejected(self, staff, bad):
        assert jpatch(client_for(staff), BOT, {'token': bad}).status_code == 400
        assert not BotSettings.objects.filter(token_enc__gt='').exists()

    def test_env_is_the_fallback(self, staff, settings):
        settings.TELEGRAM_BOT_TOKEN = 'env-token-9999'
        body = client_for(staff).get(BOT).json()
        assert body['token_last4'] == '9999' and body['token_source'] == 'env'

    def test_db_overrides_env_in_the_sender(self, staff, settings, isolate):
        from apps.accounts.services.bot_sender import send_message

        settings.TELEGRAM_BOT_TOKEN = 'env-token-9999'
        isolate.side_effect = None
        isolate.return_value = _api()
        send_message(1, 'hi')
        assert 'botenv-token-9999/' in isolate.call_args.args[0]
        jpatch(client_for(staff), BOT, {'token': TOKEN})
        send_message(1, 'hi')
        assert f'bot{TOKEN}/' in isolate.call_args.args[0]

    def test_webhook_secret_db_overrides_env(self, staff, settings):
        settings.TELEGRAM_WEBHOOK_SECRET = 'env-secret'
        url = '/api/bot/webhook/'

        def hit(secret):
            return (
                Client()
                .post(
                    url,
                    data='{}',
                    content_type='application/json',
                    HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
                )
                .status_code
            )

        assert hit('env-secret') == 200 and hit('nope') == 403
        jpatch(client_for(staff), BOT, {'regenerate_secret': True})
        new = bot_config.get_webhook_secret()
        assert new and new != 'env-secret'
        assert hit(new) == 200
        assert hit('env-secret') == 403  # the env value no longer works once the DB overrides it

    def test_secret_never_returned_only_last4(self, staff):
        c = client_for(staff)
        resp = jpatch(c, BOT, {'regenerate_secret': True})
        secret = bot_config.get_webhook_secret()
        assert resp.json()['webhook_secret_last4'] == secret[-4:]
        assert secret not in resp.content.decode()
        assert secret not in BotSettings.objects.get(pk=1).webhook_secret_enc

    def test_webhook_url_and_chat_ids_validation(self, staff):
        c = client_for(staff)
        assert jpatch(c, BOT, {'webhook_url': 'http://insecure.example.com/h'}).status_code == 400
        assert jpatch(c, BOT, {'webhook_url': 'https://user:pw@x.example.com/h'}).status_code == 400
        assert (
            jpatch(c, BOT, {'webhook_url': 'https://x.example.com/api/bot/webhook/'}).status_code
            == 200
        )
        assert jpatch(c, BOT, {'admin_chat_ids': ['1']}).status_code == 400
        assert jpatch(c, BOT, {'admin_chat_ids': [True]}).status_code == 400
        assert jpatch(c, BOT, {'admin_chat_ids': list(range(21))}).status_code == 400
        assert jpatch(c, BOT, {'admin_chat_ids': [5, -100200]}).json()['admin_chat_ids'] == [
            5,
            -100200,
        ]
        assert jpatch(c, BOT, {'channel_id': 'bad channel'}).status_code == 400
        assert jpatch(c, BOT, {'channel_id': '@my_channel'}).json()['channel_id'] == '@my_channel'

    def test_admin_chat_ids_env_fallback(self, db, settings):
        settings.OPS_ADMIN_CHAT_IDS = [42]
        bot_config.invalidate()
        assert bot_config.get_admin_chat_ids() == [42]


class TestSetWebhook:
    def _configure(self, c):
        jpatch(c, BOT, {'token': TOKEN, 'webhook_url': 'https://app.example.com/api/bot/webhook/'})

    def test_generates_secret_and_calls_set_webhook(self, staff, isolate):
        c = client_for(staff)
        self._configure(c)
        isolate.side_effect = None
        isolate.return_value = _api(description='Webhook was set')
        resp = jpost(c, BOT + 'set-webhook/')
        assert resp.status_code == 200 and resp.json()['ok'] is True
        url = isolate.call_args.args[0]
        payload = isolate.call_args.kwargs['json']
        assert url == f'https://api.telegram.org/bot{TOKEN}/setWebhook'
        assert payload['url'] == 'https://app.example.com/api/bot/webhook/'
        assert payload['secret_token'] == bot_config.get_webhook_secret() != ''
        assert payload['secret_token'] not in resp.content.decode()
        assert TOKEN not in resp.content.decode()

    def test_existing_secret_is_kept(self, staff, isolate):
        c = client_for(staff)
        self._configure(c)
        jpatch(c, BOT, {'regenerate_secret': True})
        before = bot_config.get_webhook_secret()
        isolate.side_effect = None
        isolate.return_value = _api()
        jpost(c, BOT + 'set-webhook/')
        assert bot_config.get_webhook_secret() == before

    def test_telegram_failure_is_502(self, staff, isolate):
        c = client_for(staff)
        self._configure(c)
        isolate.side_effect = None
        isolate.return_value = _api(ok=False, description='Bad webhook: HTTPS required')
        resp = jpost(c, BOT + 'set-webhook/')
        assert resp.status_code == 502 and resp.json()['ok'] is False

    def test_network_error_is_502_and_hides_token(self, staff, isolate):
        import requests

        c = client_for(staff)
        self._configure(c)
        isolate.side_effect = requests.ConnectionError(f'boom {TOKEN}')
        resp = jpost(c, BOT + 'set-webhook/')
        assert resp.status_code == 502 and TOKEN not in resp.content.decode()

    def test_requires_https_url_and_token(self, staff, isolate):
        c = client_for(staff)
        assert jpost(c, BOT + 'set-webhook/').status_code == 400  # no url
        assert (
            jpost(c, BOT + 'set-webhook/', {'webhook_url': 'http://x.example.com'}).status_code
            == 400
        )
        isolate.assert_not_called()

    def test_without_token_no_call(self, staff, isolate, settings):
        settings.TELEGRAM_BOT_TOKEN = ''
        bot_config.invalidate()
        resp = jpost(
            client_for(staff), BOT + 'set-webhook/', {'webhook_url': 'https://x.example.com/h'}
        )
        assert resp.status_code == 400
        isolate.assert_not_called()

    def test_staff_only(self, db, user, isolate):
        assert jpost(client_for(user), BOT + 'set-webhook/').status_code == 403
        assert client_for(user).get(BOT + 'webhook-status/').status_code == 403
        isolate.assert_not_called()


class TestWebhookStatus:
    def test_sanitised_info(self, staff, isolate):
        c = client_for(staff)
        jpatch(c, BOT, {'token': TOKEN})
        isolate.side_effect = None
        isolate.return_value = _api(
            result={
                'url': 'https://app.example.com/h',
                'pending_update_count': 3,
                'last_error_message': 'Connection timed out',
                'ip_address': '149.154.167.99',
                'has_custom_certificate': False,
            }
        )
        body = c.get(BOT + 'webhook-status/').json()
        assert body['ok'] and body['pending_update_count'] == 3
        assert body['last_error_message'] == 'Connection timed out'
        assert 'ip_address' not in body
        assert isolate.call_args.args[0].endswith('/getWebhookInfo')

    def test_upstream_failure(self, staff, isolate):
        c = client_for(staff)
        jpatch(c, BOT, {'token': TOKEN})
        isolate.side_effect = None
        isolate.return_value = _api(ok=False, description='Unauthorized')
        assert c.get(BOT + 'webhook-status/').status_code == 502


class TestPostToChannel:
    URL = BOT + 'post-to-channel/'

    def _ready(self, c, isolate):
        jpatch(c, BOT, {'token': TOKEN, 'channel_id': '@my_channel'})
        isolate.side_effect = None
        isolate.return_value = _api(result={'message_id': 77})

    def test_sends_text_with_button(self, staff, isolate):
        c = client_for(staff)
        self._ready(c, isolate)
        resp = jpost(
            c,
            self.URL,
            {'text': 'Yangilik!', 'button': {'text': 'Och', 'url': 'https://x.example.com'}},
        )
        assert resp.status_code == 200 and resp.json() == {'ok': True, 'message_id': 77}
        payload = isolate.call_args.kwargs['json']
        assert payload['chat_id'] == '@my_channel' and payload['text'] == 'Yangilik!'
        assert payload['reply_markup']['inline_keyboard'][0][0]['url'] == 'https://x.example.com'
        assert 'parse_mode' not in payload  # plain text: no markup injection

    def test_text_length_limit(self, staff, isolate):
        c = client_for(staff)
        self._ready(c, isolate)
        assert jpost(c, self.URL, {'text': 'a' * 3500}).status_code == 200
        isolate.reset_mock()
        for bad in ('a' * 3501, '', '   ', None, 5):
            assert jpost(c, self.URL, {'text': bad}).status_code == 400
        isolate.assert_not_called()

    @pytest.mark.parametrize(
        'button',
        [
            {'text': 'x', 'url': 'http://insecure.example.com'},
            {'text': 'x', 'url': 'javascript:alert(1)'},
            {'text': '', 'url': 'https://x.example.com'},
            {'text': 'y' * 65, 'url': 'https://x.example.com'},
            {'url': 'https://x.example.com'},
            'oops',
        ],
    )
    def test_bad_buttons_rejected(self, staff, isolate, button):
        c = client_for(staff)
        self._ready(c, isolate)
        isolate.reset_mock()
        assert jpost(c, self.URL, {'text': 'hi', 'button': button}).status_code == 400
        isolate.assert_not_called()

    def test_channel_must_be_configured(self, staff, isolate, settings):
        settings.OPS_TELEGRAM_CHANNEL_ID = ''
        c = client_for(staff)
        jpatch(c, BOT, {'token': TOKEN})
        resp = jpost(c, self.URL, {'text': 'hi'})
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'channel_not_configured'

    def test_upstream_error_is_502(self, staff, isolate):
        c = client_for(staff)
        self._ready(c, isolate)
        isolate.return_value = _api(ok=False, description='Forbidden: bot is not a member')
        assert jpost(c, self.URL, {'text': 'hi'}).status_code == 502

    def test_only_staff_may_post(self, db, user, isolate):
        assert jpost(client_for(user), self.URL, {'text': 'hi'}).status_code == 403
        assert Client().post(
            self.URL, data=json.dumps({'text': 'hi'}), content_type='application/json'
        ).status_code in (401, 403)
        isolate.assert_not_called()
