"""
Celery application bootstrap.

Worker ishga tushirish:
    celery -A core worker -l info

Beat (scheduler) ishga tushirish:
    celery -A core beat -l info -S django_celery_beat.schedulers:DatabaseScheduler

Beat schedule django-celery-beat orqali admin'da boshqariladi (DatabaseScheduler).
Statik schedule'lar shu yerda `app.conf.beat_schedule`'da, lekin admin orqali
override qilish mumkin.
"""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

app = Celery('milliy_sertifikat')

# Django settings'dan CELERY_* prefiksli barcha sozlamalarni o'qiydi.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Har bir app'dagi tasks.py'ni avtomat ro'yxatga oladi.
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Sanity check task: `celery -A core call core.celery.debug_task`."""
    print(f'Request: {self.request!r}')
