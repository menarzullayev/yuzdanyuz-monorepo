# Milliy Sertifikat — Testing Architecture

## Overview

- **Framework**: pytest + pytest-django
- **Coverage**: Aim for 80%+ on critical paths
- **TDD**: Write tests first for bug fixes
- **Integration**: Real DB (sqlite for tests), real Redis
- **CI/CD**: Automated on every push

---

## Test Structure

```
tests/
├── conftest.py                    # Shared fixtures
├── fixtures/
│   ├── users.py                   # User fixtures
│   ├── orgs.py                    # Organization fixtures
│   └── questions.py               # Question fixtures
│
├── unit/
│   ├── accounts/
│   │   ├── test_models.py         # CustomUser, Region, District
│   │   ├── test_auth.py           # Login, device fingerprint
│   │   └── test_permissions.py    # has_org_permission
│   │
│   ├── catalog/
│   │   ├── test_models.py         # Question, Tag, ImportBatch
│   │   └── test_validation.py     # Question validation
│   │
│   └── ...
│
├── integration/
│   ├── test_import_workflow.py    # Upload → Parse → Review → Publish
│   ├── test_exam_workflow.py      # Start → Answer → Submit → Score
│   ├── test_auth_flow.py          # Register → Login → Device check → Logout
│   └── test_payment_flow.py       # Wallet → Payment → Transaction
│
├── e2e/
│   ├── test_review_panel.py       # Full review panel workflow
│   └── test_exam_taking.py        # Full exam experience
│
└── performance/
    ├── test_question_search.py    # Meilisearch performance
    └── test_leaderboard_query.py  # Redis ZSET performance
```

---

## Configuration

### pytest.ini

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings.test
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = --strict-markers --tb=short --cov=. --cov-report=html
testpaths = tests
markers =
    slow: marks tests as slow (deselect with '-m "not slow"')
    integration: marks tests as integration tests
    e2e: marks tests as end-to-end tests
    performance: marks tests as performance tests
```

### settings/test.py

```python
from .base import *

# Use sqlite for tests (faster, in-memory)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'ATOMIC_REQUESTS': True,
    }
}

# Disable migrations (use model schema directly)
class DisableMigrations:
    def __contains__(self, item):
        return True
    def __getitem__(self, item):
        return None

MIGRATION_MODULES = DisableMigrations()

# Celery
CELERY_TASK_ALWAYS_EAGER = True  # Execute tasks synchronously
CELERY_TASK_EAGER_PROPAGATES = True

# Disable cache for consistent tests
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    }
}

# Speed up password hashing
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

DEBUG = True
```

---

## Fixtures

### conftest.py (Shared)

```python
# tests/conftest.py
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from core.tenant import tenant_context

CustomUser = get_user_model()

@pytest.fixture
def user(db):
    """Create a regular user."""
    return CustomUser.objects.create_user(
        username='testuser',
        email='test@example.com',
        phone='+998901234567',
        password='testpass123'
    )

@pytest.fixture
def admin_user(db):
    """Create a superuser."""
    return CustomUser.objects.create_superuser(
        username='admin',
        email='admin@example.com',
        password='adminpass123'
    )

@pytest.fixture
def organization(db, user):
    """Create a test organization."""
    from organizations.models import Organization, OrgRole

    org = Organization.objects.create(
        name='Test School',
        slug='test-school',
        org_type='b2b',
        owner=user
    )

    # Create default roles
    OrgRole.objects.create(
        organization=org,
        name='admin',
        permissions=['*']  # All permissions
    )

    OrgRole.objects.create(
        organization=org,
        name='teacher',
        permissions=['questions.create', 'questions.edit', 'exams.view']
    )

    return org

@pytest.fixture
def api_client():
    """DRF API client."""
    return APIClient()

@pytest.fixture
def auth_client(api_client, user):
    """Authenticated API client."""
    api_client.force_authenticate(user=user)
    return api_client
```

### fixtures/orgs.py

```python
# tests/fixtures/orgs.py
import pytest
from organizations.models import Organization, OrgRole, Membership

@pytest.fixture
def org_with_users(db, user, admin_user):
    """Create org with admin and regular member."""
    org = Organization.objects.create(
        name='Test Center',
        slug='test-center',
        org_type='b2b',
        owner=admin_user
    )

    admin_role = OrgRole.objects.create(
        organization=org,
        name='admin',
        permissions=['*']
    )

    teacher_role = OrgRole.objects.create(
        organization=org,
        name='teacher',
        permissions=['questions.create', 'questions.edit']
    )

    Membership.objects.create(
        user=admin_user,
        organization=org,
        role=admin_role,
        status='active'
    )

    Membership.objects.create(
        user=user,
        organization=org,
        role=teacher_role,
        status='active'
    )

    return org
