"""C22 — right to erasure (with confirmation + cooling-off) and data export.

Erasure is never immediate: the user re-authenticates (password) or confirms a code sent
by the Telegram bot, then the request waits out a cooling-off period during which it can
be cancelled. Only then a scheduled task anonymises the account, including the
simple_history rows that still hold the old email/phone/name.
"""

import logging
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import DeviceSession, ErasureRequest, OTPCode

User = get_user_model()

ERASE = '/api/auth/gdpr/erasure/'
CONFIRM = '/api/auth/gdpr/erasure/confirm/'
CANCEL = '/api/auth/gdpr/erasure/cancel/'
EXPORT = '/api/auth/gdpr/export/'

PII = {
    'email': 'victim.person@example.com',
    'phone_number': '+998901234567',
    'first_name': 'Alisher',
    'last_name': 'Navoiy',
    'telegram_username': 'alisher_tg',
}


@pytest.fixture
def person(db):
    u = User.objects.create_user(username='alisher01', password='Str0ng-pass!', **PII)
    u.telegram_id = 555000111
    u.telegram_photo_url = 'https://t.me/i/userpic/alisher.jpg'
    u.save()
    # a second history row with an older email
    u.email = 'old.address@example.com'
    u.save()
    u.email = PII['email']
    u.save()
    return u


@pytest.fixture
def api(person):
    c = APIClient()
    c.force_authenticate(person)
    return c


@pytest.fixture
def sent(monkeypatch):
    messages = []

    def fake_send(telegram_id, text):
        messages.append((telegram_id, text))
        return True

    monkeypatch.setattr('apps.accounts.services.bot_sender.send_message', fake_send)
    return messages


def _code(sent):
    import re

    return re.search(r'\b(\d{6})\b', sent[-1][1]).group(1)


def _run_due(hours=0):
    from apps.accounts.tasks import process_due_erasures

    return process_due_erasures.apply(
        kwargs={'now': (timezone.now() + timedelta(hours=hours)).isoformat()}
    ).get()


def _history_text(user_id):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT email, phone_number, first_name, last_name, username, telegram_username, '
            'telegram_photo_url FROM accounts_historicalcustomuser WHERE id = %s',
            [user_id],
        )
        return ' '.join(str(v) for row in cur.fetchall() for v in row)


# ── Request + confirmation ──────────────────────────────────


@pytest.mark.django_db
class TestRequest:
    def test_confirm_flag_alone_does_not_erase(self, api, person):
        api.post(ERASE, {'confirm': True}, format='json')
        person.refresh_from_db()
        assert person.is_active and person.email == PII['email']

    def test_wrong_password_is_refused(self, api, person):
        resp = api.post(ERASE, {'password': 'nope'}, format='json')
        assert resp.status_code == 400
        assert not ErasureRequest.objects.filter(user=person).exists()

    def test_password_reauth_schedules_after_cooling_off(self, api, person, settings):
        settings.GDPR_ERASURE_COOLING_OFF_HOURS = 72
        resp = api.post(ERASE, {'password': 'Str0ng-pass!'}, format='json')
        assert resp.status_code == 202, resp.content
        req = ErasureRequest.objects.get(user=person)
        assert req.status == ErasureRequest.Status.SCHEDULED
        assert req.scheduled_for - req.confirmed_at == timedelta(hours=72)
        person.refresh_from_db()
        assert person.is_active and person.email == PII['email']

    def test_telegram_code_flow(self, api, person, sent):
        person.set_unusable_password()
        person.save()
        resp = api.post(ERASE, {}, format='json')
        assert resp.status_code == 202 and resp.json()['confirm_via'] == 'telegram'
        assert sent and sent[-1][0] == person.telegram_id
        assert api.post(CONFIRM, {'code': '000000'}, format='json').status_code == 400
        assert api.post(CONFIRM, {'code': _code(sent)}, format='json').status_code == 200
        assert ErasureRequest.objects.get(user=person).status == ErasureRequest.Status.SCHEDULED

    def test_code_is_stored_hashed(self, api, person, sent):
        person.set_unusable_password()
        person.save()
        api.post(ERASE, {}, format='json')
        req = ErasureRequest.objects.get(user=person)
        assert _code(sent) not in (req.code_hash or '')

    def test_too_many_wrong_codes_lock_the_request(self, api, person, sent):
        person.set_unusable_password()
        person.save()
        api.post(ERASE, {}, format='json')
        for _ in range(5):
            api.post(CONFIRM, {'code': '000000'}, format='json')
        assert api.post(CONFIRM, {'code': _code(sent)}, format='json').status_code == 400

    def test_no_password_and_no_telegram_needs_another_factor(self, db):
        u = User.objects.create_user(username='nofactor', email='nf@example.com')
        u.set_unusable_password()
        u.save()
        c = APIClient()
        c.force_authenticate(u)
        resp = c.post(ERASE, {}, format='json')
        assert resp.status_code == 400
        assert resp.json()['error']['code'] == 'reauth_required'

    def test_anonymous_is_rejected(self, db):
        assert APIClient().post(ERASE, {}, format='json').status_code in (401, 403)


