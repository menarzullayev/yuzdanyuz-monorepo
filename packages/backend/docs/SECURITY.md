# Milliy Sertifikat — Security Architecture

## Overview

Multi-layer security approach:
1. **Request level** — Device fingerprinting, session locking
2. **Database level** — Row-level security (RLS), encryption at rest
3. **API level** — Permission checks, CSRF protection, rate limiting

---

## Authentication Flow

### Multi-Auth Methods

```
User arrives at /login/
    ├─ Google OAuth 2.0 (web)
    ├─ Telegram Bot (telegram_login)
    ├─ Telegram Deep-Link (one-click sign-in)
    └─ Phone OTP (SMS)
        ├─ PlayMobile (primary)
        └─ Eskiz (backup)
```

### JWT + Redis Session

```python
# accounts/auth.py
from datetime import timedelta
import jwt
from django.conf import settings
from django_redis import get_redis_connection

def create_tokens(user):
    """Create JWT access token (15 min) + refresh token (30 days in Redis)."""

    # Access token (short-lived, includes user info)
    access_token = jwt.encode({
        'user_id': str(user.id),
        'username': user.username,
        'exp': datetime.utcnow() + timedelta(minutes=15),
        'device_fingerprint': request.device_fingerprint,  # Anti-replay
    }, settings.SECRET_KEY, algorithm='HS256')

    # Refresh token (long-lived, stored in Redis only)
    refresh_token = generate_random_token(32)
    redis_conn = get_redis_connection('default')

    redis_conn.setex(
        f"refresh_token:{user.id}:{refresh_token}",
        30 * 24 * 60 * 60,  # 30 days
        json.dumps({
            'user_id': str(user.id),
            'device_fingerprint': request.device_fingerprint,
            'created_at': datetime.utcnow().isoformat(),
        })
    )

    return access_token, refresh_token

def verify_access_token(token):
    """Verify JWT and check device fingerprint."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

    # Verify device hasn't changed
    if payload.get('device_fingerprint') != request.device_fingerprint:
        raise SessionCompromisedError("Device fingerprint mismatch")

    return payload
```

---

## Device Fingerprinting & Session Locking

### Fingerprint Generation

```python
# core/fingerprinting.py
import hashlib
import json
from functools import lru_cache

def generate_device_fingerprint(request):
    """
    Hash browser + OS + IP to lock session to one device.
    One user = one active session at a time (like Netflix).
    """

    # Extract components
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    remote_ip = get_client_ip(request)

    # Client sends via JavaScript
    client_data = request.GET.get('device_data', '{}')
    try:
        device_data = json.loads(client_data)
    except:
        device_data = {}

    browser_name = device_data.get('browserName', 'unknown')  # Chrome, Safari, etc.
    os_name = device_data.get('osName', 'unknown')  # Windows, iOS, etc.

    # Hash them together
    fingerprint_string = f"{browser_name}|{os_name}|{remote_ip}"
    fingerprint = hashlib.sha256(fingerprint_string.encode()).hexdigest()

    return fingerprint

def get_client_ip(request):
    """Extract client IP, handling proxies (Nginx, Cloudflare)."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
```

### Middleware: Device Fingerprint Check

```python
# core/middleware.py
from django.http import JsonResponse
from .fingerprinting import generate_device_fingerprint
from django_redis import get_redis_connection

class DeviceFingerprintMiddleware:
    """
    On every request:
    1. Generate current device fingerprint
    2. Check if it matches the session's registered fingerprint
    3. If mismatch: Kill session, require re-login
    4. If match: Update last-seen timestamp (keep session alive)
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user

        if user.is_authenticated:
            current_fingerprint = generate_device_fingerprint(request)
            request.device_fingerprint = current_fingerprint

            # Check Redis for registered fingerprint
            redis_conn = get_redis_connection('default')
            session_key = f"session:{user.id}:fingerprint"
            registered_fingerprint = redis_conn.get(session_key)

            if registered_fingerprint and registered_fingerprint.decode() != current_fingerprint:
                # Device mismatch: kill session
                from django.contrib.auth import logout
                logout(request)

                # Redirect to login with warning
                return JsonResponse({
                    'error': 'Your session was accessed from another device. Please log in again.',
                    'status': 'device_changed'
                }, status=401)

            # Update session fingerprint & last-seen (TTL = 15 min access token)
            redis_conn.setex(session_key, 15 * 60, current_fingerprint)
            redis_conn.setex(f"session:{user.id}:last_seen", 15 * 60, datetime.utcnow().isoformat())

        response = self.get_response(request)
        return response
```

