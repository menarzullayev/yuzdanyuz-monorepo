from django.urls import path

from . import views

app_name = 'catalog'

urlpatterns = [
    path('import/<uuid:batch_id>/review/', views.review_dashboard, name='review_dashboard'),
    # HTMX Endpoints
    path('import/<uuid:batch_id>/drafts/', views.draft_list_htmx, name='draft_list_htmx'),
    path('draft/<uuid:draft_id>/', views.draft_detail_htmx, name='draft_detail_htmx'),
    path('draft/<uuid:draft_id>/autosave/', views.draft_autosave_htmx, name='draft_autosave_htmx'),
    path(
        'import/<uuid:batch_id>/bulk-action/',
        views.draft_bulk_action_htmx,
        name='draft_bulk_action_htmx',
    ),
    # Security
    path('wm.png', views.watermark_png, name='watermark_png'),
]
