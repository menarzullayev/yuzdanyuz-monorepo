"""Task 8 — Analytics REST URLs."""

from django.urls import path

from . import business_views as biz
from . import views

app_name = 'analytics'

urlpatterns = [
    path('dashboard/', views.DashboardSummaryView.as_view(), name='dashboard'),
    path('subjects/', views.SubjectAveragesView.as_view(), name='subjects'),
    path('weekly/', views.WeeklyGrowthView.as_view(), name='weekly'),
    path('distribution/', views.ScoreDistributionView.as_view(), name='distribution'),
    path('weak-students/', views.WeakStudentsView.as_view(), name='weak-students'),
    path('question-quality/', views.QuestionQualityView.as_view(), name='question-quality'),
    path('finance/', biz.FinanceSummaryView.as_view(), name='finance'),
    path('finance/expenses/', biz.ExpenseListCreateView.as_view(), name='expenses'),
    path('finance/expenses/<uuid:pk>/', biz.ExpenseDetailView.as_view(), name='expense-detail'),
    path('funnel/', biz.FunnelView.as_view(), name='funnel'),
    path('org-report/', biz.OrgReportView.as_view(), name='org-report'),
    path('export/', views.ReportExportCreateView.as_view(), name='export-create'),
    path('export/<uuid:report_id>/', views.ReportExportDetailView.as_view(), name='export-detail'),
]
