"""
ClickHouse client abstraction (stub mode default).

Production'da: clickhouse-driver bilan real ulanish (kelajakda).
Hozir stub: events PostgreSQL'ga ExamEvent jadvaliga yoziladi,
analytics queries PG SQL aggregation orqali.

Settings:
  CLICKHOUSE_ENABLED  — bool, default False (stub)
  CLICKHOUSE_DSN      — production'da real DSN
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def is_real_mode() -> bool:
    return getattr(settings, 'CLICKHOUSE_ENABLED', False)


def write_event(event_data: dict) -> None:
    """
    Stub: PostgreSQL'da ExamEvent allaqachon yoziladi.
    Real mode: clickhouse-driver bilan parallel write (durability uchun).

    event_data — denormalized fields (subject_name, score, completed_at, etc.)
    """
    if not is_real_mode():
        # Stub: do nothing — ExamEvent service tomonidan yoziladi
        return

    # TODO production:
    # from clickhouse_driver import Client
    # client = Client.from_url(settings.CLICKHOUSE_DSN)
    # client.execute(
    #     'INSERT INTO exam_events (...) VALUES',
    #     [event_data],
    # )
    raise NotImplementedError('Real ClickHouse integratsiyasi kelajakda')


def query_aggregation(sql: str, params: tuple = ()) -> list:
    """
    Stub: bo'sh list qaytaradi (caller PG ORM bilan ishlaydi).
    Real mode: ClickHouse'dan SELECT query.
    """
    if not is_real_mode():
        return []
    raise NotImplementedError('Real ClickHouse integratsiyasi kelajakda')
