"""
Telegram sign-in must only bind a Telegram account to a user when Telegram
itself attests ownership:

- the bot webhook only accepts updates carrying the configured secret header;
- a shared contact counts only when ``contact.user_id`` equals ``from.id``
  (the user's own number, not a contact card typed in by hand);
- account linking endpoints never trust a client-supplied Telegram id or phone.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from asgiref.sync import async_to_sync
from django.core.checks import Error
from django.test import Client, override_settings

from apps.accounts.models import CustomUser
from apps.accounts.services import telegram_deeplink as dl

WEBHOOK_URL = '/api/bot/webhook/'
SECRET = 'test-webhook-secret'
ATTACKER_TG_ID = 555000111
VICTIM_TG_ID = 777000222
VICTIM_PHONE = '+998901234567'


@pytest.fixture(autouse=True)
def _no_bot_http():
    """The webhook replies through the Bot API; never call it from tests."""
    with (
        patch('apps.accounts.views.telegram_deeplink.send_contact_request'),
        patch('apps.accounts.views.telegram_deeplink.send_message'),
        patch('apps.accounts.views.telegram_deeplink.send_success_message'),
    ):
        yield


@pytest.fixture
def victim(db):
    return CustomUser.objects.create(username='victim', phone_number=VICTIM_PHONE)


def _waiting_session(telegram_id: int) -> str:
    token = dl.create_session()['token']
    assert dl.on_bot_start(token, telegram_id)
    return token


def _post_update(client, update: dict, secret: str | None = SECRET):
    headers = {}
    if secret is not None:
        headers['HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN'] = secret
    return client.post(
        WEBHOOK_URL, data=json.dumps(update), content_type='application/json', **headers
    )


def _contact_update(from_id: int, phone: str, contact_user_id: int | None) -> dict:
    contact = {'phone_number': phone, 'first_name': 'X'}
    if contact_user_id is not None:
        contact['user_id'] = contact_user_id
    return {
        'update_id': 1,
        'message': {'message_id': 1, 'from': {'id': from_id}, 'contact': contact},
    }


# ── Contact ownership ────────────────────────────────────────


@pytest.mark.security
@pytest.mark.django_db
class TestContactOwnership:
    def test_contact_without_user_id_is_rejected(self, victim):
        token = _waiting_session(ATTACKER_TG_ID)

        assert dl.on_bot_contact(ATTACKER_TG_ID, VICTIM_PHONE.lstrip('+'), None) is False

        victim.refresh_from_db()
        assert victim.telegram_id is None
        assert dl.get_verified_user_pk(token) is None
        assert not CustomUser.objects.filter(telegram_id=ATTACKER_TG_ID).exists()

    def test_contact_of_another_telegram_user_is_rejected(self, victim):
        token = _waiting_session(ATTACKER_TG_ID)

        assert dl.on_bot_contact(ATTACKER_TG_ID, VICTIM_PHONE, VICTIM_TG_ID) is False

        victim.refresh_from_db()
        assert victim.telegram_id is None
        assert dl.get_verified_user_pk(token) is None

    def test_own_contact_is_accepted(self, victim):
        token = _waiting_session(VICTIM_TG_ID)

        assert dl.on_bot_contact(VICTIM_TG_ID, VICTIM_PHONE, VICTIM_TG_ID) is True

        victim.refresh_from_db()
        assert victim.telegram_id == VICTIM_TG_ID
        assert dl.get_verified_user_pk(token) == str(victim.pk)

    def test_phone_owned_by_other_telegram_account_is_not_rebound(self, victim):
        """A recycled number must not move an account to a new Telegram id."""
        victim.telegram_id = VICTIM_TG_ID
        victim.save(update_fields=['telegram_id'])
        token = _waiting_session(ATTACKER_TG_ID)

        assert dl.on_bot_contact(ATTACKER_TG_ID, VICTIM_PHONE, ATTACKER_TG_ID) is False

        victim.refresh_from_db()
        assert victim.telegram_id == VICTIM_TG_ID
        assert dl.get_verified_user_pk(token) is None

    def test_webhook_contact_without_user_id_does_not_bind(self, victim):
        client = Client()
        token = _waiting_session(ATTACKER_TG_ID)

        response = _post_update(client, _contact_update(ATTACKER_TG_ID, VICTIM_PHONE, None))

        assert response.status_code == 200
        victim.refresh_from_db()
        assert victim.telegram_id is None
        assert dl.get_verified_user_pk(token) is None


# ── Webhook secret ───────────────────────────────────────────


@pytest.mark.security
@pytest.mark.django_db
class TestWebhookSecret:
    def _update_that_would_bind(self):
        _waiting_session(VICTIM_TG_ID)
        return _contact_update(VICTIM_TG_ID, VICTIM_PHONE, VICTIM_TG_ID)

    def test_missing_secret_header_is_forbidden(self, victim):
        response = _post_update(Client(), self._update_that_would_bind(), secret=None)

        assert response.status_code == 403
        victim.refresh_from_db()
        assert victim.telegram_id is None

    def test_wrong_secret_header_is_forbidden(self, victim):
        response = _post_update(Client(), self._update_that_would_bind(), secret='wrong')

        assert response.status_code == 403
        victim.refresh_from_db()
        assert victim.telegram_id is None

    @override_settings(TELEGRAM_WEBHOOK_SECRET='')
    def test_unconfigured_secret_rejects_every_update(self, victim):
        response = _post_update(Client(), self._update_that_would_bind(), secret='')

        assert response.status_code == 403
        victim.refresh_from_db()
        assert victim.telegram_id is None

    def test_correct_secret_is_accepted(self, victim):
        response = _post_update(Client(), self._update_that_would_bind())

        assert response.status_code == 200
        victim.refresh_from_db()
        assert victim.telegram_id == VICTIM_TG_ID


@pytest.mark.security
class TestWebhookSecretSystemCheck:
    def _errors(self):
        from apps.accounts.checks import telegram_webhook_secret_check

        return [m for m in telegram_webhook_secret_check(None) if isinstance(m, Error)]

    @override_settings(DEBUG=False, TELEGRAM_WEBHOOK_SECRET='')
    def test_empty_secret_outside_debug_is_an_error(self):
        errors = self._errors()
        assert [e.id for e in errors] == ['accounts.E001']

    @override_settings(DEBUG=False, TELEGRAM_WEBHOOK_SECRET='s3cret')
    def test_configured_secret_passes(self):
        assert self._errors() == []

    @override_settings(DEBUG=True, TELEGRAM_WEBHOOK_SECRET='')
    def test_debug_does_not_block_local_development(self):
        assert self._errors() == []


# ── Account linking ──────────────────────────────────────────


def _signed_init_data(tg_user: dict, bot_token: str) -> str:
    params = {
        'auth_date': str(int(time.time())),
        'user': json.dumps(tg_user, separators=(',', ':')),
    }
    check = '\n'.join(f'{k}={v}' for k, v in sorted(params.items()))
    secret_key = hmac.new(b'WebAppData', bot_token.encode(), hashlib.sha256).digest()
    params['hash'] = hmac.new(secret_key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(params)


@pytest.mark.security
@pytest.mark.django_db
class TestLinkingRequiresProof:
    @pytest.fixture
    def attacker_client(self, member):
        # /api/linking/ is only open to org members, so the attacker is one.
        attacker = member.user
        client = Client()
        client.force_login(attacker)
        return client, attacker

    def test_link_telegram_with_bare_id_is_rejected(self, attacker_client):
        client, attacker = attacker_client

        response = client.post('/api/linking/telegram/', {'telegram_id': str(VICTIM_TG_ID)})

        assert response.status_code == 400
        attacker.refresh_from_db()
        assert attacker.telegram_id is None

    def test_link_telegram_with_forged_init_data_is_rejected(self, attacker_client):
        client, attacker = attacker_client
        forged = _signed_init_data({'id': VICTIM_TG_ID}, 'not-the-bot-token')

        response = client.post('/api/linking/telegram/', {'init_data': forged})

        assert response.status_code == 400
        attacker.refresh_from_db()
        assert attacker.telegram_id is None

    def test_link_telegram_with_signed_init_data_binds_that_id(self, attacker_client, settings):
        client, user = attacker_client
        init_data = _signed_init_data({'id': VICTIM_TG_ID}, settings.TELEGRAM_BOT_TOKEN)

        response = client.post(
            '/api/linking/telegram/', {'init_data': init_data, 'telegram_id': '1'}
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.telegram_id == VICTIM_TG_ID

    def test_link_phone_without_verification_is_rejected(self, attacker_client):
        client, attacker = attacker_client

        response = client.post('/api/linking/phone/', {'phone': VICTIM_PHONE})

        assert response.status_code >= 400
        attacker.refresh_from_db()
        assert not attacker.phone_number


# ── WebSocket JWT auth ───────────────────────────────────────


@pytest.mark.security
@pytest.mark.django_db(transaction=True)
def test_ws_auth_accepts_valid_access_token(victim):
    from apps.accounts.services.token_service import create_token_pair
    from core.ws_auth import _verify_token_and_get_user

    access, _refresh = create_token_pair(victim, 'fp')

    assert async_to_sync(_verify_token_and_get_user)(access) == victim
    assert async_to_sync(_verify_token_and_get_user)('garbage') is None
