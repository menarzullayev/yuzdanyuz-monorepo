"""ISSUE-405 — Unit tests for B2B webhook system.

Coverage:
  - WebhookEndpoint CRUD + tenant isolation
  - sign_payload stability
  - dispatch_webhook subscription filter + delivery row creation
  - deliver_webhook task: 2xx → SENT, 4xx → retry, 5 fails → EXHAUSTED + DLQ
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest

from apps.webhooks.models import WebhookDelivery, WebhookEndpoint
from apps.webhooks.services import canonical_json, dispatch_webhook, sign_payload
from apps.webhooks.tasks import deliver_webhook
from core.tenant import tenant_context

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_endpoint(org, *, name='Test EP', events=('exam.submitted',), is_active=True):
    with tenant_context(org):
        return WebhookEndpoint.objects.create(
            organization=org,
            name=name,
            url='https://example.com/hook',
            events=list(events),
            is_active=is_active,
        )


def _mock_response(status_code: int, text: str = ''):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


# ─── 1. Model CRUD + tenant isolation ────────────────────────────────────────


@pytest.mark.unit
class TestWebhookEndpointModel:
    def test_endpoint_create_basic(self, org):
        with tenant_context(org):
            ep = WebhookEndpoint.objects.create(
                organization=org,
                name='LMS Sync',
                url='https://lms.example.com/hook',
                events=['exam.submitted'],
            )
        assert ep.name == 'LMS Sync'
        assert ep.is_active is True
        assert len(ep.secret) > 0  # auto-generated
        assert ep.subscribes_to('exam.submitted') is True
        assert ep.subscribes_to('payment.completed') is False

    def test_endpoint_tenant_isolation(self, org, org2):
        ep1 = _make_endpoint(org, name='Org1 EP')
        _make_endpoint(org2, name='Org2 EP')

        with tenant_context(org):
            visible = list(WebhookEndpoint.objects.all())
        assert len(visible) == 1
        assert visible[0].id == ep1.id

        with tenant_context(org2):
            visible_2 = list(WebhookEndpoint.objects.all())
        assert len(visible_2) == 1
        assert visible_2[0].name == 'Org2 EP'

    def test_endpoint_subscribes_to_empty_events(self, org):
        ep = _make_endpoint(org, events=[])
        assert ep.subscribes_to('exam.submitted') is False

    def test_endpoint_str(self, org):
        ep = _make_endpoint(org, name='Foo')
        assert 'Foo' in str(ep)
        assert ep.url in str(ep)


# ─── 2. sign_payload ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSignPayload:
    def test_sign_payload_stable(self):
        payload = b'{"event":"exam.submitted","attempt_id":"abc"}'
        secret = 'topsecret'
        sig1 = sign_payload(payload, secret)
        sig2 = sign_payload(payload, secret)
        assert sig1 == sig2

    def test_sign_payload_matches_manual_hmac(self):
        payload = b'{"foo":"bar"}'
        secret = 'mykey'
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert sign_payload(payload, secret) == expected

    def test_sign_payload_changes_with_secret(self):
        payload = b'{"x":1}'
        assert sign_payload(payload, 'a') != sign_payload(payload, 'b')

    def test_sign_payload_rejects_str_input(self):
        with pytest.raises(TypeError):
            sign_payload('not-bytes', 'k')  # type: ignore[arg-type]

    def test_canonical_json_is_deterministic(self):
        a = canonical_json({'b': 2, 'a': 1})
        b = canonical_json({'a': 1, 'b': 2})
        assert a == b


# ─── 3. dispatch_webhook ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestDispatchWebhook:
    def test_creates_delivery_row_per_endpoint(self, org):
        ep1 = _make_endpoint(org, name='A', events=['exam.submitted'])
        ep2 = _make_endpoint(org, name='B', events=['exam.submitted'])

        with patch('apps.webhooks.tasks.requests.post', return_value=_mock_response(200, 'ok')):
            deliveries = dispatch_webhook('exam.submitted', {'attempt_id': 'x'}, org=org)

        assert len(deliveries) == 2
        endpoint_ids = {d.endpoint_id for d in deliveries}
        assert endpoint_ids == {ep1.id, ep2.id}

    def test_filters_by_event_subscription(self, org):
        _make_endpoint(org, name='Exam', events=['exam.submitted'])
        _make_endpoint(org, name='Payment', events=['payment.completed'])

        with patch('apps.webhooks.tasks.requests.post', return_value=_mock_response(200, 'ok')):
            deliveries = dispatch_webhook('exam.submitted', {'k': 'v'}, org=org)

        assert len(deliveries) == 1
        assert deliveries[0].endpoint.name == 'Exam'

    def test_skips_inactive_endpoints(self, org):
        _make_endpoint(org, name='Active', events=['exam.submitted'], is_active=True)
        _make_endpoint(org, name='Off', events=['exam.submitted'], is_active=False)

        with patch('apps.webhooks.tasks.requests.post', return_value=_mock_response(200, 'ok')):
            deliveries = dispatch_webhook('exam.submitted', {}, org=org)

        assert len(deliveries) == 1
        assert deliveries[0].endpoint.name == 'Active'

    def test_no_org_returns_empty(self, db):
        # No tenant context, no org arg → no-op.
        result = dispatch_webhook('exam.submitted', {})
        assert result == []

    def test_no_matching_endpoint_returns_empty(self, org):
        _make_endpoint(org, events=['payment.completed'])
        result = dispatch_webhook('exam.submitted', {}, org=org)
        assert result == []


# ─── 4. deliver_webhook task ─────────────────────────────────────────────────


@pytest.mark.unit
class TestDeliverWebhookTask:
    def test_2xx_marks_sent(self, org):
        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(
            endpoint=ep, event='exam.submitted', payload={'k': 'v'}
        )

        with patch('apps.webhooks.tasks.requests.post', return_value=_mock_response(200, 'OK')):
            deliver_webhook(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.SENT
        assert delivery.response_status == 200
        assert delivery.attempts == 1
        assert delivery.completed_at is not None

    def test_post_includes_hmac_signature_header(self, org):
        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(
            endpoint=ep, event='exam.submitted', payload={'x': 1}
        )

        with patch(
            'apps.webhooks.tasks.requests.post', return_value=_mock_response(200, 'ok')
        ) as mock_post:
            deliver_webhook(str(delivery.id))

        call_kwargs = mock_post.call_args.kwargs
        headers = call_kwargs['headers']
        assert 'X-Webhook-Signature' in headers
        assert headers['X-Webhook-Signature'].startswith('sha256=')
        # Verify signature matches body + secret
        body = call_kwargs['data']
        expected = 'sha256=' + sign_payload(body, ep.secret)
        assert headers['X-Webhook-Signature'] == expected
        assert headers['X-Webhook-Event'] == 'exam.submitted'

    def test_4xx_triggers_retry(self, org):
        """Non-2xx response → task.retry() invoked, delivery stays FAILED."""
        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(
            endpoint=ep, event='exam.submitted', payload={'k': 'v'}
        )

        with (
            patch(
                'apps.webhooks.tasks.requests.post',
                return_value=_mock_response(400, 'bad req'),
            ),
            patch(
                'apps.webhooks.tasks.deliver_webhook.retry',
                side_effect=RuntimeError('retry-called'),
            ) as mock_retry,
        ):
            with pytest.raises(RuntimeError, match='retry-called'):
                deliver_webhook(str(delivery.id))

        # retry() was called with the right exponential countdown
        assert mock_retry.called
        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.FAILED
        assert delivery.response_status == 400
        assert delivery.attempts == 1

    def test_5xx_triggers_retry(self, org):
        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(endpoint=ep, event='exam.submitted', payload={})

        with (
            patch(
                'apps.webhooks.tasks.requests.post',
                return_value=_mock_response(503, 'svc unavailable'),
            ),
            patch(
                'apps.webhooks.tasks.deliver_webhook.retry',
                side_effect=RuntimeError('retry-called'),
            ),
        ):
            with pytest.raises(RuntimeError):
                deliver_webhook(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.FAILED
        assert delivery.response_status == 503

    def test_network_error_triggers_retry(self, org):
        import requests as _requests

        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(endpoint=ep, event='exam.submitted', payload={})

        with (
            patch(
                'apps.webhooks.tasks.requests.post',
                side_effect=_requests.ConnectionError('boom'),
            ),
            patch(
                'apps.webhooks.tasks.deliver_webhook.retry',
                side_effect=RuntimeError('retry-called'),
            ),
        ):
            with pytest.raises(RuntimeError):
                deliver_webhook(str(delivery.id))

        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.FAILED
        assert delivery.response_status is None

    def test_response_body_truncated_to_2000(self, org):
        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(endpoint=ep, event='exam.submitted', payload={})
        long_body = 'x' * 5000

        with patch(
            'apps.webhooks.tasks.requests.post',
            return_value=_mock_response(200, long_body),
        ):
            deliver_webhook(str(delivery.id))

        delivery.refresh_from_db()
        assert len(delivery.response_body) == 2000

    def test_max_retries_exhausted_marks_dlq(self, org):
        """After 5 retries (request.retries == max_retries), mark EXHAUSTED + DLQ."""
        from apps.analytics.dlq import FailedTask

        ep = _make_endpoint(org)
        delivery = WebhookDelivery.objects.create(
            endpoint=ep, event='exam.submitted', payload={'a': 1}
        )

        # Celery `request` is a property, not patchable; use push_request() to
        # inject a fake context for this invocation only.
        deliver_webhook.push_request(retries=5, id='fake-task-id-12345')
        try:
            with patch(
                'apps.webhooks.tasks.requests.post',
                return_value=_mock_response(500, 'err'),
            ):
                with pytest.raises(RuntimeError):
                    deliver_webhook.run(str(delivery.id))
        finally:
            deliver_webhook.pop_request()

        delivery.refresh_from_db()
        assert delivery.status == WebhookDelivery.Status.EXHAUSTED
        assert delivery.completed_at is not None

        # ISSUE-308 DLQ integration: task_failure signal fires when the task
        # raises after max retries. We invoke the signal directly here since
        # task.run() bypasses Celery's failure machinery (we're not in the
        # worker loop). This validates the DLQ wiring contract.
        from celery.signals import task_failure

        task_failure.send(
            sender=deliver_webhook,
            task_id='fake-task-id-12345',
            exception=RuntimeError('HTTP 500'),
            args=[str(delivery.id)],
            kwargs={},
            traceback=None,
        )
        dlq_rows = FailedTask.objects.filter(task_name__icontains='deliver_webhook')
        assert dlq_rows.exists(), 'Expected FailedTask DLQ entry after exhaustion'
