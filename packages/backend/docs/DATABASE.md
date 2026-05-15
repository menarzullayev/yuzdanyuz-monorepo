# Milliy Sertifikat — Database Schema & Models

## Overview

- **Engine**: PostgreSQL 14+
- **ORM**: Django ORM
- **Connection**: Unix socket (`/home/hsm/.local/pgsql/run:5992`)
- **Isolation**: Row-level security (RLS) policies for multi-tenant protection
- **Serialization**: JSONB for flexible schemas (tags, metadata, permissions)

---

## Core Multi-Tenant Model Inheritance

All tenant-aware models inherit from `TenantTimestampMixin`:

```python
# core/mixins.py
from django.db import models
from django.utils import timezone
import uuid

class TenantTimestampMixin(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.CASCADE,
        null=True,  # None for global records (admin only)
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()          # Auto-filters by org
    global_objects = GlobalManager()   # Bypass filter (admin only)

    class Meta:
        abstract = True

    def get_org(self):
        """Get organization for this record."""
        from core.tenant import get_current_org
        return self.organization or get_current_org()
```

---

## Models by Domain

### 1. Accounts App (`accounts/models.py`)

```python
class CustomUser(AbstractUser):
    """
    Multi-auth user: phone | email | telegram | google
    One user can be in multiple organizations.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Auth methods
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    google_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    # Profile
    avatar_url = models.URLField(blank=True)
    bio = models.TextField(blank=True)
    date_of_birth = models.DateField(null=True, blank=True)

    # Location
    region = models.ForeignKey('Region', on_delete=models.SET_NULL, null=True, blank=True)
    district = models.ForeignKey('District', on_delete=models.SET_NULL, null=True, blank=True)

    # Academic
    school_name = models.CharField(max_length=255, blank=True)
    graduation_year = models.IntegerField(null=True, blank=True)
    exam_language = models.CharField(
        max_length=20,
        choices=[('uz', 'O\'zbek'), ('ru', 'Rus'), ('en', 'English')],
        default='uz'
    )

    # User metadata
    user_metadata = models.JSONField(default=dict)  # Custom fields

    # Flags
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    last_login_at = models.DateTimeField(null=True, blank=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_customuser'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.username} ({self.id})"

    @property
    def user_type(self):
        """Derived from membership, not stored."""
        if self.is_superuser:
            return 'platform_admin'
        if self.is_staff:
            return 'platform_staff'
        from core.tenant import get_current_org
        org = get_current_org()
        membership = self.membership_set.filter(organization=org).first()
        return membership.role.name if membership else 'b2c'


class Region(models.Model):
    """Viloyat (region) — Tashkent, Samarkand, etc."""
    id = models.CharField(max_length=2, primary_key=True)  # 'TK', 'SM', etc.
    name_uz = models.CharField(max_length=100)
    name_ru = models.CharField(max_length=100)

    class Meta:
        db_table = 'accounts_region'
        verbose_name = 'Region'

    def __str__(self):
        return self.name_uz


class District(models.Model):
    """Tuman (district) — within a region."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    region = models.ForeignKey(Region, on_delete=models.CASCADE)
    name_uz = models.CharField(max_length=100)
    name_ru = models.CharField(max_length=100)

    class Meta:
        db_table = 'accounts_district'
        unique_together = ('region', 'name_uz')

    def __str__(self):
        return f"{self.region.name_uz} — {self.name_uz}"
```

### 2. Organizations App (`organizations/models.py`)

