import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.mixins import TenantTimestampMixin


class Subject(models.Model):
    """
    Fanlar (masalan: Matematika, Fizika).
    organization=null → platform-global fan; organization=X → B2B private fan.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name=_('Fan nomi'))
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.ImageField(upload_to='subjects/icons/', null=True, blank=True)
    description = models.TextField(blank=True)

    organization = models.ForeignKey(
        'organizations.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subjects',
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Fan')
        verbose_name_plural = _('Fanlar')

    def __str__(self):
        return self.name


class Topic(models.Model):
    """Mavzular ierarxiyasi: Fan → Bo'lim → Mavzu → Sub-topic."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='topics')
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        verbose_name=_('Yuqori mavzu'),
    )
    name = models.CharField(max_length=255, verbose_name=_('Mavzu nomi'))
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('Mavzu')
        verbose_name_plural = _('Mavzular')
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.subject.name} → {self.name}'


class Tag(models.Model):
    """Platform-global teglar. UUID PK — boshqa modellar bilan izchillik."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        verbose_name = _('Teg')
        verbose_name_plural = _('Teglar')

    def __str__(self):
        return self.name


class Question(TenantTimestampMixin):
    """
    Savolning asosiy meta-ma'lumotlari.
    TenantTimestampMixin → organization FK + TenantManager + created_at/updated_at.
    """

    class Type(models.TextChoices):
        SINGLE_CHOICE = 'SC', _('Yagona tanlov (MCQ)')
        MULTIPLE_CHOICE = 'MC', _("Ko'p tanlov")
        MATCHING = 'MT', _('Moslashtirish')
        ORDERING = 'OR', _('Ketma-ketlik')
        FILL_BLANKS = 'FB', _("Bo'shliqni to'ldirish")
        OPEN_ENDED = 'OE', _('Ochiq savol (Essay)')
        FILE_UPLOAD = 'FU', _('Fayl yuklash')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name='questions')
    topic = models.ForeignKey(
        Topic, on_delete=models.SET_NULL, null=True, blank=True, related_name='questions'
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='questions')

    type = models.CharField(max_length=5, choices=Type.choices, default=Type.SINGLE_CHOICE)

    current_version = models.PositiveIntegerField(default=1)

    # IRT parametrlari
    initial_difficulty = models.CharField(
        max_length=20,
        choices=[('easy', _('Oson')), ('medium', _("O'rta")), ('hard', _('Qiyin'))],
        default='medium',
    )
    difficulty_index = models.FloatField(default=0.0, help_text=_('IRT Difficulty Index (b)'))
    discrimination_index = models.FloatField(
        default=1.0, help_text=_('IRT Discrimination Index (a)')
    )

    # Ko'p tilli bog'lash
    language = models.CharField(max_length=10, default='uz')
    parent_question = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='translations',
        help_text=_("Asosiy tildagi savol bilan bog'lash"),
    )

    shuffling_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Savol')
        verbose_name_plural = _('Savollar')

    def __str__(self):
        return f'[{self.type}] {self.id} (v{self.current_version})'


class QuestionVersion(models.Model):
    """Savolning versiyalangan mantiqiy kontenti (JSONB)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='versions')
    version_number = models.PositiveIntegerField()

    # content  = {"text": "...", "latex": "...", "media": [{"type": "image", "url": "..."}]}
    content = models.JSONField(verbose_name=_('Savol matni va media'))

    # SC/MC: [{"id": 1, "text": "...", "is_correct": true}, ...]
    # MT:    {"pairs": [{"left": "...", "right": "..."}]}
    # OR:    [{"id": 1, "text": "...", "correct_order": 2}]
    options = models.JSONField(verbose_name=_('Variantlar va javoblar mantiqi'))

    explanation = models.JSONField(null=True, blank=True, verbose_name=_('Yechim izohi'))
    adaptive_feedback = models.JSONField(
        null=True, blank=True, verbose_name=_('Moslashuvchan qayta aloqa')
    )
    metadata = models.JSONField(
        default=dict, blank=True, help_text=_('Vaqt limiti va boshqa qoidalar')
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Auto-quarantine: agar QuestionDispute > 5% bo'lsa, Celery task True qiladi
    # va barcha UserAnswer.auto_correct=True bo'lib, ball recompute qilinadi.
    is_quarantined = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name=_('Karantinda'),
        help_text=_(
            "True bo'lsa, savol yangi exam'larga qo'shilmaydi va affected attempt'lar bonus oladi."
        ),
    )

    class Meta:
        verbose_name = _('Savol versiyasi')
        verbose_name_plural = _('Savol versiyalari')
        unique_together = [['question', 'version_number']]
        ordering = ['-version_number']

    def __str__(self):
        return f'{self.question.id} v{self.version_number}'


