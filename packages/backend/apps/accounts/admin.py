from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone

from .models import (
    CustomUser,
    DeviceSession,
    DirectionPassingScore,
    District,
    DTMDirection,
    ExamCalendarEvent,
    ExamGoal,
    OnboardingProfile,
    OrgResultsConsent,
    PasswordResetRequest,
    Region,
)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = [
        'username',
        'display_name',
        'phone_number',
        'telegram_id',
        'user_type',
        'is_staff',
    ]
    list_filter = [
        'is_staff',
        'is_superuser',
        'is_active',
        'preferred_lang',
        'study_year',
        'region',
    ]
    search_fields = [
        'username',
        'first_name',
        'last_name',
        'phone_number',
        'email',
        'telegram_username',
    ]
    ordering = ['-date_joined']
    readonly_fields = ['user_type', 'is_profile_complete', 'display_name']

    fieldsets = UserAdmin.fieldsets + (
        (
            'Telegram',
            {
                'fields': (
                    'telegram_id',
                    'telegram_username',
                    'telegram_photo_url',
                    'telegram_language',
                    'tg_linked_at',
                )
            },
        ),
        ('Kontakt', {'fields': ('phone_number',)}),
        ('Profil', {'fields': ('avatar', 'preferred_lang', 'birth_year')}),
        ('Hudud', {'fields': ('region', 'district')}),
        ('Akademik', {'fields': ('study_year', 'target_score')}),
        ('Computed', {'fields': ('user_type', 'is_profile_complete', 'display_name')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (('Kontakt', {'fields': ('phone_number', 'email')}),)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ['code', 'name_uz', 'name_ru', 'slug']
    search_fields = ['name_uz', 'name_ru']
    ordering = ['code']


@admin.register(District)
class DistrictAdmin(admin.ModelAdmin):
    list_display = ['code', 'name_uz', 'region']
    list_filter = ['region']
    search_fields = ['name_uz', 'name_ru']


@admin.register(PasswordResetRequest)
class PasswordResetRequestAdmin(admin.ModelAdmin):
    """Platform-admin queue: pick a request, issue a temporary password, hand it over out of band."""

    list_display = [
        'username_entered',
        'user',
        'status',
        'contact_note',
        'created_at',
        'handled_by',
    ]
    list_filter = ['status']
    search_fields = ['username_entered', 'contact_note']
    readonly_fields = ['user', 'username_entered', 'created_at', 'handled_by', 'handled_at']
    actions = ['issue_temporary_password', 'reject_requests']

    @admin.action(
        description='Vaqtinchalik parol berish (foydalanuvchi keyingi kirishda almashtiradi)'
    )
    def issue_temporary_password(self, request, queryset):
        from apps.accounts.services import recovery

        for req in queryset.filter(status=PasswordResetRequest.Status.PENDING):
            if req.user is None or req.user.is_superuser:
                self.message_user(
                    request,
                    f'{req.username_entered}: hisob topilmadi yoki ruxsat yoʻq.',
                    messages.WARNING,
                )
                continue
            password = recovery.set_temporary_password(req.user)
            req.status = PasswordResetRequest.Status.DONE
            req.handled_by = request.user
            req.handled_at = timezone.now()
            req.save(update_fields=['status', 'handled_by', 'handled_at'])
            # Shown once to the staff member, who passes it to the person out of band.
            self.message_user(
                request, f'{req.user.username}: vaqtinchalik parol {password}', messages.SUCCESS
            )

    @admin.action(description='Rad etish')
    def reject_requests(self, request, queryset):
        queryset.filter(status=PasswordResetRequest.Status.PENDING).update(
            status=PasswordResetRequest.Status.REJECTED,
            handled_by=request.user,
            handled_at=timezone.now(),
        )


@admin.register(DeviceSession)
class DeviceSessionAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_label', 'country', 'last_used_at', 'revoked_at']
    list_filter = ['revoked_at', 'remember']
    search_fields = ['user__username', 'device_label']
    readonly_fields = [f.name for f in DeviceSession._meta.fields]


@admin.register(ExamGoal)
class ExamGoalAdmin(admin.ModelAdmin):
    list_display = ['code', 'name_uz', 'requires_direction', 'is_active', 'sort_order']
    list_editable = ['is_active', 'sort_order']
    search_fields = ['code', 'name_uz']


class DirectionPassingScoreInline(admin.TabularInline):
    model = DirectionPassingScore
    extra = 0


@admin.register(DTMDirection)
class DTMDirectionAdmin(admin.ModelAdmin):
    list_display = ['code', 'name_uz', 'subject_1', 'subject_2', 'is_active']
    list_filter = ['is_active']
    search_fields = ['code', 'name_uz']
    inlines = [DirectionPassingScoreInline]


@admin.register(ExamCalendarEvent)
class ExamCalendarEventAdmin(admin.ModelAdmin):
    list_display = ['title', 'exam_goal', 'date', 'registration_deadline', 'region']
    list_filter = ['exam_goal']
    date_hierarchy = 'date'


@admin.register(OnboardingProfile)
class OnboardingProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'primary_goal', 'test_language', 'graduation_year', 'completed_at']
    list_filter = ['test_language', 'primary_goal']
    search_fields = ['user__username']
    raw_id_fields = ['user']


@admin.register(OrgResultsConsent)
class OrgResultsConsentAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'consented_at', 'revoked_at']
    list_filter = ['organization']
    raw_id_fields = ['user']
