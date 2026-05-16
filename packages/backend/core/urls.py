from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import include, path

from apps.engagement.sitemaps import PublicQuestionsSitemap
from core import health as health_views

sitemaps = {'questions': PublicQuestionsSitemap}


def ping_view(request):
    return HttpResponse('pong')


def home_view(request):
    if request.user.is_authenticated:
        return render(request, 'index.html')
    return redirect('accounts:login')


urlpatterns = [
    path('', home_view, name='home'),
    path('admin/', admin.site.urls),
    path('ping/', ping_view, name='ping'),
    # Task 10 — K8s liveness/readiness probes + Prometheus exporter
    path('health/', health_views.liveness, name='health-liveness'),
    path('health/ready/', health_views.readiness, name='health-readiness'),
    path('', include('django_prometheus.urls')),  # /metrics endpoint
    # Google OAuth (django-allauth)
    path('accounts/', include('allauth.urls')),
    # Domain apps
    path('', include('apps.accounts.urls', namespace='accounts')),
    path('org/', include('apps.organizations.urls', namespace='organizations')),
    path('catalog/', include('apps.catalog.urls', namespace='catalog')),
    path('api/exams/', include('apps.exams.urls', namespace='exams')),
    path('api/leaderboard/', include('apps.engagement.urls', namespace='engagement')),
    path('api/intelligence/', include('apps.intelligence.urls', namespace='intelligence')),
    path('api/', include('apps.commerce.urls', namespace='commerce')),
    path('api/analytics/', include('apps.analytics.urls', namespace='analytics')),
    # Task 9 — SEO sitemap
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static('/static/', document_root=settings.STATICFILES_DIRS[0])
