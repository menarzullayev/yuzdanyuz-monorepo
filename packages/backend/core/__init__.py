# Django ishga tushganda Celery app ham yuklanishi uchun.
# `from core import celery_app` qilib ishlatish mumkin.
from .celery import app as celery_app

__all__ = ('celery_app',)
