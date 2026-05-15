from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser, District, Region


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