```python
class Organization(TenantTimestampMixin):
    """
    Tenant root. Can be:
    - B2C (user's personal org) — single_user=True
    - B2B (study center, school) — single_user=False
    - White-label (enterprise) — white_label=True
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)

    # Type & Status
    ORG_TYPE_CHOICES = [
        ('b2c', 'B2C — Individual user'),
        ('b2b', 'B2B — Study center / School'),
        ('enterprise', 'Enterprise — White-label'),
    ]
    org_type = models.CharField(max_length=20, choices=ORG_TYPE_CHOICES, default='b2c')

    STATUS_CHOICES = [
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('archived', 'Archived'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    # Pricing Tier
    TIER_CHOICES = [
        ('free', 'Free'),
        ('starter', 'Starter'),
        ('pro', 'Pro'),
        ('enterprise', 'Enterprise'),
    ]
    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='free')

    # Flags
    single_user = models.BooleanField(default=False)  # B2C flag
    white_label = models.BooleanField(default=False)   # Enterprise flag

    # Owner
    owner = models.ForeignKey(CustomUser, on_delete=models.PROTECT, related_name='owned_orgs')

    # Settings
    settings = models.JSONField(default=dict)  # Theme, logo URL, branding colors, etc.

    # Metadata
    description = models.TextField(blank=True)
    website = models.URLField(blank=True)
    logo_url = models.URLField(blank=True)

    class Meta:
        db_table = 'organizations_organization'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.org_type})"


class OrgRole(TenantTimestampMixin):
    """
    Role within an organization. Each org has default roles.
    Permissions are stored as JSON list: ['exams.create', 'questions.edit', '*']
    """
    name = models.CharField(max_length=100)  # 'admin', 'teacher', 'student'
    description = models.TextField(blank=True)

    # Permissions as JSON: ["exams.create", "questions.edit", "*"]
    permissions = models.JSONField(default=list)  # List of permission strings

    # Flags
    is_system_role = models.BooleanField(default=False)  # Can't be deleted

    class Meta:
        db_table = 'organizations_orgrole'
        unique_together = ('organization', 'name')
        ordering = ['name']

    def has_permission(self, perm):
        """Check if role has a permission."""
        if '*' in self.permissions:
            return True
        return perm in self.permissions

    def __str__(self):
        return f"{self.organization.name} — {self.name}"


class Membership(TenantTimestampMixin):
    """
    User's membership in an organization.
    One user can have multiple memberships across different orgs.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='membership_set')
    role = models.ForeignKey(OrgRole, on_delete=models.PROTECT)

    # Status
    STATUS_CHOICES = [
        ('pending', 'Pending (awaiting acceptance)'),
        ('active', 'Active'),
        ('suspended', 'Suspended'),
        ('left', 'Left'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    # Invite
    invited_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+'
    )
    invite_accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'organizations_membership'
        unique_together = ('user', 'organization')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} @ {self.organization.name} ({self.role.name})"


class OrgInvite(TenantTimestampMixin):
    """
    Pending invite to join an organization.
    Sent via email or phone.
    """
    email_or_phone = models.CharField(max_length=255)  # Either "user@email.com" or "+998901234567"
    role = models.ForeignKey(OrgRole, on_delete=models.PROTECT)

    # Status
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('expired', 'Expired'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    invite_token = models.CharField(max_length=255, unique=True)
    invite_accepted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    invited_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='+')

    class Meta:
        db_table = 'organizations_orginvite'
        ordering = ['-created_at']

    def __str__(self):
        return f"Invite {self.email_or_phone} → {self.organization.name}"
```

### 3. Catalog App (`catalog/models.py`)

