from django.urls import path

from . import partnership_views as v

app_name = 'partnerships'

urlpatterns = [
    path('apply/', v.ApplyView.as_view(), name='apply'),
    path('applications/', v.ApplicationListView.as_view(), name='application-list'),
    path('applications/summary/', v.ApplicationSummaryView.as_view(), name='application-summary'),
    path('applications/<uuid:pk>/', v.ApplicationDetailView.as_view(), name='application-detail'),
]
