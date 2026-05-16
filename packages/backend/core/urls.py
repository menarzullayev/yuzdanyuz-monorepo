from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import include, path


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
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static('/static/', document_root=settings.STATICFILES_DIRS[0])
