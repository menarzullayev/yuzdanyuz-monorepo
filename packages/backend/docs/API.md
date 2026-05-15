# Milliy Sertifikat — API Design & HTMX Patterns

## Core Design Philosophy

- **No SPA**: HTMX + Alpine.js (server-side templating)
- **Partial HTML responses**: Not JSON (templates are reusable, CSRF tokens built-in)
- **Semantic HTTP**: Proper status codes, methods, headers
- **Progressive enhancement**: Works without JavaScript (forms submit normally)
- **HATEOAS**: Links to next actions embedded in HTML

---

## Endpoint Conventions

### URL Structure

```
/app/<model>/<action>/<id>/
/app/catalog/import/<import_batch_id>/review/
/app/exams/mock/<exam_id>/take/
/app/accounts/profile/<user_id>/edit/
```

### URL Patterns (apps/*/urls.py)

```python
from django.urls import path
from . import views

app_name = 'catalog'

urlpatterns = [
    # List & Create
    path('questions/', views.QuestionListView.as_view(), name='list'),
    path('questions/create/', views.QuestionCreateView.as_view(), name='create'),

    # Retrieve, Update, Delete
    path('questions/<uuid:pk>/', views.QuestionDetailView.as_view(), name='detail'),
    path('questions/<uuid:pk>/edit/', views.QuestionEditView.as_view(), name='edit'),
    path('questions/<uuid:pk>/delete/', views.QuestionDeleteView.as_view(), name='delete'),

    # Bulk actions
    path('questions/bulk-action/', views.QuestionBulkActionView.as_view(), name='bulk_action'),

    # Custom actions (semantic)
    path('questions/<uuid:pk>/approve/', views.QuestionApproveView.as_view(), name='approve'),
    path('questions/<uuid:pk>/flag/', views.QuestionFlagView.as_view(), name='flag'),

    # Nested resources
    path('imports/<uuid:batch_id>/review/', views.ImportReviewView.as_view(), name='import_review'),
    path('imports/<uuid:batch_id>/drafts/', views.ImportDraftsListView.as_view(), name='import_drafts'),
]
```

---

## HTTP Methods & Status Codes

| Method | Purpose | Response | Status | Notes |
|--------|---------|----------|--------|-------|
| **GET** | Retrieve resource or partial | HTML (rendered template) | 200 | Safe, idempotent |
| **POST** | Create or perform action | HTML (new partial or error) | 201 (create) / 200 (action) | Not idempotent |
| **PUT** | Full update (replace) | HTML (updated partial) | 200 | Idempotent, rarely used |
| **PATCH** | Partial update | HTML (updated partial) | 200 | Idempotent |
| **DELETE** | Delete resource | Empty (or redirect via HX-Redirect) | 204 / 303 | Idempotent |

---

## View Patterns

### Class-Based View (CBV) with HTMX

```python
from django.views.generic import View, ListView
from django.shortcuts import render
from django.http import HttpResponse
from django_htmx.http import HttpResponseClientRedirect
from core.mixins import TenantMixin, LoginRequiredMixin

class QuestionListView(LoginRequiredMixin, TenantMixin, ListView):
    """
    GET /catalog/questions/ → List of questions (partial or full page)
    """
    model = Question
    template_name = 'catalog/partials/question_list.html'
    paginate_by = 20

    def get_queryset(self):
        # Auto-filtered by TenantManager
        qs = Question.objects.all()

        # Filtering
        status = self.request.GET.get('status')
        if status:
            qs = qs.filter(status=status)

        search = self.request.GET.get('search')
        if search:
            qs = qs.filter(content__icontains=search)

        return qs.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['statuses'] = Question.STATUS_CHOICES
        return context

    def get_template_names(self):
        # HTMX request: return partial
        if self.request.headers.get('HX-Request'):
            return ['catalog/partials/question_list_table.html']
        # Full page
        return [self.template_name]


class QuestionEditView(LoginRequiredMixin, TenantMixin, View):
    """
    GET /catalog/questions/<id>/edit/ → Form (partial)
    POST /catalog/questions/<id>/edit/ → Save and return updated partial or errors
    """

    def get(self, request, pk):
        question = get_object_or_404(Question, pk=pk, organization=request.org)
        form = QuestionForm(instance=question)
        return render(request, 'catalog/partials/question_form.html', {
            'form': form,
            'question': question,
        })

    def post(self, request, pk):
        question = get_object_or_404(Question, pk=pk, organization=request.org)
        form = QuestionForm(request.POST, instance=question)

        if form.is_valid():
            form.save()
            # Return updated view (client swaps it in)
            return render(request, 'catalog/partials/question_detail.html', {
                'question': question,
            })

        # Validation failed: return form with errors
        return render(request, 'catalog/partials/question_form.html', {
            'form': form,
            'question': question,
        }, status=422)  # 422 Unprocessable Entity
```

