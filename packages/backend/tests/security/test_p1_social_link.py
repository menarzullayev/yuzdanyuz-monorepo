"""B10 — a social login is linked to an existing account by email only when the
provider says the email is verified AND our stored copy is verified too.
Otherwise nothing is linked or stripped: the owner links Google from the profile."""

import pytest
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount

from apps.accounts.adapters import SocialAccountAdapter
from apps.accounts.models import CustomUser
from tests.integration.test_linking_google import STRONG, google_login, make_request

pytestmark = [pytest.mark.security, pytest.mark.django_db]


def _user(email, *, verified):
    u = CustomUser.objects.create_user(username=email.split('@')[0], email=email, password=STRONG)
    EmailAddress.objects.create(user=u, email=email, verified=verified, primary=True)
    return u


def test_unverified_local_email_is_neither_linked_nor_stripped():
    owner = _user('owner@example.com', verified=False)
    sl = google_login(email='owner@example.com', uid='g-1')
    with pytest.raises(ImmediateHttpResponse) as exc:
        SocialAccountAdapter().pre_social_login(make_request(), sl)
    assert 'link_required' in exc.value.response['Location']
    owner.refresh_from_db()
    assert owner.email == 'owner@example.com'
    assert EmailAddress.objects.filter(user=owner, email='owner@example.com').exists()
    assert not SocialAccount.objects.exists()


@pytest.mark.parametrize('flag', [None, 'yes', 1])
def test_provider_without_an_explicit_verified_flag_never_links(flag):
    owner = _user('owner@example.com', verified=True)
    sl = google_login(email='owner@example.com', uid='g-2')
    if flag is None:
        sl.account.extra_data.pop('email_verified')
    else:
        sl.account.extra_data['email_verified'] = flag
    with pytest.raises(ImmediateHttpResponse):
        SocialAccountAdapter().pre_social_login(make_request(), sl)
    assert not SocialAccount.objects.filter(user=owner).exists()


def test_google_verified_email_v1_field_is_accepted():
    owner = _user('owner@example.com', verified=True)
    sl = google_login(email='owner@example.com', uid='g-3')
    sl.account.extra_data.pop('email_verified')
    sl.account.extra_data['verified_email'] = True
    SocialAccountAdapter().pre_social_login(make_request(), sl)
    assert SocialAccount.objects.filter(user=owner, uid='g-3').exists()


def test_both_sides_verified_still_links():
    owner = _user('owner@example.com', verified=True)
    sl = google_login(email='OWNER@example.com', uid='g-4')
    SocialAccountAdapter().pre_social_login(make_request(), sl)
    assert SocialAccount.objects.filter(user=owner, uid='g-4').exists()


def test_unverified_provider_email_does_not_become_verified_on_a_new_account():
    sl = google_login(email='fresh@example.com', uid='g-5', verified=False)
    SocialAccountAdapter().pre_social_login(make_request(), sl)
    assert all(not a.verified for a in sl.email_addresses)