```python
class QuestionBank(TenantTimestampMixin):
    """
    Collection of questions. Can be global (public) or private (B2B).
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Visibility
    is_public = models.BooleanField(default=False)  # Indexed for search
    subject = models.CharField(max_length=100, db_index=True)  # e.g., 'Matematika'

    # Ownership
    owner = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)

    # Metadata
    question_count = models.IntegerField(default=0)  # Denormalized for perf
    total_views = models.IntegerField(default=0)

    class Meta:
        db_table = 'catalog_questionbank'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class Question(TenantTimestampMixin):
    """
    A test question with multiple answer formats.
    JSONB structure allows polymorphic answer formats.
    """
    question_bank = models.ForeignKey(QuestionBank, on_delete=models.CASCADE, related_name='questions')

    # Content — JSONB for multi-language support
    content = models.JSONField()  # {'uz': 'Savolning mazmuni', 'ru': 'Текст вопроса', 'en': 'Question text'}

    # Answer structure
    ANSWER_TYPE_CHOICES = [
        ('multiple_choice', 'A/B/C/D'),
        ('true_false', 'True/False'),
        ('matching', 'Matching pairs'),
        ('ordering', 'Sequence ordering'),
        ('fill_blank', 'Fill in blank'),
        ('essay', 'Essay / Short answer'),
    ]
    answer_type = models.CharField(max_length=50, choices=ANSWER_TYPE_CHOICES)

    # Answers — JSONB structure varies by answer_type
    answer_choices = models.JSONField(default=list)  # [{'label': 'A', 'text': {...}, 'is_correct': True}, ...]

    # Correctness
    correct_answer = models.JSONField(null=True, blank=True)  # Depends on answer_type
    explanation = models.JSONField(default=dict)  # Multi-language explanations

    # Metadata
    difficulty = models.IntegerField(choices=[(1, 'Easy'), (2, 'Medium'), (3, 'Hard')], default=1)
    time_limit_seconds = models.IntegerField(default=60)

    # Tagging
    tags = models.ManyToManyField('Tag', related_name='questions', blank=True)

    # Status
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('review', 'Under review'),
        ('published', 'Published'),
        ('archived', 'Archived'),
        ('disabled', 'Disabled (content issue)'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)

    # Flags
    is_deleted = models.BooleanField(default=False)  # Soft delete for audit trail
    flagged_for_review = models.BooleanField(default=False)  # User-reported issue

    # Licensing (for white-label)
    license_type = models.CharField(
        max_length=50,
        choices=[('cc0', 'Public Domain'), ('cc_by', 'CC-BY'), ('proprietary', 'Proprietary')],
        default='proprietary'
    )

    class Meta:
        db_table = 'catalog_question'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', 'status']),
            models.Index(fields=['question_bank', 'status']),
        ]

    def __str__(self):
        return f"Q#{self.id} ({self.status})"


class Tag(TenantTimestampMixin):
    """
    Hierarchical tagging: subject → chapter → subtopic → micro-skill
    Examples:
    - Matematika/Algebra/Tengsizliklar
    - Fizika/Mexanika/Kuchlар
    """
    name = models.CharField(max_length=100)

    # Hierarchy
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='children'
    )

    # Type
    TAG_TYPE_CHOICES = [
        ('subject', 'Subject'),
        ('chapter', 'Chapter'),
        ('subtopic', 'Subtopic'),
        ('skill', 'Micro-skill'),
    ]
    tag_type = models.CharField(max_length=50, choices=TAG_TYPE_CHOICES, default='subject')

    # Metadata
    description = models.TextField(blank=True)
    icon_url = models.URLField(blank=True)

    class Meta:
        db_table = 'catalog_tag'
        unique_together = ('organization', 'name', 'parent')
        ordering = ['tag_type', 'name']

    def __str__(self):
        return self.name


class ImportBatch(TenantTimestampMixin):
    """
    Bulk import session. Questions start as drafts, reviewed, then published.
    """
    import_file_url = models.URLField()  # S3 or local file URL
    import_format = models.CharField(
        max_length=50,
        choices=[('excel', 'Excel'), ('csv', 'CSV'), ('pdf', 'PDF'), ('docx', 'DOCX')],
        default='excel'
    )

    # Status
    STATUS_CHOICES = [
        ('pending', 'Pending (not started)'),
        ('parsing', 'Parsing (in progress)'),
        ('parsed', 'Parsed (awaiting review)'),
        ('review', 'In review'),
        ('publishing', 'Publishing'),
        ('published', 'Published'),
        ('failed', 'Failed'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)

    # Parsing
    parsed_by_ai = models.BooleanField(default=False)  # True if AI parsed; False if manual upload
    ai_confidence = models.FloatField(null=True, blank=True)  # 0.0 - 1.0

    # Counts
    total_imported = models.IntegerField(default=0)
    total_published = models.IntegerField(default=0)
    total_failed = models.IntegerField(default=0)

    # Error handling
    error_log = models.JSONField(default=list)  # List of error messages

    # Metadata
    imported_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='+')
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'catalog_importbatch'
        ordering = ['-created_at']

    def __str__(self):
        return f"Batch #{self.id} ({self.status})"


class QuestionDraft(TenantTimestampMixin):
    """
    Temporary draft created during import before publishing.
    Links to ImportBatch for tracking.
    """
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='drafts')
    question = models.OneToOneField(
        Question,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='draft'
    )

    # Draft content
    raw_content = models.JSONField()  # Original parsed content

    # Validation
    VALIDATION_STATUS_CHOICES = [
        ('pending', 'Not validated'),
        ('valid', 'Valid'),
        ('invalid', 'Invalid (see errors)'),
        ('warning', 'Valid with warnings'),
    ]
    validation_status = models.CharField(max_length=20, choices=VALIDATION_STATUS_CHOICES, default='pending')
    validation_errors = models.JSONField(default=list)  # List of error strings

    # Review
    reviewed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_drafts'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    # Approval
    APPROVAL_CHOICES = [
        ('pending', 'Awaiting approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    approval = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default='pending')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'catalog_questiondraft'
        ordering = ['-created_at']

    def __str__(self):
        return f"Draft #{self.id}"
```

