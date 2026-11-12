"""Shared helpers for apps.ops tests."""

import json

from django.contrib.auth import get_user_model
from django.test import Client

from apps.accounts.services.token_service import create_token_pair

User = get_user_model()


def client_for(user, device='device-aaaaaaaaaaaaaaaa'):
    c = Client()
    c.cookies['access_token'] = create_token_pair(user, device)[0]
    return c


def staff_user(username='ops-staff'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='pass123', is_staff=True
    )


def jpost(c, url, payload=None):
    return c.post(url, data=json.dumps(payload or {}), content_type='application/json')


def jpatch(c, url, payload):
    return c.patch(url, data=json.dumps(payload), content_type='application/json')
