"""ISSUE-405 — Django admin for webhook endpoints + delivery log viewer."""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages

from .models import WebhookDelivery, WebhookEndpoint, _generate_secret
from .tasks import deliver_webhook


class WebhookEndpointForm(forms.ModelForm):
    """The secret is never rendered. New endpoints get a generated secret; ticking
    'rotate' issues a new one. Either way it is shown once, in the success message."""

    rotate_secret = forms.BooleanField(
        required=False,
        label='Rotate secret',
        help_text='Generate a new HMAC secret; it is shown once after saving.',
    )

    class Meta:
        model = WebhookEndpoint
        fields = ('name', 'url', 'organization', 'is_active', 'events')


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    form = WebhookEndpointForm
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
                'fields': ('rotate_secret',),
                'description': 'HMAC-SHA256 key, encrypted at rest; shown once on create/rotate.',
            },
        ),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    @admin.display(description='Events')
    def events_display(self, obj: WebhookEndpoint) -> str:
        events = obj.events or []
        return ', '.join(events) if events else '—'

    def save_model(self, request, obj, form, change):
        show = not change or form.cleaned_data.get('rotate_secret')
        if change and form.cleaned_data.get('rotate_secret'):
            obj.secret = _generate_secret()
        super().save_model(request, obj, form, change)
        if show:
            self.message_user(
                request,
                f'Webhook secret (copy it now, it is not shown again): {obj.secret}',
                messages.WARNING,
            )


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