### Login: Lock Session to Device

```python
# accounts/views.py
from django.contrib.auth import authenticate, login

class LoginView(View):
    def post(self, request):
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user:
            # Authenticate with device lock
            login(request, user)

            # Generate and store device fingerprint
            fingerprint = generate_device_fingerprint(request)
            redis_conn = get_redis_connection('default')
            redis_conn.setex(
                f"session:{user.id}:fingerprint",
                30 * 24 * 60 * 60,  # 30 days
                fingerprint
            )

            # Store refresh token
            refresh_token = generate_random_token(32)
            redis_conn.setex(
                f"refresh_token:{user.id}:{refresh_token}",
                30 * 24 * 60 * 60,
                json.dumps({'device_fingerprint': fingerprint})
            )

            return render(request, 'accounts/login_success.html', {
                'redirect_url': request.GET.get('next', '/'),
            })

        return render(request, 'accounts/login.html', {
            'error': 'Invalid credentials',
        }, status=401)
```

### Logout: Clear Session

```python
def logout_view(request):
    """Clear device fingerprint from Redis."""
    user = request.user

    if user.is_authenticated:
        redis_conn = get_redis_connection('default')
        redis_conn.delete(f"session:{user.id}:fingerprint")
        redis_conn.delete(f"session:{user.id}:last_seen")

        # Also delete all refresh tokens for this user
        for key in redis_conn.scan_iter(f"refresh_token:{user.id}:*"):
            redis_conn.delete(key)

    logout(request)
    return redirect('accounts:login')
```

---

## Authorization & Permissions

### Two-Layer RBAC

```python
# Layer 1: Platform-level (built-in Django)
if user.is_superuser:
    # Full access (platform admin)
    can_delete_organizations = True
    can_view_all_users = True

if user.is_staff:
    # Staff-level access
    can_moderate_content = True
    can_view_analytics = True

# Layer 2: Organization-level (custom)
if user.has_org_permission('questions.edit'):
    # Can edit questions in current org
    can_edit = True
```

### CustomUser Permissions Method

```python
# accounts/models.py
class CustomUser(AbstractUser):
    def has_org_permission(self, org, permission):
        """
        Check if user has a specific permission in an org.

        Args:
            org: Organization object
            permission: String like 'questions.edit' or 'exams.create'
                       Can also be '*' for wildcard (all permissions)

        Returns:
            bool
        """
        if self.is_superuser:
            return True

        # Get membership in this org
        membership = self.membership_set.filter(
            organization=org,
            status='active'
        ).first()

        if not membership:
            return False

        # Check role permissions
        return membership.role.has_permission(permission)
```

### Permission Decorator

```python
# core/decorators.py
from functools import wraps
from django.core.exceptions import PermissionDenied
from core.tenant import get_current_org

def require_org_permission(permission):
    """
    Decorator to check organization permission.

    Usage:
        @require_org_permission('questions.edit')
        def edit_question(request, pk):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            org = get_current_org()

            if not request.user.has_org_permission(org, permission):
                raise PermissionDenied(
                    f"You don't have '{permission}' permission in {org.name}"
                )

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
```

### Permission Check in Views

