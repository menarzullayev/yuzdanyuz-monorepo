"""P1 reliability: timeouts, TLS, pooling, Celery delivery, cache and static settings.

Covers audit items F06 (timeouts), C19 (TLS in transit), E05 (pooler-safe DB
settings), F10 (acks_late), F13 (shared cache) and F05 (hashed static files).
"""

import re
import ssl
from pathlib import Path

import pytest

from core.settings import base

REPO_ROOT = Path(base.BASE_DIR).parent.parent
NGINX_CONF = REPO_ROOT / 'docker' / 'nginx' / 'default.conf'


# ── F06 + C19 + E05: database ─────────────────────────────────────────────────


class TestDatabaseSettings:
    def test_default_db_has_connect_and_statement_timeout(self):
        opts = base.DATABASES['default']['OPTIONS']
        assert int(opts['connect_timeout']) > 0
        assert 'statement_timeout=' in opts.get('options', '')

    def test_sslmode_comes_from_env(self):
        from core.infra import database_options

        assert database_options({'DB_SSLMODE': 'verify-full'})['sslmode'] == 'verify-full'
        assert database_options({'DB_SSLMODE': 'require'})['sslmode'] == 'require'
        opts = database_options({'DB_SSLMODE': 'verify-full', 'DB_SSLROOTCERT': '/ca.pem'})
        assert opts['sslrootcert'] == '/ca.pem'

    def test_invalid_sslmode_is_rejected(self):
        from core.infra import database_options

        with pytest.raises(ValueError):
            database_options({'DB_SSLMODE': 'yes-please'})

    def test_pgbouncer_mode_drops_startup_options(self):
        """PgBouncer rejects the `options` startup parameter; the timeout moves to the pooler."""
        from core.infra import database_options

        opts = database_options({'DB_POOLER': 'pgbouncer', 'DB_STATEMENT_TIMEOUT_MS': '30000'})
        assert 'options' not in opts
        assert int(opts['connect_timeout']) > 0

    def test_statement_timeout_zero_disables_it(self):
        from core.infra import database_options

        assert 'options' not in database_options({'DB_STATEMENT_TIMEOUT_MS': '0'})

    def test_server_side_cursors_disabled_for_transaction_pooling(self):
        assert base.DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'] is True


# ── F06 + C19: Redis ──────────────────────────────────────────────────────────


class TestRedisSettings:
    def test_plain_redis_gets_socket_timeouts(self):
        from core.infra import redis_connection_kwargs

        kw = redis_connection_kwargs('redis://r:6379/1')
        assert kw['socket_connect_timeout'] > 0
        assert kw['socket_timeout'] > 0
        assert 'ssl_cert_reqs' not in kw

    def test_rediss_verifies_certificates_by_default(self):
        from core.infra import redis_connection_kwargs

        kw = redis_connection_kwargs('rediss://r:6380/1')
        assert kw['ssl_cert_reqs'] == 'required'

    def test_rediss_ca_bundle_from_env(self):
        from core.infra import redis_connection_kwargs

        kw = redis_connection_kwargs('rediss://r:6380/1', env={'REDIS_SSL_CA_CERTS': '/ca.pem'})
        assert kw['ssl_ca_certs'] == '/ca.pem'

    def test_channel_layer_hosts_support_rediss(self):
        from core.infra import channel_layer_hosts

        (plain,) = channel_layer_hosts('redis://r:6379/2')
        assert plain == {'address': 'redis://r:6379/2', 'socket_connect_timeout': 5}
        (tls,) = channel_layer_hosts('rediss://r:6380/2')
        assert tls['address'].startswith('rediss://')
        assert tls['ssl_cert_reqs'] == 'required'

    def test_celery_broker_ssl_only_for_rediss(self):
        from core.infra import celery_broker_ssl

        assert celery_broker_ssl('redis://r:6379/0') is None
        assert celery_broker_ssl('rediss://r:6380/0')['ssl_cert_reqs'] == ssl.CERT_REQUIRED

    @pytest.mark.parametrize(
        'module_path',
        [
            'core.utils.rate_limiter',
            'core.membership_cache',
            'core.locks',
            'apps.accounts.services.token_service',
            'apps.accounts.services.otp_service',
            'apps.accounts.services.telegram_deeplink',
            'apps.catalog.services.watermark',
            'apps.engagement.leaderboard',
        ],
    )
    def test_every_request_path_redis_client_has_timeouts(self, module_path, monkeypatch):
        """A hung Redis must not stall a request until gunicorn kills the worker."""
        import importlib

        import redis

        captured = {}

        def fake_from_url(url, **kwargs):
            captured.update(kwargs)
            return object()

        monkeypatch.setattr(redis.Redis, 'from_url', staticmethod(fake_from_url))
        monkeypatch.setattr(redis, 'from_url', fake_from_url)
        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, '_redis_client', None, raising=False)
        # conftest replaces some _redis factories with lambdas; reload restores the real one
        module = importlib.reload(module)
        module._redis()
        assert captured.get('socket_timeout'), module_path
        assert captured.get('socket_connect_timeout'), module_path


