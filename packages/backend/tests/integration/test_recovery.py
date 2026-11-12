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
        assert '/reset-password#token=' in send.call_args.args[1]

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

    def test_requesting_a_new_link_does_not_kill_the_previous_one(self, tg_user):
        """Anyone who knows a username must not be able to invalidate the owner's link."""
        _, first = request_link('tg.person')
        _, second = request_link('tg.person')
        assert RecoveryToken.objects.filter(user=tg_user, used_at__isnull=True).count() == 2
        assert (
            j(
                Client(),
                '/api/auth/recovery/confirm/',
                {'token': token_of(first), 'new_password': NEW},
            ).status_code
            == 200
        )
        # using one link retires the rest
        again = j(
            Client(),
            '/api/auth/recovery/confirm/',
            {'token': token_of(second), 'new_password': 'An0ther-pass!9'},
        )
        assert again.status_code == 400

    def test_requests_are_limited_per_ip_as_well(self, db):
        users = [
            CustomUser.objects.create_user(
                username=f'tg.user{i}', password=STRONG, telegram_id=900 + i
            )
            for i in range(12)
        ]
        sent = 0
        for u in users:
            with patch('apps.accounts.services.bot_sender.send_message') as send:
                # same client IP every time: only the per-IP hourly cap can stop this
                Client().post(
                    '/api/auth/recovery/telegram/',
                    data=json.dumps({'username': u.username}),
                    content_type='application/json',
                    REMOTE_ADDR='203.0.113.50',
                )
            sent += int(send.called)
            from core.utils import rate_limiter as rl

            rl._redis().delete(
                *rl._redis().keys('rl:*')
            )  # clear the 5/min tier, keep the hourly cap
        assert sent == recovery.IP_REQUESTS_PER_HOUR

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
        assert 'reset-password#token=' in send.call_args.args[1]
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
    """An org admin only STARTS a recovery for a lower-ranked member; the link goes to the
    member's own Telegram and the admin never sees a password."""

    URL = '/api/auth/recovery/org-reset/'

    def _reset(self, client, target, **extra):
        with patch('apps.accounts.services.bot_sender.send_message') as send:
            resp = j(client, self.URL, {'user_id': str(target.pk), **extra})
        return resp, send

    def test_manager_triggers_a_link_for_a_student_and_never_gets_a_password(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student', telegram_id=3131)
        admin_client, _ = login('the.manager')

        resp, send = self._reset(admin_client, student)

        assert resp.status_code == 200
        assert resp.json() == {'ok': True, 'delivered': 'telegram'}
        assert 'password' not in resp.content.decode().lower()
        chat_id, text = send.call_args.args
        assert chat_id == 3131 and 'tashkilot administratori' in text
        assert 'reset-password#token=' in text
        student.refresh_from_db()
        assert student.check_password(STRONG) and not student.must_change_password

    def test_the_link_lets_only_the_member_set_the_password(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student', telegram_id=3131)
        admin_client, _ = login('the.manager')
        _, send = self._reset(admin_client, student)
        token = token_of(re.search(r'https?://\S+', send.call_args.args[1]).group(0))

        assert (
            j(
                Client(), '/api/auth/recovery/confirm/', {'token': token, 'new_password': NEW}
            ).status_code
            == 200
        )
        assert login('the.student', NEW)[1].status_code == 200

    def test_member_without_telegram_cannot_be_reset_this_way(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student')
        resp, send = self._reset(login('the.manager')[0], student)
        assert resp.status_code == 409 and resp.json()['error']['code'] == 'no_telegram'
        send.assert_not_called()
        assert RecoveryToken.objects.count() == 0

    def test_a_manager_cannot_reset_the_owner(self, org_setup):
        org_setup('the.manager', 'manager', telegram_id=1)
        owner = org_setup('the.owner', 'owner', telegram_id=2)
        resp, send = self._reset(login('the.manager')[0], owner)
        assert resp.status_code == 403
        send.assert_not_called()
        owner.refresh_from_db()
        assert owner.check_password(STRONG) and RecoveryToken.objects.count() == 0

    def test_a_manager_cannot_reset_another_manager(self, org_setup):
        org_setup('manager.one', 'manager')
        other = org_setup('manager.two', 'manager', telegram_id=2)
        resp, send = self._reset(login('manager.one')[0], other)
        assert resp.status_code == 403
        send.assert_not_called()

    def test_a_teacher_cannot_reset_anyone(self, org_setup):
        org_setup('the.teacher', 'teacher')
        student = org_setup('the.student', 'student', telegram_id=3)
        resp, _ = self._reset(login('the.teacher')[0], student)
        assert resp.status_code == 403  # teachers lack members:manage

    def test_owner_can_reset_a_manager(self, org_setup):
        org_setup('the.owner', 'owner')
        manager = org_setup('the.manager', 'manager', telegram_id=4)
        resp, send = self._reset(login('the.owner')[0], manager)
        assert resp.status_code == 200 and send.called

    def test_target_who_is_higher_in_any_shared_org_is_protected(self, org_setup, org, org2):
        """Manager in org A, but the target is owner of org B where the actor is also a member."""
        actor = org_setup('the.manager', 'manager')
        target = org_setup('the.target', 'student', telegram_id=5)
        Membership.objects.create(
            user=actor,
            organization=org2,
            role=OrgRole.objects.get(organization=org2, name='student'),
            status=MembershipStatus.ACTIVE,
        )
        Membership.objects.create(
            user=target,
            organization=org2,
            role=OrgRole.objects.get(organization=org2, name='owner'),
            status=MembershipStatus.ACTIVE,
        )
        resp, send = self._reset(login('the.manager')[0], target)
        assert resp.status_code == 403
        send.assert_not_called()

    def test_a_b2c_account_cannot_be_taken_over_by_an_org_admin(self, org_setup):
        org_setup('the.manager', 'manager')
        outsider = CustomUser(username='outsider', telegram_id=6)
        outsider.set_password(STRONG)
        outsider.save()
        resp, send = self._reset(login('the.manager')[0], outsider)
        assert resp.status_code == 403
        send.assert_not_called()
        outsider.refresh_from_db()
        assert outsider.check_password(STRONG)

    def test_a_manager_of_another_org_cannot_reset(self, org_setup, org2):
        org_setup('foreign.manager', 'manager', organization=org2)
        student = org_setup('the.student', 'student', telegram_id=7)
        resp, send = self._reset(login('foreign.manager')[0], student)
        assert resp.status_code == 403
        send.assert_not_called()

    def test_cannot_reset_platform_staff_or_self(self, org_setup):
        manager = org_setup('the.manager', 'manager', telegram_id=8)
        staff = org_setup('the.staff', 'student', is_staff=True, telegram_id=9)
        c, _ = login('the.manager')
        assert self._reset(c, staff)[0].status_code == 403
        assert self._reset(c, manager)[0].status_code == 403

    def test_a_supplied_password_is_ignored_and_no_password_is_ever_set(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student', telegram_id=10)
        resp, _ = self._reset(
            login('the.manager')[0], student, temporary_password='Attacker-pass-1!'
        )
        assert resp.status_code == 200
        student.refresh_from_db()
        assert not student.check_password('Attacker-pass-1!') and student.check_password(STRONG)

    def test_the_target_is_rate_limited(self, org_setup):
        org_setup('the.manager', 'manager')
        student = org_setup('the.student', 'student', telegram_id=11)
        c, _ = login('the.manager')
        codes = [self._reset(c, student)[0].status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and 429 in codes

    def test_unknown_user_is_indistinguishable_from_forbidden(self, org_setup):
        org_setup('the.manager', 'manager')
        c, _ = login('the.manager')
        import uuid

        assert j(c, self.URL, {'user_id': str(uuid.uuid4())}).status_code == 403
        assert j(c, self.URL, {'user_id': 'nope'}).status_code == 400

    def test_anonymous_is_rejected(self, db):
        assert j(Client(), self.URL, {'user_id': 'x'}).status_code == 401

    def test_generated_passwords_pass_the_policy(self, db):
        for _ in range(20):
            from apps.accounts.services.password_policy import password_errors

            assert password_errors(recovery.generate_temporary_password()) == []


@pytest.mark.integration
class TestForcedChange:
    """Temporary passwords now come only from platform staff (Django admin action)."""

    @pytest.fixture
    def flagged(self, db):
        user = CustomUser.objects.create_user(username='flagged.user', password=STRONG)
        temp = recovery.set_temporary_password(user)
        return user, temp

    def test_login_with_temporary_password_gives_a_ticket_and_no_session(self, flagged):
        c, resp = login('flagged.user', flagged[1])
        body = resp.json()
        assert body['password_change_required'] is True and body['change_token']
        assert 'access_token' not in resp.cookies
        assert c.get('/api/auth/me/').status_code == 401

    def test_ticket_completes_the_change_and_signs_in(self, flagged):
        user, temp = flagged
        ticket = login('flagged.user', temp)[1].json()['change_token']
        c = Client()
        resp = j(
            c, '/api/auth/password/change-required/', {'change_token': ticket, 'new_password': NEW}
        )
        assert resp.status_code == 200 and 'access_token' in resp.cookies
        user.refresh_from_db()
        assert not user.must_change_password and user.check_password(NEW)
        assert login('flagged.user', temp)[1].status_code == 400

    def test_ticket_is_single_use(self, flagged):
        ticket = login('flagged.user', flagged[1])[1].json()['change_token']
        assert (
            j(
                Client(),
                '/api/auth/password/change-required/',
                {'change_token': ticket, 'new_password': NEW},
            ).status_code
            == 200
        )
        again = j(
            Client(),
            '/api/auth/password/change-required/',
            {'change_token': ticket, 'new_password': 'An0ther-pass!9'},
        )
        assert again.status_code == 400

    def test_garbage_ticket_is_refused(self, flagged):
        resp = j(
            Client(),
            '/api/auth/password/change-required/',
            {'change_token': 'x' * 43, 'new_password': NEW},
        )
        assert resp.status_code == 400 and resp.json()['error']['code'] == 'invalid_ticket'
        flagged[0].refresh_from_db()
        assert flagged[0].must_change_password

    def test_weak_or_unchanged_passwords_keep_the_ticket_usable(self, flagged):
        user, temp = flagged
        ticket = login('flagged.user', temp)[1].json()['change_token']
        for bad in ('password1', temp, 'flagged.user1'):
            resp = j(
                Client(),
                '/api/auth/password/change-required/',
                {'change_token': ticket, 'new_password': bad},
            )
            assert resp.status_code == 400, bad
        assert (
            j(
                Client(),
                '/api/auth/password/change-required/',
                {'change_token': ticket, 'new_password': NEW},
            ).status_code
            == 200
        )

    def test_legacy_credentials_path_still_needs_the_temporary_password(self, flagged):
        resp = j(
            Client(),
            '/api/auth/password/change-required/',
            {'username': 'flagged.user', 'password': 'guess-it-1', 'new_password': NEW},
        )
        assert resp.status_code == 400
        flagged[0].refresh_from_db()
        assert flagged[0].must_change_password

    def test_409_for_normal_accounts(self, db):
        CustomUser.objects.create_user(username='normal.user', password=STRONG)
        resp = j(
            Client(),
            '/api/auth/password/change-required/',
            {'username': 'normal.user', 'password': STRONG, 'new_password': NEW},
        )
        assert resp.status_code == 409

    def test_after_2fa_login_no_second_totp_code_is_needed(self, flagged):
        """16: the TOTP step is spent at login; the ticket carries the proof."""
        from apps.accounts.models import TOTPDevice
        from apps.accounts.services import two_factor as tf

        user, temp = flagged
        secret = tf.generate_secret()
        TOTPDevice.objects.create(
            user=user, secret_enc=tf.encrypt_secret(secret), confirmed_at=timezone.now()
        )
        step = tf.current_step()
        code = tf.totp_code(secret, step + 1)

        resp = login('flagged.user', temp, code=code)[1]
        assert resp.json()['password_change_required'] is True
        done = j(
            Client(),
            '/api/auth/password/change-required/',
            {'change_token': resp.json()['change_token'], 'new_password': NEW},
        )
        assert done.status_code == 200

    def test_ticket_cannot_be_obtained_without_the_second_factor(self, flagged):
        from apps.accounts.models import TOTPDevice
        from apps.accounts.services import two_factor as tf

        user, temp = flagged
        TOTPDevice.objects.create(
            user=user,
            secret_enc=tf.encrypt_secret(tf.generate_secret()),
            confirmed_at=timezone.now(),
        )
        resp = login('flagged.user', temp)[1]
        assert resp.json() == {'need_code': True}
        assert 'change_token' not in resp.content.decode()

    def test_the_reset_kills_the_targets_sessions(self, db):
        user = CustomUser.objects.create_user(username='victim.user', password=STRONG)
        client, _ = login('victim.user')
        recovery.set_temporary_password(user)
        assert client.post('/api/auth/refresh/').status_code == 401

    def test_supplied_temporary_password_must_meet_the_policy(self, db):
        user = CustomUser.objects.create_user(username='victim.user', password=STRONG)
        with pytest.raises(recovery.RecoveryError):
            recovery.set_temporary_password(user, 'password1')
        user.refresh_from_db()
        assert user.check_password(STRONG)


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
