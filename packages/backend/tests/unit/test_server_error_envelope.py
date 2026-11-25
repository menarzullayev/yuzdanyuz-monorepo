"""Audit D02: unhandled exceptions reach API clients as a JSON envelope, not HTML."""

import pytest
from django.test import Client
from django.urls import path


def _boom(request):
    raise RuntimeError('secret internal detail')


urlpatterns = [
    path('api/boom/', _boom),
    path('page/boom/', _boom),
]
handler500 = 'core.exceptions.server_error'


@pytest.fixture
def client(settings):
    settings.ROOT_URLCONF = __name__
    settings.DEBUG = False
    return Client(raise_request_exception=False)


class TestServerErrorEnvelope:
    def test_api_path_gets_json_envelope_with_request_id(self, client):
        resp = client.get('/api/boom/', HTTP_X_REQUEST_ID='req-abc-123')
        assert resp.status_code == 500
        assert resp['Content-Type'].startswith('application/json')
        body = resp.json()
        assert body['success'] is False
        assert body['error']['code'] == 'server_error'
        assert body['error']['request_id'] == 'req-abc-123'
        assert resp['X-Request-ID'] == 'req-abc-123'

    def test_exception_text_is_not_leaked(self, client):
        resp = client.get('/api/boom/')
        assert b'secret internal detail' not in resp.content

    def test_json_accept_header_gets_envelope_off_api(self, client):
        resp = client.get('/page/boom/', HTTP_ACCEPT='application/json')
        assert resp.status_code == 500
        assert resp.json()['error']['code'] == 'server_error'

    def test_browser_page_keeps_html(self, client):
        resp = client.get('/page/boom/', HTTP_ACCEPT='text/html')
        assert resp.status_code == 500
        assert resp['Content-Type'].startswith('text/html')

    def test_project_urlconf_registers_handler(self):
        from core import urls

        assert urls.handler500 == 'core.exceptions.server_error'
