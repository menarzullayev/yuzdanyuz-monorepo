# Milliy Sertifikat — Celery Task Architecture

## Overview

- **Message Broker**: Redis (port 6379)
- **Result Backend**: Redis (separate database)
- **Task Queue**: Default queue named `celery`
- **Worker**: `celery -A config.celery worker -l info`
- **Scheduler**: `celery -A config.celery beat -l info` (for Celery Beat)

---

## Configuration

### settings/base.py

```python
# Celery
CELERY_BROKER_URL = f"redis://:{config('REDIS_PASSWORD')}@127.0.0.1:6379/1"
CELERY_RESULT_BACKEND = f"redis://:{config('REDIS_PASSWORD')}@127.0.0.1:6379/2"
CELERY_RESULT_EXPIRES = 3600  # Results expire after 1 hour
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = 'UTC'

# Task routing
CELERY_TASK_ROUTES = {
    'catalog.tasks.parse_excel_to_drafts': {'queue': 'import'},
    'intelligence.tasks.compute_skill_profile': {'queue': 'compute'},
    'engagement.tasks.check_daily_streaks': {'queue': 'beat'},
    'analytics.tasks.stream_event_to_clickhouse': {'queue': 'analytics'},
}

# Default task settings
CELERY_TASK_DEFAULT_RETRY_DELAY = 60  # 1 minute
CELERY_TASK_MAX_RETRIES = 3
CELERY_TASK_DEFAULT_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes (SoftTimeLimitExceeded warning)

# Celery Beat schedule
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    'check-daily-streaks': {
        'task': 'engagement.tasks.check_daily_streaks',
        'schedule': crontab(hour=0, minute=0),  # Midnight UTC
        'options': {'queue': 'beat'}
    },
    'update-leagues': {
        'task': 'engagement.tasks.update_weekly_leagues',
        'schedule': crontab(hour=0, minute=0, day_of_week=1),  # Monday midnight
        'options': {'queue': 'beat'}
    },
    'cleanup-old-sessions': {
        'task': 'exams.tasks.cleanup_old_sessions',
        'schedule': crontab(hour=3, minute=0),  # 3 AM (low-traffic time)
        'options': {'queue': 'beat'}
    },
    'generate-analytics-reports': {
        'task': 'analytics.tasks.generate_hourly_reports',
        'schedule': crontab(minute=0),  # Every hour
        'options': {'queue': 'analytics'}
    },
}
```

### config/celery.py

```python
import os
from celery import Celery
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.base')

app = Celery('yuzdanyuz')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
```

---

## Task Patterns

### Basic Task (Fire & Forget)

```python
# catalog/tasks.py
from celery import shared_task
import logging

logger = logging.getLogger(__name__)

@shared_task
def send_welcome_email(user_id):
    """
    Send welcome email to new user.
    Fire-and-forget: view doesn't wait for response.
    """
    from accounts.models import CustomUser

    user = CustomUser.objects.get(id=user_id)

    # Simulate email sending
    logger.info(f"Sending welcome email to {user.email}")

    return f"Email sent to {user.email}"

# In view
from catalog.tasks import send_welcome_email

def register_user(request):
    user = CustomUser.objects.create_user(...)

    # Queue task (doesn't wait)
    send_welcome_email.delay(str(user.id))

    return render(request, 'success.html')
```

### Task with Retries