# ── F06 + F10: Celery ─────────────────────────────────────────────────────────


class TestCeleryReliability:
    def test_tasks_are_acked_late_and_requeued_on_worker_loss(self):
        from core.celery import app

        assert app.conf.task_acks_late is True
        assert app.conf.task_reject_on_worker_lost is True
        # acks_late + a big prefetch would park many unacked tasks on one dying worker
        assert app.conf.worker_prefetch_multiplier == 1

    def test_broker_has_socket_timeouts(self):
        from core.celery import app

        opts = app.conf.broker_transport_options
        assert opts['socket_timeout'] > 0
        assert opts['socket_connect_timeout'] > 0
        # Redis redelivers unacked tasks after this; it must exceed the longest task
        assert opts['visibility_timeout'] > base.CELERY_TASK_TIME_LIMIT
        assert app.conf.broker_connection_timeout > 0

    def test_publish_does_not_hang_the_request(self):
        from core.celery import app

        policy = app.conf.task_publish_retry_policy
        assert policy['max_retries'] <= 3
        assert policy['interval_max'] <= 1

    def test_finalize_attempt_score_retries_on_db_errors(self):
        from django.db import OperationalError

        from apps.exams.tasks import finalize_attempt_score

        assert OperationalError in finalize_attempt_score.autoretry_for


# ── F13: shared cache ─────────────────────────────────────────────────────────


class TestSharedCache:
    def test_default_cache_is_redis_not_per_process(self):
        cache = base.CACHES['default']
        assert cache['BACKEND'] == 'django.core.cache.backends.redis.RedisCache'
        assert cache['OPTIONS']['socket_timeout'] > 0


# ── F05: hashed static files ──────────────────────────────────────────────────


class TestStaticFiles:
    def test_static_storage_hashes_filenames(self):
        backend = base.STORAGES['staticfiles']['BACKEND']
        assert 'Manifest' in backend, backend

    def test_nginx_sends_immutable_only_for_hashed_files(self):
        conf = NGINX_CONF.read_text()
        static_block = re.search(r'location /static/ \{(.*?)\n    \}', conf, re.S).group(1)
        # the /static/ prefix must not promise immutability for every name
        assert 'immutable' not in static_block
        assert 'expires' not in static_block
        assert 'add_header Cache-Control $static_cache_control' in static_block
        cache_map = re.search(r'map \$uri \$static_cache_control \{(.*?)\n\}', conf, re.S).group(1)
        rules = re.findall(r'"~(.+?)"\s+"(.+?)";', cache_map)
        assert rules, cache_map
        hashed = '/static/admin/css/base.5af66c1b1797.css'
        plain = '/static/admin/css/base.css'
        assert any(re.search(p, hashed) and 'immutable' in v for p, v in rules)
        assert not any(re.search(p, plain) and 'immutable' in v for p, v in rules)
        assert re.search(r'default\s+"public, max-age=300";', cache_map)
