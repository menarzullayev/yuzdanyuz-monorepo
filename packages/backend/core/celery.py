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


# ISSUE-301: Task routing — per-queue SLO. AI task'lar (10s+ Anthropic)
# critical task'lar (50ms finalize)'ni blok qilmasligi uchun alohida queue.
#
# Queue'lar:
#   critical  — exam submit, payment webhook (p95 < 1s SLO, low latency)
#   realtime  — notification fan-out (user-facing, p95 < 3s)
#   ai        — Anthropic API calls (slow, costly, 10-60s)
#   batch     — periodic scheduled work (no latency SLO)
#   default   — har narsa boshqa
#
# Helm chart: har queue uchun alohida celery-worker deployment kerak
# (resource limits + concurrency tuning per workload).
app.conf.task_routes = {
    # Critical — user buyer/exam-taker uchun darhol natija
    'exams.finalize_attempt_score': {'queue': 'critical'},
    # Real-time — user-facing notification
    'engagement.fan_out_notification': {'queue': 'realtime'},
    # AI — Anthropic API (slow + costly)
    'intelligence.generate_ai_tutor_feedback': {'queue': 'ai'},
    'intelligence.evaluate_openended_submission': {'queue': 'ai'},
    # Batch — periodic scheduled work
    'engagement.archive_leaderboards': {'queue': 'batch'},
    'engagement.check_broken_streaks': {'queue': 'batch'},
    'engagement.leagues_weekly_recalc': {'queue': 'batch'},
    'commerce.auto_renew_subscriptions': {'queue': 'batch'},
    'analytics.generate_export': {'queue': 'batch'},
    'exams.publish_scheduled_mocks': {'queue': 'batch'},
    'exams.quarantine_check': {'queue': 'batch'},
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Sanity check task: `celery -A core call core.celery.debug_task`."""
    print(f'Request: {self.request!r}')
