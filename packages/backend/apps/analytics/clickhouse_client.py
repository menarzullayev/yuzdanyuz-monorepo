"""ISSUE-501 — ClickHouse client (real impl + stub mode).

Stub mode (default, dev/test):
  - PostgreSQL'da ExamEvent jadvali analytics queries uchun yetadi
  - write_event() / query_aggregation() no-op

Real mode (CLICKHOUSE_ENABLED=true + DSN):
  - clickhouse-driver bilan parallel write (durability — PG + CH ikkalasi)
  - query_aggregation() to'g'ridan-to'g'ri CH'ga so'rov yuboradi

Migration path (production rollout):
  1. CH cluster deploy (managed: Altinity, Aiven, yoki self-host)
  2. CLICKHOUSE_ENABLED=true bilan dual-write yoqilsa
  3. Eski PG ExamEvent qatorlarini bir martalik migration task bilan ko'chirish
  4. Read traffic CH'ga ko'chirish (services.py'da is_real_mode tekshiruvi)
  5. PG ExamEvent 90 kun retention (compliance arxiv), CH 7 yil

Settings:
  CLICKHOUSE_ENABLED   — bool, default False (stub)
  CLICKHOUSE_DSN       — clickhouse://user:pass@host:9000/db
  CLICKHOUSE_TABLE     — default 'exam_events'
  CLICKHOUSE_TIMEOUT   — seconds, default 5

clickhouse-driver dependency optional: only needed when real mode.
"""

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_client_cache: Any = None


def is_real_mode() -> bool:
    return bool(getattr(settings, 'CLICKHOUSE_ENABLED', False)) and bool(
        getattr(settings, 'CLICKHOUSE_DSN', '')
    )


def _get_client():
    """Lazy-initialize ClickHouse client (singleton per worker)."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    try:
        from clickhouse_driver import Client
    except ImportError as e:
        raise RuntimeError(
            "ClickHouse real mode yoqilgan, ammo `clickhouse-driver` paketi yo'q. "
            "pip install clickhouse-driver bilan o'rnating."
        ) from e

    dsn = settings.CLICKHOUSE_DSN
    timeout = getattr(settings, 'CLICKHOUSE_TIMEOUT', 5)
    _client_cache = Client.from_url(dsn, connect_timeout=timeout, send_receive_timeout=timeout)
    return _client_cache


def _reset_client():
    """Tests/connection-refresh uchun."""
    global _client_cache
    _client_cache = None


def write_event(event_data: dict) -> None:
    """ExamEvent submitted bo'lganda chaqiriladi (services.record_exam_event).

    Stub: no-op (PostgreSQL'da analytics_examevent allaqachon yozilgan).
    Real: CH'ga INSERT (parallel — PG durability source-of-truth).

    Network failure swallow qilinadi (PG yozuv allaqachon bor — Lesson learned:
    analytics integrity != transactional, eventual consistency yetadi).
    """
    if not is_real_mode():
        return

    table = getattr(settings, 'CLICKHOUSE_TABLE', 'exam_events')
    columns = list(event_data.keys())
    try:
        client = _get_client()
        client.execute(
            f'INSERT INTO {table} ({", ".join(columns)}) VALUES',
            [event_data],
        )
    except Exception as e:
        # Analytics observability matters more than data perfection
        logger.warning('ClickHouse write_event failed (PG remains source-of-truth): %s', e)


def query_aggregation(sql: str, params: dict | tuple | None = None) -> list[dict]:
    """OLAP query'lar uchun (subject avg, weekly growth, distribution).

    Stub: bo'sh list — caller PG ORM aggregate bilan ishlaydi.
    Real: CH'dan column-name dict'lar qaytaradi.
    """
    if not is_real_mode():
        return []

    try:
        client = _get_client()
        result = client.execute(sql, params or {}, with_column_types=True)
        rows, columns = result
        col_names = [c[0] for c in columns]
        return [dict(zip(col_names, row, strict=False)) for row in rows]
    except Exception as e:
        logger.error('ClickHouse query failed, falling back to empty: %s', e)
        return []
