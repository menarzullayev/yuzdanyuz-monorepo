# The Anthropic SDK (2.x) sends requests through httpx2. Aliasing makes
# `import httpx` resolve to httpx2 for the whole process, so tools that patch
# httpx (Sentry's HttpxIntegration) see SDK traffic again. It has to run before
# anything imports httpx; `core` is the first package every entry point loads
# (manage.py via settings, core.wsgi, core.asgi, `celery -A core`).
import httpx2

try:
    httpx2.alias_httpx()
except RuntimeError:
    # httpx was imported first; the app still works, Sentry just misses SDK calls.
    pass

# Django ishga tushganda Celery app ham yuklanishi uchun.
# `from core import celery_app` qilib ishlatish mumkin.
from .celery import app as celery_app  # noqa: E402

__all__ = ('celery_app',)
