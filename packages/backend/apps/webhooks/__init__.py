"""ISSUE-405 — B2B Webhook system.

Outgoing webhook endpoints registered by enterprise tenants:
  - HMAC-SHA256 signed payloads
  - Exponential retry (1m → 5m → 30m → 2h → 12h)
  - DLQ integration (ISSUE-308 FailedTask) after max_retries
"""

default_app_config = 'apps.webhooks.apps.WebhooksConfig'
