"""Add the group/lesson permission codes to the already-seeded system roles.

New organizations get them from SYSTEM_ROLES; this covers existing ones. Codes are only
ADDED (never overwritten), so per-org customisations of system roles survive.
"""

from django.db import migrations

ADDED = {
    'manager': ['groups:*', 'lessons:*'],
    'teacher': ['groups:view_own', 'lessons:*'],
    'student': ['groups:view_own', 'lessons:view_own'],
    'observer': ['groups:view', 'lessons:view'],
}


def add_codes(apps, schema_editor):
    OrgRole = apps.get_model('organizations', 'OrgRole')
    for name, codes in ADDED.items():
        for role in OrgRole.objects.filter(name=name, is_system_role=True):
            perms = list(role.permissions)
            missing = [c for c in codes if c not in perms]
            if missing:
                role.permissions = perms + missing
                role.save(update_fields=['permissions'])


def remove_codes(apps, schema_editor):
    OrgRole = apps.get_model('organizations', 'OrgRole')
    for name, codes in ADDED.items():
        for role in OrgRole.objects.filter(name=name, is_system_role=True):
            role.permissions = [p for p in role.permissions if p not in codes]
            role.save(update_fields=['permissions'])


class Migration(migrations.Migration):
    dependencies = [('organizations', '0005_b2b_rls')]
    operations = [migrations.RunPython(add_codes, remove_codes)]
