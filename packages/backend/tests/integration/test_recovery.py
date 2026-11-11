"""Password recovery: Telegram one-time link, org-admin temporary password, admin queue."""

import itertools
import json
import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import CustomUser, PasswordResetRequest, RecoveryToken
from apps.accounts.services import recovery
from apps.organizations.models import Membership, MembershipStatus, OrgRole

STRONG = 'Tr0ub4dor&horse'
NEW = 'N3w-passw0rd!x'


_ips = itertools.count(1)


def j(client, url, payload=None):
    # A distinct peer per call: these endpoints sit in the 5/min per-IP tier.
    ip = f'198.51.100.{next(_ips) % 250 + 1}'
    return client.post(
        url, data=json.dumps(payload or {}), content_type='application/json', REMOTE_ADDR=ip
    )


def login(username, password=STRONG, **extra):
    c = Client()
    resp = j(c, '/api/auth/login/', {'username': username, 'password': password, **extra})
    return c, resp


@pytest.fixture
def tg_user(db):
    u = CustomUser(username='tg.person', telegram_id=777)
    u.set_password(STRONG)
    u.save()
    return u


def request_link(username):
    """POST the recovery request and return (response, link sent via the bot or None)."""
    with patch('apps.accounts.services.bot_sender.send_message') as send:
        resp = j(Client(), '/api/auth/recovery/telegram/', {'username': username})
    assert resp.status_code == 202, resp.content
    link = None
    if send.called:
        link = re.search(r'https?://\S+', send.call_args.args[1]).group(0)
    return resp, link


def token_of(link):
    return link.split('token=')[1]


@pytest.mark.integration
class TestTelegramLink:
    def test_link_is_sent_to_the_owners_chat(self, tg_user):
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            resp = j(Client(), '/api/auth/recovery/telegram/', {'username': 'TG.person'})
        assert resp.status_code == 202
        assert send.call_args.args[0] == 777
        assert '/reset-password?token=' in send.call_args.args[1]

    def test_response_is_identical_for_unknown_and_telegramless_accounts(self, tg_user):
        CustomUser.objects.create_user(username='no.telegram', password=STRONG)
        known, _ = request_link('tg.person')
        unknown, link1 = request_link('ghost.user')
        plain, link2 = request_link('no.telegram')
        assert known.status_code == unknown.status_code == plain.status_code == 202
        assert known.content == unknown.content == plain.content
        assert link1 is None and link2 is None

    def test_only_the_hash_of_the_token_is_stored(self, tg_user):
        _, link = request_link('tg.person')
        row = RecoveryToken.objects.get()
        assert token_of(link) not in row.token_hash
        assert row.token_hash == recovery._hash(token_of(link))
        assert abs((row.expires_at - row.created_at) - timedelta(minutes=15)) < timedelta(seconds=2)

    def test_at_most_three_links_per_hour_per_account(self, tg_user):
        links = [request_link('tg.person')[1] for _ in range(5)]
        assert [bool(x) for x in links] == [True, True, True, False, False]

    def test_a_new_link_invalidates_the_previous_one(self, tg_user):
        _, first = request_link('tg.person')
        _, second = request_link('tg.person')
        assert (
            j(
                Client(),
                '/api/auth/recovery/confirm/',
                {'token': token_of(first), 'new_password': NEW},
            ).status_code
            == 400
        )
        assert (
            j(
                Client(),
                '/api/auth/recovery/confirm/',
                {'token': token_of(second), 'new_password': NEW},
            ).status_code
            == 200
        )

    def test_inactive_account_gets_no_link(self, tg_user):
        tg_user.is_active = False
        tg_user.save()
        assert request_link('tg.person')[1] is None

    def test_bot_reset_command_sends_a_link(self, tg_user):
        update = {'message': {'text': '/reset', 'from': {'id': 777}}}
        with patch('apps.accounts.views.telegram_deeplink.send_message') as send:
            resp = Client().post(
                '/api/bot/webhook/',
                data=json.dumps(update),
                content_type='application/json',
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-webhook-secret',
            )
        assert resp.status_code == 200
        assert 'reset-password?token=' in send.call_args.args[1]
        assert RecoveryToken.objects.filter(user=tg_user).count() == 1

    def test_bot_reset_for_unknown_telegram_creates_nothing(self, db):
        update = {'message': {'text': '/reset', 'from': {'id': 999}}}
        with patch('apps.accounts.views.telegram_deeplink.send_message'):
            Client().post(
                '/api/bot/webhook/',
                data=json.dumps(update),
                content_type='application/json',
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='test-webhook-secret',
            )
        assert RecoveryToken.objects.count() == 0


