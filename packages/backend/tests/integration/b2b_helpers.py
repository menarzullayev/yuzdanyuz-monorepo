"""Shared helpers for the B2B tests."""

from django.contrib.auth import get_user_model
from django.test import Client

from apps.organizations.models import Membership, MembershipStatus, OrgRole

User = get_user_model()


def make_user(username, org=None, role=None, *, status=MembershipStatus.ACTIVE, **extra):
    user = User.objects.create_user(
        username=username, email=f'{username}@example.com', password='pass12345', **extra
    )
    if org is not None:
        Membership.objects.create(
            user=user,
            organization=org,
            role=OrgRole.objects.get(organization=org, name=role),
            status=status,
            is_primary=True,
        )
    return user


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


def post_json(client, url, data):
    return client.post(url, data, content_type='application/json')


def patch_json(client, url, data):
    return client.patch(url, data, content_type='application/json')