# ── Cooling-off, cancel, execution ──────────────────────────


@pytest.mark.django_db
class TestExecution:
    def _schedule(self, api):
        assert api.post(ERASE, {'password': 'Str0ng-pass!'}, format='json').status_code == 202

    def test_nothing_happens_before_the_cooling_off_ends(self, api, person):
        self._schedule(api)
        _run_due(hours=1)
        person.refresh_from_db()
        assert person.email == PII['email']

    def test_cancel_during_cooling_off(self, api, person):
        self._schedule(api)
        assert api.post(CANCEL, {}, format='json').status_code == 200
        _run_due(hours=24 * 30)
        person.refresh_from_db()
        assert person.is_active and person.email == PII['email']
        assert ErasureRequest.objects.get(user=person).status == ErasureRequest.Status.CANCELLED

    def test_erasure_removes_pii_including_history(self, api, person, tmp_path, settings):
        from django.core.files.base import ContentFile

        settings.MEDIA_ROOT = tmp_path
        person.avatar.save('a.png', ContentFile(b'png'), save=True)
        avatar_path = person.avatar.path
        DeviceSession.objects.create(
            user=person,
            device_id='d' * 32,
            refresh_hash='h' * 64,
            user_agent='Mozilla/5.0 victim',
            ip_address='10.1.2.3',
            expires_at=timezone.now() + timedelta(days=1),
        )
        OTPCode.objects.create(
            phone=PII['phone_number'], code_hash='x' * 64, expires_at=timezone.now()
        )
        assert PII['email'] in _history_text(person.pk)

        self._schedule(api)
        _run_due(hours=24 * 30)

        person.refresh_from_db()
        assert not person.is_active and not person.has_usable_password()
        current = (
            f'{person.email} {person.phone_number} {person.first_name} '
            f'{person.last_name} {person.username} {person.telegram_username}'
        )
        for value in [*PII.values(), 'alisher01']:
            assert value not in current, value
        assert person.telegram_id is None and person.telegram_photo_url in (None, '')
        assert not person.avatar
        import os

        assert not os.path.exists(avatar_path)

        history = _history_text(person.pk)
        for value in [*PII.values(), 'old.address@example.com', 'alisher01', 'userpic']:
            assert value not in history, value
        assert not DeviceSession.objects.filter(user=person).exists()
        assert not OTPCode.objects.filter(phone=PII['phone_number']).exists()

        req = ErasureRequest.objects.get(user=person)
        assert req.status == ErasureRequest.Status.COMPLETED
        assert req.completed_at and 'CustomUser' in req.redacted

    def test_failure_rolls_back_and_is_recorded(self, api, person, monkeypatch):
        from apps.accounts.services import erasure

        def boom(user):
            raise RuntimeError('history purge failed')

        monkeypatch.setattr(erasure, '_purge_history', boom)
        self._schedule(api)
        with pytest.raises(RuntimeError):
            erasure.run_due(now=timezone.now() + timedelta(days=30), raise_errors=True)
        person.refresh_from_db()
        assert person.email == PII['email']  # atomic: nothing half-erased
        req = ErasureRequest.objects.get(user=person)
        assert req.status == ErasureRequest.Status.FAILED
        assert 'history purge failed' in req.error

    def test_erasure_does_not_log_pii(self, api, person, caplog):
        caplog.set_level(logging.DEBUG)
        self._schedule(api)
        _run_due(hours=24 * 30)
        assert PII['email'] not in caplog.text
        assert PII['phone_number'] not in caplog.text


# ── Export ──────────────────────────────────────────────────


@pytest.mark.django_db
class TestExport:
    def test_export_returns_own_data_as_json(self, api, person, user2):
        resp = api.get(EXPORT)
        assert resp.status_code == 200
        assert 'attachment' in resp['Content-Disposition']
        data = resp.json()
        assert data['profile']['email'] == PII['email']
        assert data['profile']['phone_number'] == PII['phone_number']
        assert user2.email not in resp.content.decode()
        assert 'password' not in data['profile']
        assert {'profile', 'memberships', 'sessions', 'exam_attempts', 'wallet'} <= set(data)

    def test_export_requires_login(self, db):
        assert APIClient().get(EXPORT).status_code in (401, 403)
