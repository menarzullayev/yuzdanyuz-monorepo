from django.urls import path

from . import views

app_name = 'ops'

urlpatterns = [
    path('status/', views.StatusView.as_view(), name='status'),
    path('ops/incidents/', views.IncidentListCreateView.as_view(), name='incidents'),
    path('ops/incidents/<uuid:pk>/', views.IncidentDetailView.as_view(), name='incident-detail'),
    path(
        'ops/incidents/<uuid:pk>/updates/',
        views.IncidentUpdateCreateView.as_view(),
        name='incident-update',
    ),
    path('ops/probes/', views.ProbeListView.as_view(), name='probes'),
    path('ops/security/', views.SecurityDashboardView.as_view(), name='security'),
    path('ops/audit/', views.AuditListView.as_view(), name='audit'),
    path('ops/audit/export/', views.AuditExportView.as_view(), name='audit-export'),
    path('ops/bot/', views.BotSettingsView.as_view(), name='bot'),
    path('ops/bot/set-webhook/', views.BotSetWebhookView.as_view(), name='bot-set-webhook'),
    path(
        'ops/bot/webhook-status/', views.BotWebhookStatusView.as_view(), name='bot-webhook-status'
    ),
    path('ops/bot/post-to-channel/', views.BotPostChannelView.as_view(), name='bot-post-channel'),
]
