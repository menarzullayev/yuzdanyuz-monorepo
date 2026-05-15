from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def ping_view(request):
    return HttpResponse('pong')


from django.shortcuts import redirect


def home_view(request):
    if request.user.is_authenticated:
        return HttpResponse('<h1>YuzdanYuz Dashboard</h1><p>Tizimga muvaffaqiyatli kirdingiz!</p>')
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
    path('exams/', include('apps.exams.urls', namespace='exams')),
    path('learn/', include('apps.intelligence.urls', namespace='intelligence')),
    path('billing/', include('apps.commerce.urls', namespace='commerce')),
    path('engage/', include('apps.engagement.urls', namespace='engagement')),
    path('analytics/', include('apps.analytics.urls', namespace='analytics')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static('/static/', document_root=settings.STATICFILES_DIRS[0])