```python
from core.decorators import require_org_permission

@require_org_permission('questions.create')
def create_question(request):
    # Only users with 'questions.create' in their org role reach here
    ...

class EditQuestionView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        # Check permission
        org = request.org  # Set by TenantMiddleware
        if not request.user.has_org_permission(org, 'questions.edit'):
            raise PermissionDenied("Cannot edit questions")

        return super().dispatch(request, *args, **kwargs)
```

---

## Row-Level Security (RLS)

PostgreSQL enforces tenant isolation at the database layer:

```sql
-- Enable RLS on all tenant-aware tables
ALTER TABLE catalog_question ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams_mockexam ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts_customuser ENABLE ROW LEVEL SECURITY;
-- ... etc

-- Policy: Users can only see records from their organization
CREATE POLICY org_isolation ON catalog_question
  AS PERMISSIVE FOR SELECT
  USING (organization_id = current_setting('app.current_org_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid);

CREATE POLICY org_isolation ON exams_mockexam
  AS PERMISSIVE FOR SELECT
  USING (organization_id = current_setting('app.current_org_id')::uuid);

-- Admin bypass: SET ROLE admin;
CREATE ROLE admin WITH SUPERUSER;
ALTER DATABASE yuzdanyuz_db OWNER TO admin;
```

### Setting RLS Context in Django

```python
# core/middleware.py
from django.db import connection
from core.tenant import get_current_org

class RLSMiddleware:
    """Set PostgreSQL RLS context on each request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        org = get_current_org()

        if org:
            # Set RLS policy variable for this connection
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET app.current_org_id = %s",
                    [str(org.id)]
                )

        response = self.get_response(request)
        return response
```

---

## CSRF Protection

Django's CSRF middleware is enabled by default:

```python
# settings/base.py
MIDDLEWARE = [
    ...
    'django.middleware.csrf.CsrfViewMiddleware',
    ...
]

# All POST/PUT/PATCH/DELETE requests require CSRF token
```

### In Templates

```html
<!-- Include CSRF token in all forms -->
<form method="post" action="/submit/">
  {% csrf_token %}
  <input type="text" name="title">
  <button type="submit">Save</button>
</form>

<!-- Or as hidden input -->
<input type="hidden" name="csrfmiddlewaretoken" value="{{ csrf_token }}">
```

### With AJAX/HTMX

```javascript
// Include CSRF token in all AJAX requests
document.addEventListener('htmx:configRequest', function(evt) {
  evt.detail.headers['X-CSRFToken'] = document.querySelector('[name=csrfmiddlewaretoken]').value;
});
```

---

## Rate Limiting

### Cache-Based Rate Limiting

```python
# core/rate_limit.py
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from datetime import timedelta

def rate_limit(request, action, limit=10, window_seconds=60):
    """
    Check rate limit for a user/action pair.

    Args:
        request: HttpRequest
        action: str like 'login', 'submit_answer', 'api_call'
        limit: int max requests
        window_seconds: int time window

    Raises:
        PermissionDenied if limit exceeded
    """

    user_id = request.user.id if request.user.is_authenticated else get_client_ip(request)
    key = f"ratelimit:{user_id}:{action}"

    count = cache.get(key, 0)

    if count >= limit:
        remaining_ttl = cache.ttl(key)
        raise PermissionDenied(
            f"Too many {action} attempts. Try again in {remaining_ttl} seconds."
        )

    # Increment counter
    cache.set(key, count + 1, window_seconds)

# Usage in view
@require_http_methods(["POST"])
def submit_answer(request):
    try:
        rate_limit(request, 'submit_answer', limit=50, window_seconds=60)
    except PermissionDenied as e:
        return render(request, 'error.html', {
            'error': str(e)
        }, status=429)  # 429 Too Many Requests
```

### Rate Limit Tiers by User Type

```python
def get_rate_limit_tier(request):
    """Determine rate limit based on user type."""
    user = request.user

    if not user.is_authenticated:
        return {'submit_answer': 5, 'api_call': 10}  # Tight for anon

    if user.is_superuser:
        return {'submit_answer': 1000, 'api_call': 1000}  # No limit for admin

    if user.is_staff:
        return {'submit_answer': 500, 'api_call': 500}  # Loose for staff

    # Regular users
    return {'submit_answer': 50, 'api_call': 100}
```