```

### fixtures/questions.py

```python
# tests/fixtures/questions.py
import pytest
from catalog.models import Question, QuestionBank, Tag

@pytest.fixture
def question_bank(organization):
    """Create a question bank."""
    return QuestionBank.objects.create(
        organization=organization,
        name='Math Questions',
        is_public=False,
        subject='Matematika'
    )

@pytest.fixture
def question(organization, question_bank):
    """Create a test question."""
    return Question.objects.create(
        organization=organization,
        question_bank=question_bank,
        content={'uz': 'Savolning mazmuni', 'en': 'Question text'},
        answer_type='multiple_choice',
        answer_choices=[
            {'label': 'A', 'text': {'uz': 'Javob A'}, 'is_correct': True},
            {'label': 'B', 'text': {'uz': 'Javob B'}, 'is_correct': False},
            {'label': 'C', 'text': {'uz': 'Javob C'}, 'is_correct': False},
            {'label': 'D', 'text': {'uz': 'Javob D'}, 'is_correct': False},
        ],
        correct_answer={'selected': 'A'},
        status='published',
        difficulty=2
    )

@pytest.fixture
def tag(organization):
    """Create a tag."""
    return Tag.objects.create(
        organization=organization,
        name='Algebra',
        tag_type='subject'
    )
```

---

## Unit Tests

### Model Tests

```python
# tests/unit/accounts/test_models.py
import pytest
from accounts.models import CustomUser

class TestCustomUserModel:
    """Test CustomUser model."""

    def test_user_creation(self, db):
        """User can be created with username and email."""
        user = CustomUser.objects.create_user(
            username='john',
            email='john@example.com',
            password='pass123'
        )

        assert user.username == 'john'
        assert user.email == 'john@example.com'
        assert user.check_password('pass123')

    def test_user_type_platform_admin(self, admin_user):
        """Superuser has user_type = platform_admin."""
        assert admin_user.user_type == 'platform_admin'

    def test_user_type_b2c(self, user):
        """User without membership has user_type = b2c."""
        assert user.user_type == 'b2c'

    def test_has_org_permission_success(self, user, organization):
        """User with permission returns True."""
        from organizations.models import OrgRole, Membership

        role = OrgRole.objects.create(
            organization=organization,
            name='teacher',
            permissions=['questions.edit']
        )

        Membership.objects.create(
            user=user,
            organization=organization,
            role=role
        )

        assert user.has_org_permission(organization, 'questions.edit')

    def test_has_org_permission_denied(self, user, organization):
        """User without permission returns False."""
        assert not user.has_org_permission(organization, 'questions.delete')

    def test_has_org_permission_wildcard(self, user, organization):
        """User with '*' permission has all permissions."""
        from organizations.models import OrgRole, Membership

        role = OrgRole.objects.create(
            organization=organization,
            name='admin',
            permissions=['*']
        )

        Membership.objects.create(
            user=user,
            organization=organization,
            role=role
        )

        assert user.has_org_permission(organization, 'any.permission')
```

### View Tests

```python
# tests/unit/catalog/test_views.py
import pytest
from django.urls import reverse
from catalog.models import Question

class TestQuestionListView:
    """Test question list view."""

    def test_list_questions_unauthorized(self, client):
        """Anonymous user is redirected to login."""
        response = client.get(reverse('catalog:list'))

        assert response.status_code == 302
        assert '/login/' in response.url

    def test_list_questions_authorized(self, client, user, question):
        """Authenticated user sees their org's questions."""
        client.force_login(user)

        response = client.get(reverse('catalog:list'))

        assert response.status_code == 200
        assert 'questions' in response.context

    def test_list_questions_filtering(self, client, user, question, db):
        """Questions are filtered by status."""
        from catalog.models import Question
        from core.tenant import tenant_context

        client.force_login(user)

        # Create another question with different status
        with tenant_context(question.organization):
            draft = Question.objects.create(
                organization=question.organization,
                question_bank=question.question_bank,
                content={'uz': 'Draft question'},
                answer_type='multiple_choice',
                answer_choices=[],
                status='draft'
            )

        # Filter by status
        response = client.get(reverse('catalog:list') + '?status=draft')

        assert response.status_code == 200
        # Check that only draft is in results
        assert draft in response.context['object_list']

    def test_list_questions_multi_tenant_isolation(self, client, user, organization, question, db):
        """User only sees questions from their organization."""
        from organizations.models import Organization, OrgRole, Membership
        from catalog.models import Question as Q
        from core.tenant import tenant_context

        # Create another org
        other_org = Organization.objects.create(
            name='Other School',
            slug='other-school',
            org_type='b2b',
            owner=user
        )

        role = OrgRole.objects.create(organization=other_org, name='teacher', permissions=[])
        Membership.objects.create(user=user, organization=other_org, role=role)

        # Create question in other org
        with tenant_context(other_org):
            other_question = Q.objects.create(
                organization=other_org,
                question_bank=question.question_bank,
                content={'uz': 'Other org question'},
                answer_type='multiple_choice',
                answer_choices=[],
                status='published'
            )

        # In user's current org
        client.force_login(user)
        response = client.get(reverse('catalog:list'), {'org': str(organization.id)})

        # Should not see other org's question
        assert question in response.context['object_list']
        assert other_question not in response.context['object_list']
