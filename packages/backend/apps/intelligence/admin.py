from django.contrib import admin

from .models import AIFeedback, OpenEndedSubmission, SkillTag, UserSkillProfile


@admin.register(SkillTag)
class SkillTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'subject', 'parent', 'created_at')
    list_filter = ('subject',)
    search_fields = ('name', 'slug')
    raw_id_fields = ('parent', 'subject')
    filter_horizontal = ('questions',)


@admin.register(UserSkillProfile)
class UserSkillProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'skill', 'mastery', 'confidence', 'alpha', 'beta')
    list_filter = ('skill__subject',)
    search_fields = ('user__email', 'user__username', 'skill__name')
    raw_id_fields = ('user', 'skill')
    readonly_fields = ('mastery', 'confidence', 'last_updated_at', 'created_at')


@admin.register(AIFeedback)
class AIFeedbackAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'requested_at', 'completed_at')
    list_filter = ('status',)
    raw_id_fields = ('user',)
    readonly_fields = ('summary', 'content', 'error', 'prompt_hash', 'requested_at', 'completed_at')


@admin.register(OpenEndedSubmission)
class OpenEndedSubmissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'submission_type', 'status', 'ai_score', 'human_score', 'submitted_at')
    list_filter = ('submission_type', 'status')
    raw_id_fields = ('user', 'question_version', 'human_reviewer')
    readonly_fields = ('ai_feedback', 'submitted_at', 'ai_completed_at', 'human_completed_at')
