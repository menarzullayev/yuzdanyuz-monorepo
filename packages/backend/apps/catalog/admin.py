from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    AIProviderConfig,
    ImportBatch,
    Question,
    QuestionBank,
    QuestionDraft,
    QuestionVersion,
    Subject,
    Tag,
    Topic,
)


class AIProviderConfigForm(forms.ModelForm):
    """The stored key is never rendered (neither plaintext nor ciphertext).

    A blank field keeps the current key; 'clear' removes it.
    """

    api_key = forms.CharField(
        required=False,
        strip=True,
        label='API kalit',
        widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'new-password'}),
        help_text="Yangi kalit kiriting. Bo'sh qoldirilsa, joriy kalit saqlanadi.",
    )
    clear_api_key = forms.BooleanField(required=False, label="Kalitni o'chirish")

    class Meta:
        model = AIProviderConfig
        fields = (
            'name',
            'provider',
            'model',
            'use_for',
            'priority',
            'is_active',
            'api_key',
            'base_url',
            'extra_options',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['api_key'] = ''

    def clean(self):
        data = super().clean()
        if data.get('clear_api_key'):
            data['api_key'] = ''
        elif not data.get('api_key'):
            data['api_key'] = self.instance.api_key if self.instance.pk else ''
        return data


@admin.register(AIProviderConfig)
class AIProviderConfigAdmin(admin.ModelAdmin):
    form = AIProviderConfigForm
    list_display = (
        'priority',
        'name',
        'provider_badge',
        'model',
        'use_for',
        'status_badge',
        'updated_at',
    )
    list_display_links = ('name',)
    list_editable = ('priority',)
    list_filter = ('provider', 'use_for', 'is_active')
    ordering = ('priority',)
    readonly_fields = ('api_key_masked', 'created_at', 'updated_at')

    fieldsets = (
        (
            'Asosiy',
            {
                'fields': ('name', 'provider', 'model', 'use_for', 'priority', 'is_active'),
            },
        ),
        (
            'Ulanish',
            {
                'fields': (
                    'api_key_masked',
                    'api_key',
                    'clear_api_key',
                    'base_url',
                    'extra_options',
                ),
                'description': (
                    'API kalit: Claude → console.anthropic.com | OpenAI → platform.openai.com | '
                    'Gemini → aistudio.google.com.<br>'
                    'Ollama uchun API kalit kerak emas, faqat Base URL (http://localhost:11434).'
                ),
            },
        ),
        (
            'Meta',
            {
                'fields': ('created_at', 'updated_at'),
                'classes': ('collapse',),
            },
        ),
    )

    @admin.display(description='Joriy kalit')
    def api_key_masked(self, obj):
        return f'••••{obj.api_key_hint}' if obj and obj.api_key else '—'

    @admin.display(description='Provayder')
    def provider_badge(self, obj):
        colors = {
            'claude': '#b45309',
            'openai': '#16a34a',
            'gemini': '#1d4ed8',
            'ollama': '#7c3aed',
        }
        color = colors.get(obj.provider, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px">{}</span>',
            color,
            obj.get_provider_display(),
        )

    @admin.display(description='Holat')
    def status_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color:#16a34a;font-weight:bold">● Faol</span>')
        return format_html('<span style="color:#dc2626">● O\'chiq</span>')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'organization', 'is_active')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'slug')


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'parent', 'order')
    list_filter = ('subject',)
    search_fields = ('name',)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name',)


class QuestionVersionInline(admin.TabularInline):
    model = QuestionVersion
    extra = 0
    fields = ('version_number', 'content', 'options', 'created_by', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'type',
        'subject',
        'language',
        'initial_difficulty',
        'is_active',
        'created_at',
    )
    list_filter = ('type', 'language', 'initial_difficulty', 'is_active')
    search_fields = ('id',)
    inlines = [QuestionVersionInline]


@admin.register(QuestionBank)
class QuestionBankAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'organization', 'is_public', 'created_at')
    list_filter = ('is_public',)
    search_fields = ('name', 'slug')
    filter_horizontal = ('questions',)


class QuestionDraftInline(admin.TabularInline):
    model = QuestionDraft
    extra = 0
    fields = ('is_valid', 'is_published', 'type', 'subject')
    readonly_fields = ('is_valid', 'is_published')


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'organization', 'file_type', 'status', 'total_questions', 'created_at')
    list_filter = ('status', 'file_type')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [QuestionDraftInline]
