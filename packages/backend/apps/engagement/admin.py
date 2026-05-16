from django.contrib import admin

from .models import (
    LeaderboardSnapshot,
    League,
    LeagueMembership,
    Notification,
    UserStreak,
)


@admin.register(LeaderboardSnapshot)
class LeaderboardSnapshotAdmin(admin.ModelAdmin):
    list_display = ('period', 'period_key', 'scope_kind', 'scope_id', 'total', 'taken_at')
    list_filter = ('period', 'scope_kind')
    search_fields = ('period_key', 'scope_id')
    readonly_fields = ('id', 'taken_at', 'entries')
    date_hierarchy = 'taken_at'


@admin.register(UserStreak)
class UserStreakAdmin(admin.ModelAdmin):
    list_display = ('user', 'current_streak', 'max_streak', 'last_activity_date')
    search_fields = ('user__email', 'user__username')
    raw_id_fields = ('user',)


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ('name', 'rank_order', 'promote_reward_coins')
    ordering = ('rank_order',)


@admin.register(LeagueMembership)
class LeagueMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'league', 'period_start', 'points_earned', 'promoted', 'demoted')
    list_filter = ('league', 'promoted', 'demoted')
    raw_id_fields = ('user', 'league', 'next_league')
    date_hierarchy = 'period_start'


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'channel', 'priority', 'status', 'title', 'created_at')
    list_filter = ('channel', 'priority', 'status')
    search_fields = ('user__email', 'title')
    raw_id_fields = ('user',)
    readonly_fields = ('created_at', 'sent_at', 'read_at', 'delivery_attempts', 'error')
