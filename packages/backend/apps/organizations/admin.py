from django.contrib import admin

from .group_models import Group, GroupMember, Lesson
from .partnership_models import PartnershipApplication


@admin.register(PartnershipApplication)
class PartnershipApplicationAdmin(admin.ModelAdmin):
    list_display = ('name', 'applicant_type', 'phone', 'region', 'status', 'next_call_date')
    list_filter = ('status', 'applicant_type')
    search_fields = ('name', 'contact_person', 'phone')


class _TenantAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        # default manager is tenant-scoped; the admin is a platform-staff tool
        return self.model.global_objects.all()


@admin.register(Group)
class GroupAdmin(_TenantAdmin):
    list_display = ('name', 'organization', 'is_archived')


@admin.register(GroupMember)
class GroupMemberAdmin(_TenantAdmin):
    list_display = ('group', 'user', 'role')


@admin.register(Lesson)
class LessonAdmin(_TenantAdmin):
    list_display = ('title', 'group', 'scheduled_at')