### 4. Exams App (`exams/models.py`)

```python
class MockExam(TenantTimestampMixin):
    """
    Static exam with fixed questions. Used for fair ranking.
    One exam per week, everyone takes same questions.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Questions
    questions = models.ManyToManyField(Question, through='ExamQuestion', related_name='mock_exams')

    # Timing
    duration_minutes = models.IntegerField(default=60)
    scheduled_at = models.DateTimeField()  # When exam is available
    closed_at = models.DateTimeField(null=True, blank=True)  # When exam closes

    # Flags
    is_active = models.BooleanField(default=True)

    # Metadata
    max_attempts = models.IntegerField(default=1)  # Usually 1 per week
    passing_score = models.IntegerField(default=60)  # Percentage

    class Meta:
        db_table = 'exams_mockexam'
        ordering = ['-scheduled_at']

    def __str__(self):
        return f"{self.name} ({self.scheduled_at})"


class ExamQuestion(TenantTimestampMixin):
    """
    Junction table: Question ordering within Exam.
    """
    exam = models.ForeignKey(MockExam, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    order = models.IntegerField(default=0)  # Question sequence

    class Meta:
        db_table = 'exams_examquestion'
        unique_together = ('exam', 'question')
        ordering = ['order']


class PracticeSession(TenantTimestampMixin):
    """
    Dynamic practice set. User selects tags → questions randomized from DB.
    Unlimited attempts.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='practice_sessions')

    # Configuration
    selected_tags = models.JSONField(default=list)  # Tag IDs selected by user
    difficulty_level = models.IntegerField(choices=[(1, 'Easy'), (2, 'Medium'), (3, 'Hard')], default=2)
    num_questions = models.IntegerField(default=10)

    # Randomization seed (for reproducibility)
    seed = models.CharField(max_length=255, unique=True)

    # Timing
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)

    # Scoring
    total_score = models.IntegerField(default=0)
    max_score = models.IntegerField(default=0)
    percentage = models.FloatField(default=0.0)

    # Status
    STATUS_CHOICES = [
        ('in_progress', 'In progress'),
        ('completed', 'Completed'),
        ('abandoned', 'Abandoned'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')

    class Meta:
        db_table = 'exams_practicesession'
        ordering = ['-started_at']

    def __str__(self):
        return f"Practice #{self.id} ({self.percentage}%)"


class ExamAttempt(TenantTimestampMixin):
    """
    User's attempt to take a MockExam.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='exam_attempts')
    exam = models.ForeignKey(MockExam, on_delete=models.CASCADE, related_name='attempts')

    # Timing
    started_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.IntegerField(null=True, blank=True)

    # Scoring
    total_score = models.IntegerField(default=0)
    max_score = models.IntegerField(default=0)
    percentage = models.FloatField(default=0.0)
    passed = models.BooleanField(default=False)

    # Flags
    is_cheating_flagged = models.BooleanField(default=False)
    cheating_reason = models.TextField(blank=True)

    # IP tracking (anti-cheat)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    device_fingerprint = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'exams_examattempt'
        unique_together = ('user', 'exam')  # One attempt per user per exam
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.user.username} @ {self.exam.name} ({self.percentage}%)"


class UserAnswer(TenantTimestampMixin):
    """
    User's answer to a specific question within an attempt.
    """
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='user_answers')

    # Answer
    user_answer = models.JSONField()  # Format depends on answer_type
    is_correct = models.BooleanField(default=False)
    points_earned = models.IntegerField(default=0)

    # Timing
    time_spent_seconds = models.IntegerField(default=0)
    answered_at = models.DateTimeField()

    class Meta:
        db_table = 'exams_useranswer'
        unique_together = ('attempt', 'question')
        ordering = ['answered_at']

    def __str__(self):
        return f"{self.attempt.user.username} → Q#{self.question.id}"
```

