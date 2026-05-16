from django.contrib import admin

from .models import ExamEvent, ReportExport


@admin.register(ExamEvent)
class ExamEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'subject_name', 'score', 'organization', 'completed_at')
    list_filter = ('subject_name', 'organization')
    search_fields = ('user__email', 'user__username', 'subject_name')
    raw_id_fields = ('user', 'organization')
    readonly_fields = (
        'exam_attempt_id',
        'mock_exam_id',
        'subject_id',
        'subject_name',
        'score',
        'correct_count',
        'total_count',
        'duration_seconds',
        'region_id',
        'completed_at',
        'created_at',
    )
    date_hierarchy = 'completed_at'


@admin.register(ReportExport)
class ReportExportAdmin(admin.ModelAdmin):
    list_display = ('user', 'report_type', 'fmt', 'status', 'requested_at', 'completed_at')
    list_filter = ('status', 'fmt', 'report_type')
    raw_id_fields = ('user', 'organization')
    readonly_fields = ('file', 'requested_at', 'completed_at', 'error')