```python
# catalog/tasks.py
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def parse_excel_file(self, batch_id, file_url):
    """
    Parse Excel file to draft questions.
    Retry on transient failures (network, API timeout).
    """
    from catalog.models import ImportBatch
    import requests

    batch = ImportBatch.objects.get(id=batch_id)
    batch.status = 'parsing'
    batch.save()

    try:
        # Download file
        response = requests.get(file_url, timeout=30)
        response.raise_for_status()

        # Parse (simplified)
        questions = parse_excel(response.content)

        batch.total_imported = len(questions)
        batch.status = 'parsed'
        batch.save()

        logger.info(f"Batch {batch_id}: Parsed {len(questions)} questions")

        return {'batch_id': str(batch_id), 'count': len(questions)}

    except requests.Timeout as e:
        # Retry on timeout (transient failure)
        logger.warning(f"Batch {batch_id}: Timeout, retrying...")
        raise self.retry(exc=e, countdown=60)  # Wait 60s before retry

    except Exception as e:
        # Non-retryable error
        batch.status = 'failed'
        batch.error_log.append(str(e))
        batch.save()

        logger.error(f"Batch {batch_id}: Failed permanently: {e}")
        raise  # Don't retry

# Usage
from catalog.tasks import parse_excel_file

def start_import(request, batch_id):
    batch = ImportBatch.objects.get(id=batch_id)

    # Queue task with args
    parse_excel_file.apply_async(
        args=(str(batch_id), batch.import_file_url),
        countdown=5  # Start after 5 seconds
    )

    return JsonResponse({'status': 'parsing_started'})
```

### Task with Progress Tracking

```python
# intelligence/tasks.py
from celery import shared_task
from celery_progress.backend import ProgressRecorder

@shared_task(bind=True)
def compute_skill_profile(self, user_id):
    """
    Compute user's skill mastery scores.
    Report progress to frontend.
    """
    from accounts.models import CustomUser
    from exams.models import UserAnswer

    user = CustomUser.objects.get(id=user_id)
    progress_recorder = ProgressRecorder(self)

    # Get all answers for this user
    answers = UserAnswer.objects.filter(
        attempt__user=user
    ).select_related('question')

    # Aggregate by skill
    skill_scores = {}

    for i, answer in enumerate(answers):
        if answer.question.tags.exists():
            for tag in answer.question.tags.all():
                skill_id = str(tag.id)

                if skill_id not in skill_scores:
                    skill_scores[skill_id] = {'correct': 0, 'total': 0}

                skill_scores[skill_id]['total'] += 1
                if answer.is_correct:
                    skill_scores[skill_id]['correct'] += 1

        # Report progress
        progress_recorder.set_progress(
            i + 1,
            answers.count(),
            description=f"Processing answer {i + 1}/{answers.count()}"
        )

    # Calculate mastery scores (0-100)
    mastery = {}
    for skill_id, data in skill_scores.items():
        if data['total'] > 0:
            mastery[skill_id] = (data['correct'] / data['total']) * 100

    # Save to user profile
    from intelligence.models import UserSkillProfile
    profile, _ = UserSkillProfile.objects.get_or_create(user=user)
    profile.skill_scores = mastery
    profile.overall_mastery = sum(mastery.values()) / len(mastery) if mastery else 0
    profile.save()

    return {'user_id': str(user_id), 'skills_computed': len(mastery)}

# Frontend polling
# GET /tasks/{{ task_id }}/progress/
# Response: {"current": 50, "total": 100, "status": "progress"}
```

### Scheduled Task (Celery Beat)

```python
# engagement/tasks.py
from celery import shared_task
from celery.utils.log import get_task_logger
from datetime import date

logger = get_task_logger(__name__)

@shared_task
def check_daily_streaks():
    """
    Celery Beat task: Run every night at 00:00 UTC.
    Check if users were active today; update streaks.
    """
    from accounts.models import CustomUser
    from engagement.models import Streak
    from exams.models import PracticeSession, ExamAttempt

    today = date.today()
    updated = 0
    broken = 0

    for user in CustomUser.objects.filter(is_active=True):
        # Was user active today? (completed practice or exam)
        was_active_today = (
            PracticeSession.objects.filter(
                user=user,
                completed_at__date=today
            ).exists() or
            ExamAttempt.objects.filter(
                user=user,
                submitted_at__date=today
            ).exists()
        )

        streak, _ = Streak.objects.get_or_create(user=user)

        if was_active_today:
            # Extend streak
            streak.current_streak += 1
            streak.last_active_date = today
            if streak.current_streak > streak.longest_streak:
                streak.longest_streak = streak.current_streak
            streak.save()
            updated += 1
        else:
            # Streak broken (unless last active was today)
            if streak.last_active_date != today:
                streak.current_streak = 0
                streak.save()
                broken += 1

    logger.info(f"Streaks: Updated {updated}, Broken {broken}")
    return {'updated': updated, 'broken': broken}
```