### Function-Based View (FBV) with HTMX

```python
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from core.decorators import require_org_permission

@require_http_methods(["POST"])
@require_org_permission('questions.approve')
def approve_question(request, pk):
    """
    POST /catalog/questions/<id>/approve/ → Mark as published
    """
    question = get_object_or_404(Question, pk=pk, organization=request.org)
    question.status = 'published'
    question.save()

    # Return updated badge + out-of-band status update
    return render(request, 'catalog/partials/question_status_badge.html', {
        'question': question,
    })
```

---

## HTMX Request Pattern

### Typical HTMX HTML

```html
<!-- Client initiates HTMX request -->
<div id="draft-list-panel"
     hx-get="/catalog/drafts/"
     hx-trigger="load, draftListReload from:body"
     hx-swap="innerHTML swap:1s"
     hx-target="#draft-list-panel"
     hx-indicator=".htmx-indicator"
     hx-select=".draft-item">

  <div class="htmx-indicator">Loading drafts...</div>
</div>

<!-- Manual HTMX request from button -->
<button hx-post="/catalog/questions/{{ question.id }}/approve/"
        hx-target="#status-badge"
        hx-swap="outerHTML"
        hx-confirm="Approve this question?">
  Approve
</button>

<!-- Form submission via HTMX -->
<form hx-post="/catalog/questions/create/"
      hx-target="#question-list"
      hx-swap="afterbegin">
  {% csrf_token %}
  <input type="text" name="title" placeholder="Question title">
  <button type="submit">Create</button>
</form>
```

### Response Flow

**Server receives**:
```
POST /catalog/questions/create/ HTTP/1.1
HX-Request: true
HX-Current-URL: /catalog/
...
title=Qanday formula...&tags=4,5
```

**Server responds** (partial HTML, not JSON):
```html
HTTP/1.1 201 Created
Content-Type: text/html
HX-Redirect: /catalog/questions/123/
HX-Trigger: "questionCreated"

<!-- Empty body; client handles redirect -->
```

Or if validation fails:
```html
HTTP/1.1 422 Unprocessable Entity
Content-Type: text/html

<form hx-post="/catalog/questions/create/"
      hx-target="#question-list"
      hx-swap="afterbegin">
  {% csrf_token %}
  <input type="text" name="title" placeholder="..." value="Qanday formula...">
  <span class="error">This field is required.</span>
  <button type="submit">Create</button>
</form>
```

---

## Out-of-Band (OOB) Swaps

Use `hx-swap-oob="outerHTML"` to update multiple DOM regions in a single response:

```html
<!-- Server response includes multiple swaps -->

<!-- Primary swap (replaces hx-target) -->
<div id="main-content">
  Updated question details here...
</div>

<!-- Out-of-band swap (updates elsewhere on page) -->
<div id="question-count" hx-swap-oob="outerHTML">
  Total: 42 questions
</div>

<!-- Another OOB update -->
<div class="toast" hx-swap-oob="beforeend:#toast-container">
  Question saved successfully!
</div>
```

Django template:
```html
{% if question.status == 'published' %}
  <div id="question-count" hx-swap-oob="outerHTML">
    Total: {{ total_count }} questions
  </div>
{% endif %}

<div id="main-content">
  Question saved
</div>
```

---

## Event Handling & Client-Side State

### Alpine.js Integration

```html
<div x-data="{
  filters: { status: 'draft', search: '' },
  selectedIds: [],
  isLoading: false
}">

  <!-- Trigger HTMX reload when filters change -->
  <input x-model="filters.status"
         @change="$dispatch('filters-changed')"
         name="status">

  <div hx-trigger="filters-changed from:parent"
       hx-get="/catalog/questions/"
       @htmx:configRequest="event.detail.parameters = filters">
  </div>

  <!-- Bulk selection -->
  <input type="checkbox"
         x-model="selectedIds"
         :value="question.id"
         @change="$dispatch('selection-changed')">

  <!-- Bulk action button (disabled if none selected) -->
  <button hx-post="/catalog/questions/bulk-action/"
          hx-vals="js:{ids: selectedIds, action: 'approve'}"
          :disabled="selectedIds.length === 0">
    Approve selected
  </button>
</div>
```