@pytest.mark.integration
class TestConfirm:
    def _link(self):
        return token_of(request_link('tg.person')[1])

    def test_reset_sets_password_revokes_sessions_and_clears_lock(self, tg_user):
        phone, _ = login('tg.person')
        # lock the account first
        for _ in range(6):
            login('tg.person', 'wrong-pass-1')
        token = self._link()

        resp = j(Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW})

        assert resp.status_code == 200
        assert phone.post('/api/auth/refresh/').status_code == 401  # old devices are out
        _, ok = login('tg.person', NEW)
        assert ok.status_code == 200  # lock cleared, new password works
        assert login('tg.person', STRONG)[1].status_code == 400

    def test_token_is_single_use(self, tg_user):
        token = self._link()
        assert (
            j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW}
            ).status_code
            == 200
        )
        again = j(
            Client(),
            '/api/auth/recovery/confirm/',
            {'token': token, 'new_password': 'An0ther-pass!9'},
        )
        assert again.status_code == 400 and again.json()['error']['code'] == 'invalid_token'
        tg_user.refresh_from_db()
        assert tg_user.check_password(NEW)

    def test_expired_token_is_refused(self, tg_user):
        token = self._link()
        RecoveryToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        resp = j(Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW})
        assert resp.status_code == 400
        tg_user.refresh_from_db()
        assert tg_user.check_password(STRONG)

    @pytest.mark.parametrize('token', ['', 'x', 'a' * 43, '../etc'])
    def test_garbage_tokens_are_refused(self, tg_user, token):
        assert (
            j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW}
            ).status_code
            == 400
        )

    def test_new_password_must_meet_the_policy_and_keeps_the_token(self, tg_user):
        token = self._link()
        for weak in ('password1', 'tg.person1', 'short', '123456789'):
            resp = j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': weak}
            )
            assert resp.status_code == 400
            assert resp.json()['error']['details']['new_password']
        tg_user.refresh_from_db()
        assert tg_user.check_password(STRONG)
        # the token survived the failed attempts
        assert (
            j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW}
            ).status_code
            == 200
        )

    def test_inactive_user_cannot_use_a_token(self, tg_user):
        token = self._link()
        tg_user.is_active = False
        tg_user.save()
        assert (
            j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW}
            ).status_code
            == 400
        )

    def test_reset_keeps_two_factor_enabled(self, tg_user):
        """Recovery must not be a way around 2FA."""
        from apps.accounts.services import two_factor as tf

        tf.begin_setup(tg_user)
        from apps.accounts.models import TOTPDevice

        TOTPDevice.objects.filter(user=tg_user).update(confirmed_at=timezone.now())
        token = self._link()
        j(Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW})
        assert tf.is_enabled(tg_user)
        assert login('tg.person', NEW)[1].json() == {'need_code': True}


@pytest.fixture
def org_setup(db, org, org2):
    def member(username, role, organization=org, **kw):
        user = CustomUser.objects.create_user(username=username, password=STRONG, **kw)
        Membership.objects.create(
            user=user,
            organization=organization,
            role=OrgRole.objects.get(organization=organization, name=role),
            status=MembershipStatus.ACTIVE,
            is_primary=True,
        )
        return user

    return member


