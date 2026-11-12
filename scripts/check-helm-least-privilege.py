#!/usr/bin/env python3
"""Least-privilege check on rendered Helm output (audit B15).

For every pod in every rendered file:
  * no container imports the whole application Secret with `envFrom.secretRef`;
    each key is referenced on its own with `secretKeyRef`;
  * a pod only reads the keys its component needs (POLICY below): nginx none, the
    migrate Job no payment / bot / AI / OAuth keys, and so on;
  * only the migrate Job reads the DB owner password, and it connects as a different
    DB user than the runtime pods; only the backup CronJob reads the backup password;
  * every container has readOnlyRootFilesystem: true and, when it is a backend or nginx
    container, a writable emptyDir on /tmp;
  * the nginx container listens on an unprivileged port (>= 1024).

Usage: check-helm-least-privilege.py [--app-secret NAME] RENDERED.yaml [...]
"""

import argparse
import sys
from pathlib import Path

import yaml

POD_SPEC_PATHS = {
    'Deployment': ('spec', 'template', 'spec'),
    'StatefulSet': ('spec', 'template', 'spec'),
    'Job': ('spec', 'template', 'spec'),
    'CronJob': ('spec', 'jobTemplate', 'spec', 'template', 'spec'),
}

PAYMENT = {'PAYME_MERCHANT_ID', 'PAYME_MERCHANT_KEY', 'PAYME_SECRET_KEY', 'CLICK_MERCHANT_ID', 'CLICK_SECRET_KEY'}
OAUTH = {
    'GOOGLE_CLIENT_ID',
    'GOOGLE_CLIENT_SECRET',
    'YANDEX_CLIENT_ID',
    'YANDEX_CLIENT_SECRET',
    'APPLE_CLIENT_ID',
    'APPLE_SECRET',
    'APPLE_PRIVATE_KEY',
}
BOT = {'TELEGRAM_BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET'}
AI = {'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN'}
SMS = {'PLAYMOBILE_LOGIN', 'PLAYMOBILE_PASSWORD'}
OWNER = {'DB_MIGRATE_PASSWORD'}
BACKUP = {'DB_BACKUP_PASSWORD'}

# component label -> keys it must never read
POLICY = {
    'nginx': None,  # no secret at all
    'migrate': PAYMENT | OAUTH | BOT | AI | SMS | BACKUP,
    'channels': PAYMENT | OAUTH | AI | SMS | OWNER | BACKUP | {'TELEGRAM_WEBHOOK_SECRET'},
    'celery-beat': PAYMENT | OAUTH | BOT | AI | SMS | OWNER | BACKUP,
    'celery-worker': PAYMENT | OAUTH | OWNER | BACKUP | {'TELEGRAM_WEBHOOK_SECRET'},
    'web': OWNER | BACKUP,
    'backup': PAYMENT | OAUTH | BOT | AI | SMS | OWNER | {'SECRET_KEY', 'FIELD_ENCRYPTION_KEYS'},
}
NEEDS_TMP = {'nginx', 'migrate', 'channels', 'celery-beat', 'celery-worker', 'web'}


def pod_specs(docs):
    for doc in docs:
        if doc.get('kind') not in POD_SPEC_PATHS:
            continue
        spec = doc
        for key in POD_SPEC_PATHS[doc['kind']]:
            spec = spec[key]
        labels = doc['metadata'].get('labels') or {}
        component = labels.get('app.kubernetes.io/component', '')
        if component.startswith('celery-worker'):
            component = 'celery-worker'
        yield f'{doc["kind"]}/{doc["metadata"]["name"]}', component, spec


def env_value(c, name):
    for e in c.get('env') or []:
        if e['name'] == name:
            return e.get('value')
    return None


def check_docs(docs, app_secret: str) -> list[str]:
    errors = []
    db_users = {}
    for name, component, spec in pod_specs(docs):
        volumes = {v['name']: v for v in spec.get('volumes') or []}
        for c in list(spec.get('initContainers') or []) + list(spec.get('containers') or []):
            where = f'{name}/{c["name"]}'
            for ef in c.get('envFrom') or []:
                if (ef.get('secretRef') or {}).get('name') == app_secret:
                    errors.append(f'{where}: envFrom imports the whole Secret {app_secret}')
            keys = set()
            for e in c.get('env') or []:
                ref = (e.get('valueFrom') or {}).get('secretKeyRef')
                if ref and ref['name'] == app_secret:
                    keys.add(ref['key'])
            forbidden = POLICY.get(component, set())
            if forbidden is None and keys:
                errors.append(f'{where}: reads secret keys {sorted(keys)} but needs none')
            elif forbidden and keys & forbidden:
                errors.append(f'{where}: reads keys it does not need: {sorted(keys & forbidden)}')
            if component != 'migrate' and keys & OWNER:
                errors.append(f'{where}: only the migrate Job may read the DB owner password')
            if component in POLICY and component != 'nginx' and component != 'backup':
                user = env_value(c, 'DB_USER')
                if user:
                    db_users.setdefault(component, set()).add(user)

            sc = c.get('securityContext') or {}
            if sc.get('readOnlyRootFilesystem') is not True:
                errors.append(f'{where}: readOnlyRootFilesystem is not true')
            if component in NEEDS_TMP:
                mount = next((m for m in c.get('volumeMounts') or [] if m['mountPath'] == '/tmp'), None)
                if not mount or 'emptyDir' not in volumes.get(mount['name'], {}):
                    errors.append(f'{where}: no writable emptyDir on /tmp')
            if component == 'nginx':
                for p in c.get('ports') or []:
                    if p['containerPort'] < 1024:
                        errors.append(f'{where}: listens on privileged port {p["containerPort"]}')

    migrate_users = db_users.pop('migrate', set())
    runtime_users = set().union(*db_users.values()) if db_users else set()
    if migrate_users and not migrate_users.isdisjoint(runtime_users):
        errors.append(f'migrate Job uses the runtime DB user {sorted(migrate_users)}: split the roles')
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--app-secret', default='yuzdanyuz-secrets')
    ap.add_argument('files', nargs='+', type=Path)
    args = ap.parse_args()
    failed = False
    for path in args.files:
        docs = [d for d in yaml.safe_load_all(path.read_text(encoding='utf-8')) if d]
        errors = check_docs(docs, args.app_secret)
        if errors:
            failed = True
            for e in errors:
                print(f'FAIL {path}: {e}')
        else:
            print(f'ok   {path}: per-pod secrets, split DB roles, read-only root filesystems')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
