"""Task 8 — Analytics REST URLs."""

from django.urls import path

from . import views

app_name = 'analytics'

urlpatterns = [
    path('dashboard/', views.DashboardSummaryView.as_view(), name='dashboard'),
    path('subjects/', views.SubjectAveragesView.as_view(), name='subjects'),
    path('weekly/', views.WeeklyGrowthView.as_view(), name='weekly'),
    path('distribution/', views.ScoreDistributionView.as_view(), name='distribution'),
    path('weak-students/', views.WeakStudentsView.as_view(), name='weak-students'),
    path('export/', views.ReportExportCreateView.as_view(), name='export-create'),
    path('export/<uuid:report_id>/', views.ReportExportDetailView.as_view(), name='export-detail'),
]