class QuestionBank(TenantTimestampMixin):
    """
    Savol banki — bir yoki ko'p savolni birlashtiradi.
    is_public=True → platforma miqyosida ko'rinadi (global_objects orqali).
    is_public=False → faqat o'z tashkilotiga ko'rinadi (TenantManager orqali).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, verbose_name=_('Bank nomi'))
    slug = models.SlugField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    is_public = models.BooleanField(
        default=False,
        help_text=_("Public bank: platform admini tomonidan barcha tenantlarga ko'rinadi"),
    )
    questions = models.ManyToManyField(Question, blank=True, related_name='banks')

    class Meta:
        verbose_name = _('Savol banki')
        verbose_name_plural = _('Savol banklari')

    def __str__(self):
        return self.name


# ─── AI Provider Configuration ──────────────────────────────────────────────


class AIProviderConfig(models.Model):
    """
    Admin paneldan boshqariladigan AI provider konfiguratsiyasi.
    Bir nechta provider qo'shish mumkin — priority bo'yicha fallback ishlaydi.
    """

    class Provider(models.TextChoices):
        CLAUDE = 'claude', 'Anthropic Claude'
        OPENAI = 'openai', 'OpenAI GPT'
        GEMINI = 'gemini', 'Google Gemini'
        OLLAMA = 'ollama', 'Ollama (Mahalliy / Bepul)'

    class UseFor(models.TextChoices):
        PARSER = 'parser', 'Savol Parseri (Bulk Import)'
        TUTOR = 'tutor', 'AI Tutor (Diagnostika)'
        ALL = 'all', 'Barchasi'

    name = models.CharField(
        max_length=100,
        verbose_name=_('Nom'),
        help_text=_("Masalan: 'Asosiy Claude', 'Zaxira Ollama'"),
    )
    provider = models.CharField(
        max_length=20, choices=Provider.choices, verbose_name=_('Provayder')
    )
    model = models.CharField(
        max_length=100,
        verbose_name=_('Model nomi'),
        help_text=_('claude-sonnet-4-6 | gpt-4o | gemini-1.5-pro | llama3'),
    )
    api_key = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('API kalit'),
        help_text=_("Ollama uchun bo'sh qoldiring"),
    )
    base_url = models.CharField(
        max_length=500,
        blank=True,
        verbose_name=_('Base URL'),
        help_text=_('Faqat Ollama uchun: http://localhost:11434'),
    )

    use_for = models.CharField(
        max_length=20,
        choices=UseFor.choices,
        default=UseFor.ALL,
        verbose_name=_('Foydalanish maqsadi'),
    )
    is_active = models.BooleanField(default=True, verbose_name=_('Faol'))
    priority = models.PositiveSmallIntegerField(
        default=1,
        verbose_name=_('Ustuvorlik'),
        help_text=_("Kichik son = yuqori ustuvorlik. Xato bo'lsa keyingisi urinadi."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('AI Provider konfiguratsiyasi')
        verbose_name_plural = _('AI Provider konfiguratsiyalari')
        ordering = ['priority']

    def __str__(self):
        status = '✓' if self.is_active else '✗'
        return (
            f'{status} [{self.priority}] {self.name} ({self.get_provider_display()}: {self.model})'
        )


# ─── Bulk Import & Draft System ─────────────────────────────────────────────


class ImportBatch(TenantTimestampMixin):
    """Ommaviy yuklash seansi."""

    class Status(models.TextChoices):
        PENDING = 'pending', _('Kutilmoqda')
        PROCESSING = 'processing', _('Tahlil qilinmoqda')
        COMPLETED = 'completed', _('Yakunlandi')
        FAILED = 'failed', _('Xatolik')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.FileField(upload_to='imports/%Y/%m/%d/', blank=True, verbose_name=_('Asl fayl'))
    file_type = models.CharField(max_length=10, help_text='docx, xlsx, etc.')

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    total_questions = models.PositiveIntegerField(default=0)
    processed_questions = models.PositiveIntegerField(default=0)
    error_log = models.JSONField(null=True, blank=True, verbose_name=_('Xatoliklar jurnali'))

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        verbose_name = _('Import paketi')
        verbose_name_plural = _('Import paketlari')
        ordering = ['-created_at']


class QuestionDraft(models.Model):
    """AI/Parser dan olingan xomaki savol — foydalanuvchi tasdiqlaguncha."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name='drafts')

    data = models.JSONField(help_text=_("AI yoki Parserdan olingan xomaki ma'lumot"))
    is_valid = models.BooleanField(default=True)
    validation_errors = models.JSONField(null=True, blank=True)

    type = models.CharField(max_length=5, choices=Question.Type.choices, null=True, blank=True)
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True, blank=True)
    topic = models.ForeignKey(Topic, on_delete=models.SET_NULL, null=True, blank=True)

    is_published = models.BooleanField(default=False)
    published_question = models.ForeignKey(
        Question, on_delete=models.SET_NULL, null=True, blank=True, related_name='draft_source'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Qoralama savol')
        verbose_name_plural = _('Qoralama savollar')

    def __str__(self):
        return f'Draft {self.id} ({self.batch_id})'