### 5. Intelligence App (`intelligence/models.py`)

```python
class SkillTag(TenantTimestampMixin):
    """
    Micro-skill: smallest learnable unit.
    Example: "Linear equation solving", "Present perfect tense"
    Each Question can link to multiple skills.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Categorization
    subject = models.CharField(max_length=100)  # "Matematika", "English"
    chapter = models.CharField(max_length=100, blank=True)

    # Learning curve (for adaptive difficulty)
    average_mastery = models.FloatField(default=0.0)  # Global average across all users

    class Meta:
        db_table = 'intelligence_skilltag'
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name


class UserSkillProfile(TenantTimestampMixin):
    """
    User's proficiency in each skill.
    Calculated after each practice/exam.
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='skill_profile')

    # Skill scores (denormalized from user_skill_scores)
    skill_scores = models.JSONField(default=dict)  # {skill_id: mastery_score (0.0-100.0)}

    # Profile metadata
    last_updated = models.DateTimeField(auto_now=True)
    total_questions_answered = models.IntegerField(default=0)
    overall_mastery = models.FloatField(default=0.0)  # Average across all skills

    class Meta:
        db_table = 'intelligence_userskillprofile'

    def __str__(self):
        return f"{self.user.username}'s skill profile"


class StudyPlan(TenantTimestampMixin):
    """
    Personalized study recommendations based on weak skills.
    Generated by knowledge graph engine.
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='study_plan')

    # Weak skills (skill_id → mastery_score)
    weak_skills = models.JSONField(default=list)  # [{'skill_id': '...', 'mastery': 20.0, 'priority': 1}, ...]

    # Recommended questions
    recommended_questions = models.JSONField(default=list)  # [question_id, ...]

    # Metadata
    generated_at = models.DateTimeField(auto_now=True)
    estimated_hours = models.FloatField(default=0.0)  # Time to improve weak areas

    class Meta:
        db_table = 'intelligence_studyplan'

    def __str__(self):
        return f"Study plan for {self.user.username}"


class AIFeedback(TenantTimestampMixin):
    """
    AI-generated feedback and motivation.
    Created asynchronously by Celery.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='ai_feedbacks')
    practice_session = models.ForeignKey(PracticeSession, on_delete=models.CASCADE, null=True, related_name='ai_feedbacks')

    # Feedback
    feedback_text = models.TextField()  # AI-generated motivation
    improvement_areas = models.JSONField(default=list)  # Weak skill names

    # Tone
    tone = models.CharField(
        max_length=20,
        choices=[('encouraging', 'Encouraging'), ('neutral', 'Neutral'), ('challenging', 'Challenging')],
        default='encouraging'
    )

    class Meta:
        db_table = 'intelligence_aifeedback'
        ordering = ['-created_at']
```

### 6. Commerce App (`commerce/models.py`)