### HTMX Event Dispatch

```html
<!-- Server triggers event via HX-Trigger header -->
<button hx-post="/catalog/import/123/parse/"
        @htmx:afterSwap="
          if (event.detail.xhr.status === 200) {
            $dispatch('import-completed');
          }
        ">
  Start parsing
</button>

<!-- Listener updates UI -->
<div @import-completed="selectedIndex = null; location.reload();">
  Resets form on import completion
</div>
```

---

## Error Handling

### Validation Errors (422)

```python
# View
if form.is_valid():
    form.save()
    return render(request, 'partials/success.html', status=200)

return render(request, 'partials/form_with_errors.html', {
    'form': form,
}, status=422)
```

HTML template (form_with_errors.html):
```html
<form hx-post="/submit/" hx-target="#form-container">
  {% csrf_token %}

  {% for field in form %}
    <div class="form-group">
      {{ field.label }}
      {{ field }}
      {% if field.errors %}
        <span class="error">{{ field.errors.0 }}</span>
      {% endif %}
    </div>
  {% endfor %}

  {% if form.non_field_errors %}
    <div class="alert alert-danger">
      {{ form.non_field_errors.0 }}
    </div>
  {% endif %}

  <button type="submit">Submit</button>
</form>
```

### Server Errors (500)

```python
@require_http_methods(["POST"])
def risky_operation(request):
    try:
        perform_operation()
    except ValueError as e:
        return render(request, 'partials/error_banner.html', {
            'error': str(e),
        }, status=400)  # 400 Bad Request
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        return render(request, 'partials/error_banner.html', {
            'error': 'An unexpected error occurred. Please try again.',
        }, status=500)
```

HTML template (error_banner.html):
```html
<div class="alert alert-danger" role="alert">
  <strong>Error:</strong> {{ error }}
</div>
```

### Proper Status Codes

```python
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError

# 200 OK — successful GET or non-mutating POST
return render(request, 'template.html')

# 201 Created — resource created
response = render(request, 'template.html')
response.status_code = 201
return response

# 204 No Content — deletion or no response body
return HttpResponse('', status=204)

# 303 See Other — redirect after POST (POST-Redirect-GET pattern)
from django.shortcuts import redirect
return redirect('view_name', pk=obj.pk)

# 304 Not Modified — cached response
from django.views.decorators.http import condition
@condition(etag_func=get_etag)
def my_view(request):
    return render(request, 'template.html')

# 400 Bad Request — client error (malformed data, validation)
return render(request, 'error.html', status=400)

# 401 Unauthorized — missing authentication
from django.contrib.auth.decorators import login_required

# 403 Forbidden — authenticated but lacking permission
from core.decorators import require_org_permission

# 404 Not Found
from django.http import Http404

# 422 Unprocessable Entity — validation failed (standard for forms)
return render(request, 'form.html', status=422)

# 500 Internal Server Error — unhandled exception (logged automatically)
```

---

## Pagination

### List View with Pagination

```python
class QuestionListView(ListView):
    paginate_by = 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = context['page_obj']
        context['has_next'] = page.has_next()
        context['next_page_num'] = page.next_page_number() if page.has_next() else None
        return context
```

### Template with Pagination

```html
{% for question in object_list %}
  <div class="draft-item">{{ question.title }}</div>
{% endfor %}

<!-- Pagination with HTMX infinite scroll -->
{% if has_next %}
  <div id="pagination-anchor"
       hx-get="?page={{ next_page_num }}"
       hx-trigger="revealed"
       hx-swap="afterend"
       hx-select=".draft-item">
    Loading more...
  </div>
{% endif %}
```

---

## Authentication & Permissions

### Protecting Views

```python
from django.contrib.auth.decorators import login_required
from core.decorators import require_org_permission

# Check authentication
@login_required
def view_func(request):
    pass

# Check organization permission
@require_org_permission('questions.edit')
def edit_question(request, pk):
    question = get_object_or_404(Question, pk=pk, organization=request.org)
    # Guaranteed: user has 'questions.edit' permission in current org
```

### Class-Based View Mixins

```python
from django.contrib.auth.mixins import LoginRequiredMixin
from core.mixins import TenantMixin

class QuestionEditView(LoginRequiredMixin, TenantMixin, UpdateView):
    """
    - LoginRequiredMixin: Redirects anonymous to login
    - TenantMixin: Sets request.org from middleware
    """
    model = Question
    fields = ['title', 'content', 'status']

    def get_queryset(self):
        # Only allow editing questions in user's org
        return Question.objects.filter(organization=self.request.org)
```