@pytest.mark.integration
class TestOrgAdminReset:
    def test_manager_issues_a_temporary_password(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        admin_client, _ = login('the.manager')

        resp = j(admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)})

        assert resp.status_code == 200
        temp = resp.json()['temporary_password']
        assert len(temp) >= 12
        student.refresh_from_db()
        assert student.must_change_password and student.check_password(temp)

    def test_login_with_temporary_password_demands_a_change_and_gives_no_session(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        admin_client, _ = login('the.manager')
        temp = j(
            admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)}
        ).json()['temporary_password']

        c, resp = login('the.student', temp)

        assert resp.json() == {'password_change_required': True}
        assert 'access_token' not in resp.cookies
        assert c.get('/api/auth/me/').status_code == 401

    def test_forced_change_replaces_the_password_and_signs_in(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        admin_client, _ = login('the.manager')
        temp = j(
            admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)}
        ).json()['temporary_password']

        c = Client()
        resp = j(
            c,
            '/api/auth/password/change-required/',
            {'username': 'the.student', 'password': temp, 'new_password': NEW},
        )

        assert resp.status_code == 200 and 'access_token' in resp.cookies
        student.refresh_from_db()
        assert not student.must_change_password and student.check_password(NEW)
        assert login('the.student', NEW)[1].json()['user']['username'] == 'the.student'
        assert login('the.student', temp)[1].status_code == 400

    def test_forced_change_rejects_weak_or_unchanged_passwords(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        admin_client, _ = login('the.manager')
        temp = j(
            admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)}
        ).json()['temporary_password']
        for bad in ('password1', temp, 'the.student1'):
            resp = j(
                Client(),
                '/api/auth/password/change-required/',
                {'username': 'the.student', 'password': temp, 'new_password': bad},
            )
            assert resp.status_code == 400, bad
        student.refresh_from_db()
        assert student.must_change_password

    def test_forced_change_needs_the_temporary_password(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        admin_client, _ = login('the.manager')
        j(admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)})
        resp = j(
            Client(),
            '/api/auth/password/change-required/',
            {'username': 'the.student', 'password': 'guess-it-1', 'new_password': NEW},
        )
        assert resp.status_code == 400
        student.refresh_from_db()
        assert student.must_change_password

    def test_forced_change_is_409_for_normal_accounts(self, org_setup):
        org_setup('the.student', 'student')
        resp = j(
            Client(),
            '/api/auth/password/change-required/',
            {'username': 'the.student', 'password': STRONG, 'new_password': NEW},
        )
        assert resp.status_code == 409

    def test_the_reset_kills_the_targets_sessions(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        stu_client, _ = login('the.student')
        admin_client, _ = login('the.manager')
        j(admin_client, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)})
        assert stu_client.post('/api/auth/refresh/').status_code == 401

    def test_a_student_cannot_reset_anyone(self, org_setup):
        org_setup('the.student', 'student')
        other = org_setup('other.student', 'student')
        c, _ = login('the.student')
        assert j(c, '/api/auth/recovery/org-reset/', {'user_id': str(other.pk)}).status_code == 403
        other.refresh_from_db()
        assert not other.must_change_password

    def test_a_manager_of_another_org_cannot_reset(self, org_setup, org2):
        org_setup('foreign.manager', 'manager', organization=org2)
        student = org_setup('the.student', 'student')
        c, _ = login('foreign.manager')
        assert (
            j(c, '/api/auth/recovery/org-reset/', {'user_id': str(student.pk)}).status_code == 403
        )
        student.refresh_from_db()
        assert student.check_password(STRONG) and not student.must_change_password

    def test_cannot_reset_platform_staff_or_self(self, org_setup):
        manager = org_setup('the.manager', 'manager')
        staff = org_setup('the.staff', 'student', is_staff=True)
        c, _ = login('the.manager')
        assert j(c, '/api/auth/recovery/org-reset/', {'user_id': str(staff.pk)}).status_code == 403
        assert (
            j(c, '/api/auth/recovery/org-reset/', {'user_id': str(manager.pk)}).status_code == 403
        )

    def test_unknown_user_is_indistinguishable_from_forbidden(self, org_setup):
        org_setup('the.manager', 'manager')
        c, _ = login('the.manager')
        import uuid

        assert (
            j(c, '/api/auth/recovery/org-reset/', {'user_id': str(uuid.uuid4())}).status_code == 403
        )
        assert j(c, '/api/auth/recovery/org-reset/', {'user_id': 'nope'}).status_code == 400

    def test_supplied_temporary_password_must_meet_the_policy(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        c, _ = login('the.manager')
        resp = j(
            c,
            '/api/auth/recovery/org-reset/',
            {'user_id': str(student.pk), 'temporary_password': 'password1'},
        )
        assert resp.status_code == 400
        student.refresh_from_db()
        assert student.check_password(STRONG)

    def test_anonymous_is_rejected(self, db):
        assert j(Client(), '/api/auth/recovery/org-reset/', {'user_id': 'x'}).status_code == 401

    def test_generated_passwords_pass_the_policy(self, db):
        for _ in range(20):
            from apps.accounts.services.password_policy import password_errors

            assert password_errors(recovery.generate_temporary_password()) == []


@pytest.mark.integration
class TestAdminQueue:
    def test_request_is_queued_and_answer_is_always_202(self, tg_user):
        resp = j(
            Client(),
            '/api/auth/recovery/request/',
            {'username': 'tg.person', 'contact': '+998901112233'},
        )
        assert resp.status_code == 202
        req = PasswordResetRequest.objects.get()
        assert (
            req.user == tg_user and req.status == 'pending' and req.contact_note == '+998901112233'
        )

    def test_unknown_username_is_queued_without_a_user(self, db):
        resp = j(Client(), '/api/auth/recovery/request/', {'username': 'ghost.user'})
        assert resp.status_code == 202
        assert PasswordResetRequest.objects.get().user is None

    def test_duplicates_are_not_queued_twice(self, tg_user):
        for _ in range(2):
            j(Client(), '/api/auth/recovery/request/', {'username': 'tg.person'})
        assert PasswordResetRequest.objects.count() == 1

    def test_admin_action_issues_temp_password_and_closes_the_request(self, tg_user, rf, superuser):
        from django.contrib import admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        j(Client(), '/api/auth/recovery/request/', {'username': 'tg.person'})
        model_admin = admin.site._registry[PasswordResetRequest]
        request = rf.post('/admin/')
        request.user = superuser
        request.session = {}
        request._messages = FallbackStorage(request)

        model_admin.issue_temporary_password(request, PasswordResetRequest.objects.all())

        req = PasswordResetRequest.objects.get()
        assert req.status == 'done' and req.handled_by == superuser
        tg_user.refresh_from_db()
        assert tg_user.must_change_password

    def test_admin_action_never_touches_superusers(self, superuser, rf):
        from django.contrib import admin
        from django.contrib.messages.storage.fallback import FallbackStorage

        PasswordResetRequest.objects.create(user=superuser, username_entered='admin')
        model_admin = admin.site._registry[PasswordResetRequest]
        request = rf.post('/admin/')
        request.user = superuser
        request.session = {}
        request._messages = FallbackStorage(request)
        model_admin.issue_temporary_password(request, PasswordResetRequest.objects.all())
        superuser.refresh_from_db()
        assert not superuser.must_change_password
        assert PasswordResetRequest.objects.get().status == 'pending'