### Task Chaining & Workflows

```python
# catalog/tasks.py
from celery import shared_task, chain, group, chord

@shared_task
def validate_draft(draft_id):
    """Step 1: Validate draft."""
    from catalog.models import QuestionDraft

    draft = QuestionDraft.objects.get(id=draft_id)
    errors = draft.validate()

    draft.validation_errors = errors
    draft.validation_status = 'valid' if not errors else 'invalid'
    draft.save()

    return str(draft_id)

@shared_task
def generate_ai_explanation(draft_id):
    """Step 2: Generate AI explanation (only if valid)."""
    from catalog.models import QuestionDraft
    from anthropic import Anthropic

    draft = QuestionDraft.objects.get(id=draft_id)

    if draft.validation_status != 'valid':
        return None  # Skip for invalid drafts

    client = Anthropic()
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"Generate explanation for: {draft.raw_content}"
        }]
    )

    draft.question.explanation = {'ai_generated': response.content[0].text}
    draft.question.save()

    return str(draft_id)

@shared_task
def notify_admin(draft_id):
    """Step 3: Notify admin for review."""
    from catalog.models import QuestionDraft
    from engagement.models import Notification
    from accounts.models import CustomUser

    draft = QuestionDraft.objects.get(id=draft_id)
    org = draft.import_batch.organization

    admins = CustomUser.objects.filter(
        membership_set__organization=org,
        membership_set__role__name='admin'
    )

    for admin in admins:
        Notification.objects.create(
            user=admin,
            notification_type='draft_ready_for_review',
            title='New draft ready for review',
            message=f'Draft from batch {draft.import_batch.id} is ready.',
            action_url=f'/catalog/drafts/{draft_id}/'
        )

    return str(draft_id)

# Workflow: Chain tasks
@shared_task
def process_import_batch(batch_id):
    """
    Process import batch with workflow:
    1. Validate all drafts
    2. Generate AI explanations (if valid)
    3. Notify admins
    """
    from catalog.models import ImportBatch

    batch = ImportBatch.objects.get(id=batch_id)

    # Get all draft IDs in batch
    draft_ids = list(batch.drafts.values_list('id', flat=True))

    # Chain: sequential tasks
    workflow = chain(
        group([validate_draft.s(str(did)) for did in draft_ids]),
        group([generate_ai_explanation.s(str(did)) for did in draft_ids]),
        group([notify_admin.s(str(did)) for did in draft_ids]),
    )

    return workflow.apply_async()

# Or use chord for reduce step
def process_import_batch_with_summary(batch_id):
    """
    Process batch and get summary.
    """
    draft_ids = list(ImportBatch.objects.get(id=batch_id).drafts.values_list('id', flat=True))

    def process_batch_summary(results):
        """Called after all drafts processed."""
        return {
            'batch_id': batch_id,
            'processed_count': len(results),
            'timestamp': datetime.utcnow().isoformat()
        }

    # Chord: parallel + callback
    callback = chord([validate_draft.s(str(did)) for did in draft_ids])(
        finalize_batch_processing.s(batch_id)
    )

    return callback

@shared_task
def finalize_batch_processing(results, batch_id):
    """Callback: Run after all drafts processed."""
    from catalog.models import ImportBatch

    batch = ImportBatch.objects.get(id=batch_id)
    batch.status = 'parsed'
    batch.total_imported = len(results)
    batch.save()

    return f"Batch {batch_id} finalized"
```

---

## Task Monitoring

### Celery Flower

Monitor tasks in real-time:

```bash
# Install
pip install flower

# Start
celery -A config.celery flower --port=5555

# Access
http://localhost:5555/
```

Features:
- Running tasks
- Task history
- Worker status
- Queue status
- Pool/concurrency

### Custom Task Logging

```python
# core/tasks.py
from celery import Task
from celery.utils.log import get_task_logger
import logging

logger = get_task_logger(__name__)

class LoggingTask(Task):
    """Base task class with detailed logging."""

    def before_start(self, task_id, args, kwargs):
        logger.info(f"Task {self.name} starting: {task_id}")

    def on_success(self, result, task_id, args, kwargs):
        logger.info(f"Task {self.name} succeeded: {result}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        logger.warning(f"Task {self.name} retrying: {exc}")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(f"Task {self.name} failed: {exc}", exc_info=einfo)

@shared_task(base=LoggingTask)
def my_task():
    return "done"
```

