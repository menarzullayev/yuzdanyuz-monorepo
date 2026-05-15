from django.contrib import admin

from .models import LeaderboardSnapshot


@admin.register(LeaderboardSnapshot)
class LeaderboardSnapshotAdmin(admin.ModelAdmin):
    list_display = ('period', 'period_key', 'scope_kind', 'scope_id', 'total', 'taken_at')
    list_filter = ('period', 'scope_kind')
    search_fields = ('period_key', 'scope_id')
    readonly_fields = ('id', 'taken_at', 'entries')
    date_hierarchy = 'taken_at'
