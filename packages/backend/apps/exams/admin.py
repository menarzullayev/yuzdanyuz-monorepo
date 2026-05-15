"""
Exams admin — superuser/staff sees all (auto-unscoped via TenantMiddleware).
list_display fields are kept tight to avoid N+1 in changelist.
"""

from django.contrib import admin

from .models import (
    AntiCheatEvent,
    ExamAttempt,
    MockExam,
    MockExamQuestion,
    PracticeSession,
    QuestionDispute,
    UserAnswer,
)


class MockExamQuestionInline(admin.TabularInline):
    model = MockExamQuestion
    extra = 0
    raw_id_fields = ('question_version',)


@admin.register(MockExam)
class MockExamAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'organization',
        'is_public',
        'status',
        'scheduled_at',
        'duration_minutes',
    )
    list_filter = ('status', 'is_public', 'organization')
    search_fields = ('title',)
    raw_id_fields = ('created_by', 'organization')
    inlines = [MockExamQuestionInline]
    date_hierarchy = 'scheduled_at'


@admin.register(MockExamQuestion)
class MockExamQuestionAdmin(admin.ModelAdmin):
    list_display = ('mock_exam', 'order', 'question_version', 'points')
    list_filter = ('mock_exam',)
    raw_id_fields = ('mock_exam', 'question_version')


@admin.register(ExamAttempt)
class ExamAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'exam', 'status', 'score', 'strikes', 'started_at')
    list_filter = ('status', 'cancel_reason')
    raw_id_fields = ('user', 'exam', 'organization')
    search_fields = ('user__email', 'user__username', 'exam__title')
    readonly_fields = ('started_at', 'submitted_at', 'heartbeat_last_at')


@admin.register(PracticeSession)
class PracticeSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'status', 'correct_count', 'total_count', 'started_at')
    list_filter = ('status',)
    raw_id_fields = ('user', 'organization')
    readonly_fields = ('started_at', 'ended_at')


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = ('id', 'attempt', 'session', 'question_version', 'is_correct', 'auto_correct')
    list_filter = ('is_correct', 'auto_correct')
    raw_id_fields = ('attempt', 'session', 'question_version', 'organization')
    readonly_fields = ('answered_at',)


@admin.register(AntiCheatEvent)
class AntiCheatEventAdmin(admin.ModelAdmin):
    list_display = ('attempt', 'event_type', 'occurred_at')
    list_filter = ('event_type',)
    raw_id_fields = ('attempt', 'organization')
    readonly_fields = ('occurred_at',)


@admin.register(QuestionDispute)
class QuestionDisputeAdmin(admin.ModelAdmin):
    list_display = ('user', 'question_version', 'reason', 'status', 'created_at')
    list_filter = ('reason', 'status')
    raw_id_fields = ('user', 'attempt', 'question_version', 'organization')
    search_fields = ('user__email', 'note')