```python
class Wallet(TenantTimestampMixin):
    """
    User's account balance (Sertifikat Coins).
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='wallet')

    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # Coins
    currency = models.CharField(max_length=3, default='CRT')  # "CRT" for Sertifikat

    # Metadata
    total_earned = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        db_table = 'commerce_wallet'

    def __str__(self):
        return f"{self.user.username}'s wallet ({self.balance} {self.currency})"


class WalletTransaction(TenantTimestampMixin):
    """
    Audit trail for all wallet changes.
    """
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')

    # Transaction
    TRANSACTION_TYPE_CHOICES = [
        ('earned', 'Earned (practice, quiz, daily streak)'),
        ('spent', 'Spent (premium features)'),
        ('refund', 'Refund'),
        ('bonus', 'Bonus (promotion)'),
        ('withdrawal', 'Withdrawal (cash out)'),
    ]
    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPE_CHOICES)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.TextField()

    # Reference
    related_model = models.CharField(max_length=100, blank=True)  # 'exams.PracticeSession', etc.
    related_id = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'commerce_wallettransaction'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.transaction_type}: {self.amount} {self.wallet.currency}"


class Subscription(TenantTimestampMixin):
    """
    B2B organization subscription (per-seat billing).
    """
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='subscription')

    # Plan
    PLAN_CHOICES = [
        ('free', 'Free'),
        ('starter', 'Starter (5 seats)'),
        ('pro', 'Pro (50 seats)'),
        ('enterprise', 'Enterprise (unlimited)'),
    ]
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='free')

    # Billing
    monthly_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_cycle = models.CharField(
        max_length=20,
        choices=[('monthly', 'Monthly'), ('yearly', 'Yearly'), ('once', 'One-time')],
        default='monthly'
    )

    # Renewal
    next_billing_date = models.DateField(null=True, blank=True)
    auto_renew = models.BooleanField(default=True)

    # Status
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('past_due', 'Past due'),
        ('suspended', 'Suspended'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')

    class Meta:
        db_table = 'commerce_subscription'

    def __str__(self):
        return f"{self.organization.name} ({self.plan})"


class Affiliate(TenantTimestampMixin):
    """
    Referral program tracking.
    User can earn coins by referring others.
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='affiliate')

    # Referral code
    referral_code = models.CharField(max_length=20, unique=True)
    referral_url = models.URLField(null=True, blank=True)

    # Stats
    total_referrals = models.IntegerField(default=0)
    total_earned = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Withdrawal
    is_withdrawable = models.BooleanField(default=False)  # True if 50+ referrals
    withdrawal_requested = models.BooleanField(default=False)
    withdrawal_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'commerce_affiliate'

    def __str__(self):
        return f"Affiliate {self.referral_code}"
```

### 7. Engagement App (`engagement/models.py`)

```python
class Streak(TenantTimestampMixin):
    """
    Daily activity streak for gamification.
    """
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='streak')

    # Counts
    current_streak = models.IntegerField(default=0)  # Consecutive days
    longest_streak = models.IntegerField(default=0)
    total_days_active = models.IntegerField(default=0)

    # Last activity
    last_active_date = models.DateField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'engagement_streak'

    def __str__(self):
        return f"{self.user.username} ({self.current_streak} day streak)"


class League(TenantTimestampMixin):
    """
    Weekly leaderboard league (Bronze, Silver, Gold, Diamond).
    Users move up/down based on score.
    """
    name = models.CharField(
        max_length=50,
        choices=[('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('diamond', 'Diamond')]
    )

    # Week
    week_start = models.DateField(db_index=True)
    week_end = models.DateField()

    # Rankings (denormalized for speed)
    rankings = models.JSONField(default=list)  # [{'user_id': '...', 'score': 1000, 'rank': 1}, ...]

    class Meta:
        db_table = 'engagement_league'
        unique_together = ('organization', 'name', 'week_start')
        ordering = ['week_start', 'name']

    def __str__(self):
        return f"{self.name.title()} ({self.week_start})"


class Badge(TenantTimestampMixin):
    """
    Achievement badge earned by users.
    """
    name = models.CharField(max_length=100)
    description = models.TextField()
    icon_url = models.URLField()

    # Criteria
    criteria_json = models.JSONField(default=dict)  # {type: 'streak_days', value: 7}

    class Meta:
        db_table = 'engagement_badge'
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name


class UserBadge(TenantTimestampMixin):
    """
    User's earned badge.
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='badges')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE)

    earned_at = models.DateTimeField()

    class Meta:
        db_table = 'engagement_userbadge'
        unique_together = ('user', 'badge')
        ordering = ['-earned_at']


class Notification(TenantTimestampMixin):
    """
    User notification (in-app, email, SMS, Telegram).
    """
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='notifications')

    # Content
    title = models.CharField(max_length=255)
    message = models.TextField()

    # Type
    NOTIFICATION_TYPE_CHOICES = [
        ('streak_broken', 'Streak broken reminder'),
        ('league_promotion', 'League promotion'),
        ('new_exam', 'New mock exam available'),
        ('ai_feedback', 'AI feedback ready'),
        ('wallet', 'Wallet update'),
        ('system', 'System announcement'),
    ]
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPE_CHOICES)

    # Delivery
    DELIVERY_METHOD_CHOICES = [
        ('telegram', 'Telegram bot'),
        ('push', 'Push notification'),
        ('sms', 'SMS'),
        ('email', 'Email'),
    ]
    delivery_method = models.CharField(max_length=50, choices=DELIVERY_METHOD_CHOICES, default='telegram')

    # Status
    is_read = models.BooleanField(default=False)
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)

    # Link
    action_url = models.URLField(blank=True)

    class Meta:
        db_table = 'engagement_notification'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['user', '-created_at']),
        ]
```

