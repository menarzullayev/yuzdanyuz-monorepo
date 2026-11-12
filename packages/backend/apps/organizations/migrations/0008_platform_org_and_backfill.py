"""Create the built-in platform organization and put every org-less B2C user into it.

Idempotent: re-running neither duplicates the org/roles nor touches existing memberships.
Staff, superusers and inactive users are skipped. The platform membership is non-primary
(real orgs always win as primary, see CustomUser.primary_membership).
"""

from django.conf import settings
from django.db import migrations
from django.utils import timezone

from apps.organizations.models import SYSTEM_ROLES

BATCH = 1000


def forwards(apps, schema_editor):
    Organization = apps.get_model('organizations', 'Organization')
    OrgRole = apps.get_model('organizations', 'OrgRole')
    Membership = apps.get_model('organizations', 'Membership')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))

    org, _ = Organization.objects.get_or_create(
        slug=getattr(settings, 'PLATFORM_ORG_SLUG', 'platform'),
        defaults={
            'name': settings.PROJECT_BRAND_NAME,
            'org_type': 'platform',
            'status': 'active',
            'single_device_policy': False,
        },
    )
    # Historical models fire no post_save signals: create the system roles here.
    for name, data in SYSTEM_ROLES.items():
        OrgRole.objects.get_or_create(
            organization=org,
            name=name,
            defaults={
                'display_name': data['display_name'],
                'permissions': data['permissions'],
                'is_system_role': True,
            },
        )
    student = OrgRole.objects.get(organization=org, name='student')

    orgless = (
        User.objects.filter(is_active=True, is_staff=False, is_superuser=False)
        .exclude(memberships__status='active')
        .exclude(memberships__organization=org)
        .values_list('pk', flat=True)
    )
    now = timezone.now()
    batch = []
    for pk in orgless.iterator(chunk_size=BATCH):
        batch.append(
            Membership(
                user_id=pk,
                organization=org,
                role=student,
                status='active',
                joined_via='auto',
                is_primary=False,
                joined_at=now,
            )
        )
        if len(batch) >= BATCH:
            Membership.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        Membership.objects.bulk_create(batch, ignore_conflicts=True)


def backwards(apps, schema_editor):
    Membership = apps.get_model('organizations', 'Membership')
    Membership.objects.filter(organization__org_type='platform', joined_via='auto').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('organizations', '0007_membership_joined_via_auto'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