---

## CSRF Protection

HTMX forms automatically include CSRF token (from template context):

```html
<form hx-post="/submit/">
  {% csrf_token %}  <!-- Hidden input with token -->
  <input type="text" name="title">
  <button type="submit">Save</button>
</form>
```

Or via HTMX header:
```python
# JavaScript
document.body.addEventListener('htmx:beforeRequest', function(evt) {
    evt.detail.xhr.setRequestHeader(
        'X-CSRFToken',
        document.querySelector('[name=csrfmiddlewaretoken]').value
    );
});
```

---

## Responding to HTMX Events

### Server-Triggered Events (HX-Trigger header)

```python
def create_question(request):
    form = QuestionForm(request.POST)
    if form.is_valid():
        question = form.save()
        response = render(request, 'partials/question_created.html', {
            'question': question,
        })
        # Trigger event on client
        response['HX-Trigger'] = json.dumps({
            'questionCreated': {'id': str(question.id)},
            'updateList': None
        })
        return response
```

HTML listens:
```html
<div @htmx:afterSwap="
  if (event.detail.xhr.getResponseHeader('HX-Trigger')) {
    const triggers = JSON.parse(
      event.detail.xhr.getResponseHeader('HX-Trigger')
    );
    if (triggers.questionCreated) {
      $dispatch('question-created', triggers.questionCreated);
    }
  }
">
</div>
```

### Redirect After Form Submission

```python
def create_question(request):
    form = QuestionForm(request.POST)
    if form.is_valid():
        question = form.save()
        response = HttpResponse('')
        response['HX-Redirect'] = f'/catalog/questions/{question.id}/'
        return response
```

---

## Content Negotiation

Return different formats based on accept header or request type:

```python
def get_questions(request):
    questions = Question.objects.all()

    # API request (JSON)
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse({
            'questions': list(questions.values('id', 'title'))
        })

    # HTMX request (partial HTML)
    if request.headers.get('HX-Request'):
        return render(request, 'catalog/partials/question_list_table.html', {
            'questions': questions,
        })

    # Full page HTML
    return render(request, 'catalog/question_list.html', {
        'questions': questions,
    })
```

---

## Rate Limiting

```python
from django.core.cache import cache
from datetime import timedelta

def rate_limit_key(request, action):
    """Generate rate limit key."""
    user_id = request.user.id if request.user.is_authenticated else request.META.get('REMOTE_ADDR')
    return f"ratelimit:{user_id}:{action}"

def apply_rate_limit(request, action, limit=10, window=60):
    """Check and increment rate limit counter."""
    key = rate_limit_key(request, action)
    count = cache.get(key, 0)

    if count >= limit:
        raise PermissionDenied("Rate limit exceeded. Try again later.")

    cache.set(key, count + 1, window)

# In view
from django.core.exceptions import PermissionDenied

@require_http_methods(["POST"])
def submit_answer(request):
    try:
        apply_rate_limit(request, 'submit_answer', limit=50, window=60)
    except PermissionDenied:
        return render(request, 'partials/error.html', {
            'error': 'Too many submissions. Please wait a moment.'
        }, status=429)
```

---

## Response Headers

```python
# Cache control
response['Cache-Control'] = 'public, max-age=3600'  # 1 hour

# CORS (if needed)
response['Access-Control-Allow-Origin'] = '*'

# Security
response['X-Content-Type-Options'] = 'nosniff'
response['X-Frame-Options'] = 'DENY'

# Custom HTMX headers
response['HX-Redirect'] = '/new-url/'
response['HX-Trigger'] = json.dumps({'event': 'data'})
response['HX-Push-Url'] = '/new-url/'  # Update browser URL

# Vary header (for cache)
response['Vary'] = 'Accept, Accept-Language'
```

---

## Debugging HTMX

### Enable HTMX Debug Mode

```html
<script>
  htmx.config.debugLogLevel = 'debug';
</script>
```

### Browser DevTools

HTMX requests appear as normal XHR in Network tab:
```
Request Headers:
  HX-Request: true
  HX-Current-URL: /catalog/
  HX-Trigger: #my-button

Response Headers:
  HX-Trigger: eventName
  HX-Redirect: /new-path/
  HX-Refresh: true
```

### Server Logging

```python
import logging
logger = logging.getLogger(__name__)

def my_view(request):
    if request.headers.get('HX-Request'):
        logger.debug(f"HTMX request from {request.user}")
        logger.debug(f"Trigger: {request.headers.get('HX-Trigger')}")
    ...
```