---

## Question Content Protection

### Dynamic Watermarking

Prevent screenshots/copying by rendering questions dynamically:

```python
# catalog/views.py
def question_detail(request, pk):
    question = get_object_or_404(Question, pk=pk, organization=request.org)

    # Don't cache user-specific views
    response = render(request, 'catalog/question_detail.html', {
        'question': question,
        'user_id': str(request.user.id),  # For watermark
        'timestamp': datetime.utcnow().isoformat(),
    })

    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    return response
```

Template with watermark:
```html
<div class="question-content"
     style="background-image:
       radial-gradient(circle at 30% 50%,
         rgba(200,200,200,0.15) 0%,
         rgba(200,200,200,0.05) 50%)">

  <!-- Watermark text (semi-transparent, angled) -->
  <div style="position: fixed; width: 100%; height: 100%;
    transform: rotate(-45deg); opacity: 0.1;
    pointer-events: none; z-index: -1; font-size: 48px; color: gray;">
    {{ user_id }}
  </div>

  <h1>{{ question.title }}</h1>
  <p>{{ question.content }}</p>
</div>
```

### DOM Obfuscation

```html
<!-- Prevent easy element inspection -->
<div class="question"
     @htmx:load="
       // Scramble DOM on page load
       document.querySelectorAll('.answer-choice').forEach(el => {
         el.style.order = Math.random();
       });
     ">
</div>
```

---

## Exam Anti-Cheat

### Browser Lock (Fullscreen, Block Keys)

```html
<!-- exam_detail.html -->
<div x-data="examAntiCheat()"
     @load="init()"
     @beforeunload="onExit">

  <div id="exam-container"
       @keydown.ctrl.c="$event.preventDefault()"
       @keydown.ctrl.v="$event.preventDefault()"
       @keydown.f12="$event.preventDefault()"
       @keydown.ctrl.shift.i="$event.preventDefault()">

    <!-- Exam content -->
  </div>
</div>

<script>
function examAntiCheat() {
  return {
    init() {
      // Request fullscreen
      document.documentElement.requestFullscreen()
        .catch(err => alert('Please enable fullscreen to take exam'));

      // Detect tab switch
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
          htmx.ajax('POST', '/exam/flag-cheat/', {
            target: '#exam-container',
            values: { reason: 'tab_switch' }
          });
        }
      });

      // Disable right-click
      document.addEventListener('contextmenu', e => e.preventDefault());
    }
  }
}
</script>
```

### Server-Side Heartbeat & Monitoring

```python
# exams/tasks.py
from celery import shared_task
import logging

@shared_task
def monitor_exam_activity(attempt_id):
    """
    Called every 10 seconds during exam.
    Checks for suspicious activity.
    """
    attempt = ExamAttempt.objects.get(id=attempt_id)

    # Check 1: Multiple IPs from same user during exam
    ips = list(UserAnswer.objects.filter(
        attempt=attempt
    ).values_list('ip_address', flat=True).distinct())

    if len(set(ips)) > 1:
        attempt.is_cheating_flagged = True
        attempt.cheating_reason = 'Multiple IPs detected'
        attempt.save()
        logger.warning(f"Attempt {attempt_id}: Multiple IPs {ips}")

    # Check 2: Unusual answer patterns
    answers = UserAnswer.objects.filter(attempt=attempt)

    correct_in_a_row = 0
    for answer in answers:
        if answer.is_correct:
            correct_in_a_row += 1
        else:
            correct_in_a_row = 0

    if correct_in_a_row > 20:  # Suspiciously perfect
        attempt.is_cheating_flagged = True
        attempt.cheating_reason = 'Suspicious success pattern'
        attempt.save()
        logger.warning(f"Attempt {attempt_id}: {correct_in_a_row} correct in a row")
```

