# Django ishga tushganda Celery app ham yuklanishi uchun.
# `from core import celery_app` qilib ishlatish mumkin.
#
# No httpx -> httpx2 alias here any more: sentry-sdk >= 2.62 instruments httpx2
# (the Anthropic SDK's HTTP layer) itself, and with the alias every SDK request
# was recorded twice. tests/unit/test_sentry_http.py checks this.
from .celery import app as celery_app

__all__ = ('celery_app',)
