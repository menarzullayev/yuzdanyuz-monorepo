"""Give existing manager system roles ``analytics:business`` (audit B11).

The finance/funnel/report views used to check the role name list (owner, admin,
manager); they now check this code, so managers keep access. Added only, never
overwritten, as in 0006.
"""

from django.db import migrations

CODE = 'analytics:business'


def add_code(apps, schema_editor):
    OrgRole = apps.get_model('organizations', 'OrgRole')
    for role in OrgRole.objects.filter(name='manager', is_system_role=True):
        if CODE not in role.permissions:
            role.permissions = [*role.permissions, CODE]
            role.save(update_fields=['permissions'])


def remove_code(apps, schema_editor):
    OrgRole = apps.get_model('organizations', 'OrgRole')
    for role in OrgRole.objects.filter(name='manager', is_system_role=True):
        if CODE in role.permissions:
            role.permissions = [p for p in role.permissions if p != CODE]
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('organizations', '0008_platform_org_and_backfill')]
    operations = [migrations.RunPython(add_code, remove_code)]
