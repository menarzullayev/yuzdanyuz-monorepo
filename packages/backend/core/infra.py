"""Connection settings for Postgres, Redis, Channels and Celery, built from the environment.

Pure functions (env in, dict out) so settings stay declarative and the rules are
unit-tested: timeouts on every network hop (F06), TLS when the URL or env asks for
it (C19), and options that survive PgBouncer transaction pooling (E05).
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Mapping
from urllib.parse import urlparse

import redis

SSL_MODES = {'disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'}
_CERT_REQS = {'required': ssl.CERT_REQUIRED, 'optional': ssl.CERT_OPTIONAL, 'none': ssl.CERT_NONE}


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


# ── Postgres ──────────────────────────────────────────────────────────────────


def database_options(env: Mapping[str, str] | None = None) -> dict:
    """psycopg2 connection OPTIONS.

    DB_POOLER=pgbouncer: PgBouncer refuses the `options` startup parameter, so the
    statement timeout is applied by the pooler (connect_query) instead of here.
    """
    env = _env(env)
    sslmode = env.get('DB_SSLMODE', 'prefer')
    if sslmode not in SSL_MODES:
        raise ValueError(f'DB_SSLMODE={sslmode!r} is not one of {sorted(SSL_MODES)}')
    opts: dict = {
        'connect_timeout': int(env.get('DB_CONNECT_TIMEOUT', '5')),
        'sslmode': sslmode,
        # dead peers (failover, dropped NAT) are noticed in ~1 min instead of hours
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 3,
    }
    if env.get('DB_SSLROOTCERT'):
        opts['sslrootcert'] = env['DB_SSLROOTCERT']
    statement_timeout = int(env.get('DB_STATEMENT_TIMEOUT_MS', '30000'))
    if statement_timeout > 0 and env.get('DB_POOLER', '') != 'pgbouncer':
        opts['options'] = f'-c statement_timeout={statement_timeout}'
    return opts


# ── Redis ─────────────────────────────────────────────────────────────────────


def _redis_tls_kwargs(url: str, env: Mapping[str, str]) -> dict:
    if urlparse(url).scheme != 'rediss':
        return {}
    kw = {'ssl_cert_reqs': env.get('REDIS_SSL_CERT_REQS', 'required')}
    if env.get('REDIS_SSL_CA_CERTS'):
        kw['ssl_ca_certs'] = env['REDIS_SSL_CA_CERTS']
    return kw


def redis_connection_kwargs(url: str, env: Mapping[str, str] | None = None) -> dict:
    """redis-py kwargs: bounded connect/read time on the request path, TLS for rediss://."""
    env = _env(env)
    return {
        'socket_connect_timeout': float(env.get('REDIS_SOCKET_CONNECT_TIMEOUT', '2')),
        'socket_timeout': float(env.get('REDIS_SOCKET_TIMEOUT', '2')),
        'health_check_interval': 30,
        'retry_on_timeout': True,
        **_redis_tls_kwargs(url, env),
    }


def redis_from_url(url: str, **kwargs) -> redis.Redis:
    """`redis.Redis.from_url` with the timeouts and TLS rules above."""
    return redis.Redis.from_url(url, **{**redis_connection_kwargs(url), **kwargs})


def channel_layer_hosts(url: str, env: Mapping[str, str] | None = None) -> list[dict]:
    """channels_redis `hosts` entry; extra keys go to redis.asyncio's from_url."""
    env = _env(env)
    # No socket_timeout here: the layer blocks on BZPOPMIN for its receive loop.
    return [{'address': url, 'socket_connect_timeout': 5, **_redis_tls_kwargs(url, env)}]


def celery_broker_ssl(url: str, env: Mapping[str, str] | None = None) -> dict | None:
    """`broker_use_ssl` for a rediss:// broker (kombu wants ssl module constants)."""
    env = _env(env)
    tls = _redis_tls_kwargs(url, env)
    if not tls:
        return None
    out = {'ssl_cert_reqs': _CERT_REQS[tls['ssl_cert_reqs']]}
    if 'ssl_ca_certs' in tls:
        out['ssl_ca_certs'] = tls['ssl_ca_certs']
    return out
