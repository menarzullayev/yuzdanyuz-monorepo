"""AI on the Claude subscription: defer on limits, fail fast on auth, warn before the token expires."""

from datetime import timedelta

import pytest
import sentry_sdk
from django.utils import timezone

from apps.intelligence import checks, tasks
from apps.intelligence.models import AIFeedback
from core import claude_cli
from core.celery import app as celery_app

LIMIT = claude_cli.ClaudeRateLimitError('m: Claude AI usage limit reached|1794500000')


def _feedback(user):
    return AIFeedback.objects.create(
        user=user, summary={'weak': [], 'strong': [], 'stats': {}}, prompt_hash='x' * 64
    )


def _calls(monkeypatch, *outcomes):
    """Patch the LLM call to raise or return `outcomes` in order; return the call log."""
    log = []

    def fake(prompt, **kw):
        log.append(prompt)
        outcome = outcomes[min(len(log), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(tasks, '_generate_text', fake)
    return log


@pytest.mark.integration
class TestTutorRetryPolicy:
    # apply() runs eagerly and re-applies on self.retry(), as a worker would (minus the
    # countdown) — but only when eager exceptions are not propagated; test settings
    # propagate them, which turns a retry into a raised Retry.
    @pytest.fixture(autouse=True)
    def _eager_retries(self):
        before = celery_app.conf.task_eager_propagates
        celery_app.conf.task_eager_propagates = False
        try:
            yield
        finally:
            celery_app.conf.task_eager_propagates = before

    def test_rate_limit_then_success_completes(self, db, user, monkeypatch):
        log = _calls(monkeypatch, LIMIT, 'Davom et!')
        fb = _feedback(user)

        tasks.generate_ai_tutor_feedback.apply(args=[str(fb.id)])

        fb.refresh_from_db()
        assert len(log) == 2
        assert (fb.status, fb.content) == (AIFeedback.Status.READY, 'Davom et!')

    def test_rate_limit_that_never_clears_fails_after_the_retries(self, db, user, monkeypatch):
        log = _calls(monkeypatch, LIMIT)
        fb = _feedback(user)

        tasks.generate_ai_tutor_feedback.apply(args=[str(fb.id)])

        fb.refresh_from_db()
        assert len(log) == tasks.AI_RETRY_MAX + 1
        assert fb.status == AIFeedback.Status.FAILED
        assert 'usage limit' in fb.error

    def test_auth_error_fails_at_once(self, db, user, monkeypatch):
        log = _calls(
            monkeypatch, claude_cli.ClaudeAuthError('m: Not logged in · Please run /login')
        )
        fb = _feedback(user)

        tasks.generate_ai_tutor_feedback.apply(args=[str(fb.id)])

        fb.refresh_from_db()
        assert len(log) == 1
        assert fb.status == AIFeedback.Status.FAILED
        assert 'Not logged in' in fb.error

    def test_auth_failure_reaches_sentry_tagged(self, db, user, monkeypatch):
        _calls(monkeypatch, claude_cli.ClaudeAuthError('m: Not logged in'))
        # Test LOGGING sets disable_existing_loggers=True, so a re-applied config can
        # silence this logger; prod logging keeps it on, which is the path under test.
        monkeypatch.setattr(tasks.logger, 'disabled', False)
        events = []
        # before_send drops every event and client reports are off, so nothing is sent.
        sentry_sdk.init(
            dsn='https://public@sentry.invalid/1',
            before_send=lambda event, hint: events.append(event) or None,
            send_client_reports=False,
        )
        try:
            tasks.generate_ai_tutor_feedback.apply(args=[str(_feedback(user).id)])
        finally:
            sentry_sdk.get_client().close()
            sentry_sdk.init()

        tagged = [e for e in events if (e.get('tags') or {}).get('ai.error_kind') == 'auth']
        assert tagged, events


@pytest.mark.unit
class TestRetryCountdown:
    def test_waits_until_the_limit_resets(self):
        exc = claude_cli.ClaudeRateLimitError('m', resets_at=timezone.now() + timedelta(hours=2))
        assert 7200 <= tasks.ai_retry_countdown(exc) <= 7260

    def test_reset_in_the_past_waits_a_minute(self):
        exc = claude_cli.ClaudeRateLimitError('m', resets_at=timezone.now() - timedelta(hours=1))
        assert tasks.ai_retry_countdown(exc) == 60

    def test_unknown_reset_and_long_waits_are_bounded(self):
        far = claude_cli.ClaudeRateLimitError('m', resets_at=timezone.now() + timedelta(days=2))
        assert tasks.ai_retry_countdown(claude_cli.ClaudeRateLimitError('m')) == 15 * 60
        assert tasks.ai_retry_countdown(far) == 6 * 60 * 60

    def test_timeout_retries_but_auth_and_other_errors_do_not(self):
        assert tasks.ai_retry_countdown(claude_cli.ClaudeTimeoutError('m')) == 120
        assert tasks.ai_retry_countdown(claude_cli.ClaudeAuthError('m')) is None
        assert tasks.ai_retry_countdown(claude_cli.ClaudeCLIError('m')) is None
        assert tasks.ai_retry_countdown(RuntimeError('x')) is None


def _expires_in(days: int) -> str:
    return (timezone.localdate() + timedelta(days=days)).isoformat()


@pytest.mark.unit
class TestTokenExpiryTask:
    @pytest.fixture(autouse=True)
    def _claude_cli(self, settings, monkeypatch):
        settings.LLM_BACKEND = 'claude_cli'
        self.logged = []
        for level in ('warning', 'error'):
            monkeypatch.setattr(
                tasks.logger, level, lambda msg, *a, _l=level: self.logged.append((_l, msg % a))
            )

    @pytest.mark.parametrize(
        ('days', 'status', 'level'),
        [
            (100, 'ok', None),
            (20, 'soon', 'warning'),
            (5, 'urgent', 'error'),
            (-3, 'expired', 'error'),
        ],
    )
    def test_status_and_log_level(self, settings, days, status, level):
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = _expires_in(days)

        assert tasks.check_ai_token_expiry() == {'status': status, 'days_left': days}
        assert [lvl for lvl, _ in self.logged] == ([level] if level else [])

    def test_unset_and_invalid(self, settings):
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = ''
        assert tasks.check_ai_token_expiry() == {'status': 'unknown'}
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = 'next year'
        assert tasks.check_ai_token_expiry() == {'status': 'invalid'}

    def test_api_backend_is_not_checked(self, settings):
        settings.LLM_BACKEND = 'anthropic_api'
        assert tasks.check_ai_token_expiry() == {'status': 'not_used'}

    def test_runs_daily_on_the_batch_queue(self):
        schedule = celery_app.conf.beat_schedule['ai-token-expiry-daily']
        assert schedule['task'] == tasks.check_ai_token_expiry.name
        assert celery_app.conf.task_routes[tasks.check_ai_token_expiry.name] == {'queue': 'batch'}


@pytest.mark.unit
class TestTokenExpiryCheck:
    @pytest.fixture(autouse=True)
    def _claude_cli(self, settings, monkeypatch):
        settings.LLM_BACKEND = 'claude_cli'
        monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN', raising=False)

    def ids(self):
        return [m.id for m in checks.ai_token_expiry_check(None)]

    # Day offsets, not dates: dates computed at collection time could straddle midnight.
    @pytest.mark.parametrize(
        ('expires', 'expected'),
        [
            (100, []),
            (10, ['intelligence.W004']),
            (-1, ['intelligence.W001']),
            ('2027/11/11', ['intelligence.W003']),
        ],
    )
    def test_expiry_dates(self, settings, expires, expected):
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = (
            _expires_in(expires) if isinstance(expires, int) else expires
        )
        assert self.ids() == expected

    def test_token_without_expiry_date_warns(self, settings, monkeypatch):
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = ''
        assert self.ids() == []
        monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'token-for-the-test')
        assert self.ids() == ['intelligence.W002']

    def test_api_backend_is_not_checked(self, settings):
        settings.LLM_BACKEND = 'anthropic_api'
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = _expires_in(-1)
        assert self.ids() == []

    def test_expired_token_is_only_a_warning(self, settings):
        # An expired AI token must not stop migrate/runserver (that needs an Error).
        settings.CLAUDE_CODE_OAUTH_TOKEN_EXPIRES = _expires_in(-30)
        assert all(not m.is_serious() for m in checks.ai_token_expiry_check(None))