### Quarantine Flagged Questions

```python
# exams/views.py
def submit_answer(request, attempt_id, question_id):
    attempt = ExamAttempt.objects.get(id=attempt_id)
    question = Question.objects.get(id=question_id)

    # Check if question is quarantined
    if question.is_quarantined:
        return render(request, 'error.html', {
            'error': 'This question has been temporarily removed. Your answer was not recorded.',
        }, status=410)  # 410 Gone

    # ... normal answer processing ...

    # Update complaint count
    if request.POST.get('report'):
        question.complaint_count += 1
        question.save()

        # Auto-quarantine if 5% of takers report it
        total_takers = ExamAttempt.objects.filter(
            exam=attempt.exam,
            submitted_at__isnull=False
        ).count()

        if question.complaint_count / total_takers > 0.05:
            question.is_quarantined = True
            question.save()

            # Award bonus points to all takers
            affected_attempts = ExamAttempt.objects.filter(exam=attempt.exam)
            for att in affected_attempts:
                att.total_score += 10
                att.save()
```

---

## PII & Data Privacy

### Personally Identifiable Information (PII) Handling

```python
# accounts/models.py
class CustomUser(AbstractUser):
    phone = models.CharField(max_length=20, ...)  # PII
    email = models.EmailField(...)  # PII
    date_of_birth = models.DateField(...)  # PII

    class Meta:
        # Don't log PII fields
        sensitive_fields = ['password', 'phone', 'email', 'date_of_birth']
```

### Audit Logging (without PII)

```python
# core/audit.py
import logging

audit_logger = logging.getLogger('audit')

def log_user_action(request, action, resource_type, resource_id, status='success'):
    """
    Log user action without including PII.
    """
    audit_logger.info(
        f"[{status}] {action}",
        extra={
            'user_id': request.user.id,  # OK (UUID)
            'org_id': request.org.id,     # OK (UUID)
            'resource_type': resource_type,
            'resource_id': resource_id,
            'ip_address': get_client_ip(request),
            'timestamp': datetime.utcnow().isoformat(),
        }
    )

# Usage
def edit_question(request, pk):
    log_user_action(request, 'edit', 'question', pk)
```

### Data Retention & Deletion

```python
# accounts/management/commands/cleanup_old_data.py
from django.core.management.base import BaseCommand
from datetime import timedelta
from django.utils import timezone

class Command(BaseCommand):
    help = 'Delete old user data per retention policy'

    def handle(self, *args, **options):
        # Delete practice sessions older than 90 days
        cutoff = timezone.now() - timedelta(days=90)

        old_sessions = PracticeSession.objects.filter(completed_at__lt=cutoff)
        count, _ = old_sessions.delete()

        self.stdout.write(f"Deleted {count} old practice sessions")
```

---

## Encryption

### Sensitive Data at Rest

```python
# settings/prod.py
from django_encrypted_model_fields.fields import EncryptedTextField

class WalletTransaction(models.Model):
    # Sensitive fields are encrypted in DB
    wallet_snapshot = EncryptedTextField()  # Encrypted JSON
```

### HTTPS Only

```python
# settings/prod.py
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000

# Nginx config
server {
    listen 443 ssl http2;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
}
```

---

## Security Checklist

- [ ] HTTPS enforced in production
- [ ] CSRF tokens on all forms
- [ ] Rate limiting on sensitive endpoints
- [ ] Device fingerprinting session lock active
- [ ] RLS policies enabled in PostgreSQL
- [ ] Permission checks in views (@require_org_permission)
- [ ] PII not logged or exposed in errors
- [ ] Secrets (.env, API keys) not in version control
- [ ] SQL injection impossible (using ORM)
- [ ] XSS prevented (template autoescaping enabled)
- [ ] CORS headers set appropriately
- [ ] Audit logging configured
- [ ] Backups encrypted and tested
- [ ] Anti-cheat monitoring for exams
- [ ] Content watermarking on questions