```

---

## Integration Tests

### Import Workflow

```python
# tests/integration/test_import_workflow.py
import pytest
from django.files.uploadedfile import SimpleUploadedFile
from catalog.models import ImportBatch, QuestionDraft
from core.tenant import tenant_context

@pytest.mark.integration
class TestImportWorkflow:
    """Test full import → review → publish workflow."""

    def test_import_excel_to_publish(self, client, user, organization):
        """Complete import workflow: upload → parse → approve → publish."""
        client.force_login(user)

        # Step 1: Upload file
        excel_file = SimpleUploadedFile(
            'questions.xlsx',
            b'xlsx content here',
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

        response = client.post(
            reverse('catalog:import_create'),
            {'import_file': excel_file},
            follow=True
        )

        assert response.status_code == 200
        batch = ImportBatch.objects.latest('id')
        assert batch.status == 'pending'

        # Step 2: Parse (triggers Celery task)
        response = client.post(
            reverse('catalog:import_parse', args=[batch.id]),
            follow=True
        )

        # Task runs synchronously in tests (CELERY_TASK_ALWAYS_EAGER)
        batch.refresh_from_db()
        assert batch.status == 'parsed'

        # Drafts created
        drafts = batch.drafts.all()
        assert drafts.count() > 0

        # Step 3: Review and approve draft
        draft = drafts.first()
        response = client.post(
            reverse('catalog:draft_approve', args=[draft.id]),
            {'review_notes': 'Looks good'},
            follow=True
        )

        draft.refresh_from_db()
        assert draft.approval == 'approved'

        # Step 4: Publish batch
        response = client.post(
            reverse('catalog:import_publish', args=[batch.id]),
            follow=True
        )

        batch.refresh_from_db()
        assert batch.status == 'published'
        assert draft.question.status == 'published'
```

### Exam Workflow

```python
# tests/integration/test_exam_workflow.py
import pytest
from exams.models import MockExam, ExamAttempt, UserAnswer
from django.urls import reverse

@pytest.mark.integration
class TestExamWorkflow:
    """Test full exam taking workflow."""

    def test_take_exam_and_submit(self, client, user, db):
        """User takes exam, answers questions, submits, gets score."""
        from exams.models import MockExam, ExamQuestion
        from datetime import datetime, timedelta

        client.force_login(user)

        # Create exam with 2 questions
        exam = MockExam.objects.create(
            organization=user.org,
            name='Weekly Mock',
            duration_minutes=60,
            scheduled_at=datetime.utcnow(),
        )

        questions = [pytest.lazy_fixture('question') for _ in range(2)]

        for i, q in enumerate(questions):
            ExamQuestion.objects.create(
                exam=exam,
                question=q,
                order=i
            )

        # Step 1: Start exam
        response = client.get(
            reverse('exams:exam_detail', args=[exam.id])
        )

        assert response.status_code == 200

        # Create attempt
        attempt = ExamAttempt.objects.create(
            user=user,
            exam=exam,
            started_at=datetime.utcnow()
        )

        # Step 2: Submit answers
        for question in questions:
            response = client.post(
                reverse('exams:submit_answer', args=[attempt.id, question.id]),
                {'answer': 'A'},
            )

            assert response.status_code == 200

        # Step 3: Submit exam
        response = client.post(
            reverse('exams:submit_exam', args=[attempt.id]),
            follow=True
        )

        attempt.refresh_from_db()
        assert attempt.submitted_at is not None
        assert attempt.percentage > 0
```

---

## Mocking & Patching

### Mock External APIs

```python
# tests/unit/accounts/test_auth.py
import pytest
from unittest.mock import patch, MagicMock

class TestGoogleOAuth:
    """Test Google OAuth login."""

    @patch('accounts.auth.oauth2_session.fetch_token')
    def test_google_login_success(self, mock_fetch_token, client):
        """Google OAuth login creates/updates user."""
        mock_fetch_token.return_value = {
            'access_token': 'fake_token',
            'id_token': 'fake_id_token'
        }

        with patch('accounts.auth.GoogleIdTokenVerifier.verify') as mock_verify:
            mock_verify.return_value = {
                'sub': 'google_user_123',
                'email': 'user@gmail.com',
                'name': 'John Doe'
            }

            response = client.post('/auth/google/callback/', {
                'code': 'fake_auth_code'
            })

            # User should be created or updated
            from accounts.models import CustomUser
            user = CustomUser.objects.get(google_id='google_user_123')
            assert user.email == 'user@gmail.com'

    @patch('accounts.auth.send_sms')
    def test_otp_login_sends_sms(self, mock_send_sms, client):
        """OTP login sends SMS."""
        mock_send_sms.return_value = True

        response = client.post('/auth/otp/send/', {
            'phone': '+998901234567'
        })

        mock_send_sms.assert_called_once()
        assert response.status_code == 200
```

### Mock Celery Tasks

```python
# tests/integration/test_tasks.py
import pytest
from unittest.mock import patch
from catalog.tasks import parse_excel_file

class TestCeleryTasks:
    """Test Celery task execution."""

    @patch('catalog.tasks.parse_excel_to_questions')
    def test_parse_task_success(self, mock_parse, organization):
        """Parse task processes file and creates drafts."""
        from catalog.models import ImportBatch

        mock_parse.return_value = [
            {'title': 'Q1', 'answer': 'A'},
            {'title': 'Q2', 'answer': 'B'},
        ]

        batch = ImportBatch.objects.create(
            organization=organization,
            import_format='excel',
            import_file_url='https://example.com/file.xlsx'
        )

        result = parse_excel_file(str(batch.id), 'https://example.com/file.xlsx')

        assert result['count'] == 2
        batch.refresh_from_db()
        assert batch.status == 'parsed'
```

---

## Performance Tests

```python
# tests/performance/test_question_search.py
import pytest
from django.test.utils import override_settings

@pytest.mark.performance
class TestQuestionSearchPerformance:
    """Test search performance."""

    @override_settings(DEBUG=True)
    def test_search_1000_questions(self, django_db_blocker, client, user):
        """Search across 1000 questions performs well."""
        from django.test import TransactionTestCase
        from django.db import connection
        from catalog.models import Question, QuestionBank

        with django_db_blocker.unblock():
            # Create 1000 questions
            bank = QuestionBank.objects.create(
                organization=user.org,
                name='Large bank'
            )

            questions = [
                Question(
                    organization=user.org,
                    question_bank=bank,
                    content={'uz': f'Question {i}'},
                    answer_type='multiple_choice',
                    answer_choices=[],
                    status='published'
                )
                for i in range(1000)
            ]

            Question.objects.bulk_create(questions, batch_size=100)

            # Test search performance
            client.force_login(user)

            with self.assertNumQueries(2):  # Expect exactly 2 queries
                response = client.get(
                    reverse('catalog:list') + '?search=Question+500'
                )

            assert response.status_code == 200
            assert len(response.context['object_list']) > 0
```

---

## Test Coverage

### Run Coverage Report

```bash
# Run tests with coverage
pytest --cov=. --cov-report=html

# View report
open htmlcov/index.html

# Or terminal report
pytest --cov=. --cov-report=term-missing
```

### Coverage Configuration (.coveragerc)

```ini
[run]
source = .
omit =
    */migrations/*
    */tests/*
    manage.py
    config/wsgi.py

[report]
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
    @abstractmethod
```

---

## TDD: Test-Driven Development

### Workflow

1. **Write failing test** (red)
   ```python
   def test_question_watermark_includes_user_id(self):
       response = self.client.get(f'/question/{q.id}/')
       assert 'user_id_123' in response.content.decode()
   ```

2. **Write minimal code to pass** (green)
   ```python
   def question_detail(request, pk):
       # Add watermark to context
       context['user_id'] = str(request.user.id)
       return render(request, 'question.html', context)
   ```

3. **Refactor** (refactor)
   - Move watermark generation to template filter
   - Extract to reusable component

### Bug Fix TDD

When a bug is reported:

1. Write test that reproduces the bug (fails)
2. Fix the bug (test passes)
3. Ensure other tests still pass

```python
# Bug report: Questions sometimes appear in wrong org
def test_multi_tenant_isolation_bug(self):
    """Regression test for org isolation bug."""
    # Setup org1 and org2
    # Create question in org1
    # Login as user from org2
    # Verify question is NOT visible
```
