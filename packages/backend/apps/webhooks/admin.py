"""ISSUE-405 — Django admin for webhook endpoints + delivery log viewer."""

from __future__ import annotations

from django.contrib import admin, messages

from .models import WebhookDelivery, WebhookEndpoint
from .tasks import deliver_webhook


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'organization', 'is_active', 'events_display', 'created_at')
    list_filter = ('is_active', 'organization')
    search_fields = ('name', 'url', 'organization__name')
    raw_id_fields = ('organization',)
    readonly_fields = ('id', 'created_at', 'updated_at')

    fieldsets = (
        (None, {'fields': ('id', 'name', 'url', 'organization', 'is_active')}),
        ('Subscription', {'fields': ('events',)}),
        (
            'Security',
            {
                'fields': ('secret',),
                'description': 'HMAC-SHA256 key. Displayed on create; rotate by editing.',
            },
        ),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    @admin.display(description='Events')
    def events_display(self, obj: WebhookEndpoint) -> str:
        events = obj.events or []
        return ', '.join(events) if events else '—'

    def get_readonly_fields(self, request, obj=None):
        # On edit (obj exists), hide raw secret from casual viewers — staff can
        # rotate by reissuing through the dedicated rotate action (future) or
        # by editing this field directly via the change form.
        ro = list(self.readonly_fields)
        if obj is not None:
            # secret stays editable for rotation, but no special masking.
            pass
        return ro


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'event',
        'endpoint',
        'status',
        'response_status',
        'attempts',
        'created_at',
        'completed_at',
    )
    list_filter = ('status', 'event', 'endpoint')
    search_fields = ('endpoint__name', 'event', 'id')
    raw_id_fields = ('endpoint',)
    readonly_fields = (
        'id',
        'endpoint',
        'event',
        'payload',
        'status',
        'response_status',
        'response_body',
        'attempts',
        'last_attempted_at',
        'created_at',
        'completed_at',
    )
    date_hierarchy = 'created_at'
    actions = ['retry_deliveries']

    def has_add_permission(self, request) -> bool:
        # Delivery rows are write-only via the service layer (dispatch_webhook).
        return False

    @admin.action(description='Retry selected deliveries')
    def retry_deliveries(self, request, queryset):
        """Re-queue selected deliveries (resets attempts via fresh task run)."""
        count = 0
        for delivery in queryset:
            deliver_webhook.delay(str(delivery.id))
            count += 1
        messages.success(request, f'Re-queued {count} delivery task(s).')