---

## PostgreSQL Row-Level Security (RLS)

For multi-tenant isolation at DB level:

```sql
-- Enable RLS on all tenant-aware tables
ALTER TABLE catalog_question ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_questionbank ENABLE ROW LEVEL SECURITY;
ALTER TABLE exams_mockexam ENABLE ROW LEVEL SECURITY;
-- ... etc for all tenant tables

-- Policy: Users can only see records from their organization
CREATE POLICY org_isolation ON catalog_question
  AS PERMISSIVE FOR SELECT
  USING (organization_id = current_setting('app.current_org_id')::uuid)
  WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid);

-- Set current org on each request (in Django middleware)
-- Connection: SET app.current_org_id = '12345-org-uuid';
```

---

## Indexes & Performance

```python
# Recommended indexes (beyond primary key)
class Meta:
    indexes = [
        # Multi-tenant queries
        models.Index(fields=['organization', 'status']),
        models.Index(fields=['organization', '-created_at']),

        # Status filtering
        models.Index(fields=['status']),
        models.Index(fields=['status', '-created_at']),

        # User lookups
        models.Index(fields=['user', '-created_at']),

        # Timestamps
        models.Index(fields=['-created_at']),
        models.Index(fields=['-updated_at']),
    ]
```

---

## JSONB Usage Patterns

**Flexible metadata**:
```python
# Question content (multi-language)
question.content = {
    'uz': 'Savolning mazmuni',
    'ru': 'Текст вопроса',
    'en': 'Question text'
}

# Answer choices (polymorphic)
question.answer_choices = [
    {'label': 'A', 'text': {'uz': 'Javob A', 'ru': 'Ответ А'}, 'is_correct': True},
    {'label': 'B', 'text': {'uz': 'Javob B', 'ru': 'Ответ B'}, 'is_correct': False},
]

# Organization settings
organization.settings = {
    'theme_color': '#FF6B35',
    'logo_url': 'https://...',
    'custom_css': '...',
    'branding': {'name': 'My School', 'domain': 'school.local'}
}

# User answer (varies by question type)
user_answer.user_answer = {
    'answer_type': 'multiple_choice',
    'selected': 'A',  # or ['A', 'B'] for matching
}
```

---

## Connection Pool Strategy

```python
# settings/prod.py
DATABASES = {
    'default': {
        ...
        'CONN_MAX_AGE': 600,  # 10 minutes
        'OPTIONS': {
            'connect_timeout': 10,
        },
        'ATOMIC_REQUESTS': False,  # Use @transaction.atomic on views instead
    }
}
```