---

## Redis Key Patterns

```python
# Task result (auto-managed by Celery)
celery-task-meta-<task_id>

# Queue (messages)
celery

# Custom app cache keys
f"cache:{user_id}:skill_profile"
f"cache:{exam_id}:leaderboard"
f"cache:{org_id}:stats"

# Rate limiting
f"ratelimit:{user_id}:{action}"

# Session
f"session:{user_id}:fingerprint"
f"session:{user_id}:last_seen"

# Temporary state
f"draft:{draft_id}:autosave"
f"import:{batch_id}:status"
```

### Cleanup Old Redis Keys

```python
# management/commands/cleanup_redis.py
from django.core.management.base import BaseCommand
from django_redis import get_redis_connection
from datetime import timedelta
from django.utils import timezone

class Command(BaseCommand):
    help = 'Clean up expired Redis keys'

    def handle(self, *args, **options):
        redis_conn = get_redis_connection('default')

        # SCAN through keys (doesn't block)
        for key in redis_conn.scan_iter('cache:*'):
            ttl = redis_conn.ttl(key)

            if ttl == -1:  # No TTL set
                # Auto-expire old cache keys
                redis_conn.expire(key, 3600)  # 1 hour

        self.stdout.write("Redis cleanup complete")
```

---

## Dead Letter Queue (Error Handling)

```python
# celery_config for unrecoverable failures
CELERY_TASK_ACKS_LATE = True  # Acknowledge only after task completes
CELERY_REJECT_ON_WORKER_LOST = True  # Requeue if worker dies

# Manual dead letter queue
@shared_task(bind=True, max_retries=5)
def critical_task(self):
    try:
        risky_operation()
    except UnrecoverableError as e:
        # Move to dead-letter queue
        logger.error(f"Moving to DLQ: {e}")

        # Store for manual review
        from core.models import FailedTask
        FailedTask.objects.create(
            task_name='critical_task',
            error_message=str(e),
            traceback=traceback.format_exc(),
            retry_count=self.request.retries
        )

        raise  # Don't retry

    except RecoverableError:
        raise self.retry(countdown=120, max_retries=5)
```

---

## Task Naming Convention

```
# Pattern: app.tasks.action_resource
catalog.tasks.parse_excel_to_drafts
catalog.tasks.publish_question_draft
intelligence.tasks.compute_skill_profile
intelligence.tasks.generate_ai_feedback
engagement.tasks.check_daily_streaks
engagement.tasks.update_weekly_leagues
engagement.tasks.send_notification
analytics.tasks.stream_event_to_clickhouse
commerce.tasks.process_payment
```

---

## Performance Tuning

### Worker Configuration

```bash
# Start with concurrency (default = CPU count)
celery -A config.celery worker -l info --concurrency=4

# Pool type (prefork = process-based, default)
celery -A config.celery worker -l info --pool=prefork

# Time limits
celery -A config.celery worker -l info --time-limit=1800 --soft-time-limit=1500

# Autoscale (min=2, max=10)
celery -A config.celery worker -l info --autoscale=10,2
```

### Monitoring Worker Health

```python
# management/commands/check_celery_health.py
from django.core.management.base import BaseCommand
from celery.app.control import Inspect

class Command(BaseCommand):
    help = 'Check Celery worker health'

    def handle(self, *args, **options):
        from config.celery import app

        insp = Inspect(app=app)

        # Active tasks
        active = insp.active()
        print(f"Active tasks: {sum(len(v) for v in active.values())}")

        # Worker stats
        stats = insp.stats()
        for worker, stat in stats.items():
            print(f"{worker}: pool={stat['pool']['max-concurrency']} tasks")

        # Queue lengths
        reserved = insp.reserved()
        for worker, reserved_tasks in reserved.items():
            print(f"{worker}: {len(reserved_tasks)} reserved")
```
