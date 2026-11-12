#!/usr/bin/env python3
"""Check rendered Helm manifests for the P1 reliability guarantees.

* E05: when a PgBouncer Deployment is rendered, the app ConfigMap points DB_HOST at
  its Service with DB_POOLER=pgbouncer, its pgbouncer.ini uses transaction pooling,
  and the migrate Job bypasses it (direct host, no statement timeout).
* F13: every Celery worker Deployment mounts the shared media PVC at /app/media.
* F05: the nginx Deployment fills /var/www/static from the backend image
  (initContainer `copy-static`).

Usage: check-helm-reliability.py RENDERED.yaml [...]
Exit 1 with one line per problem.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml


def _docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding='utf-8')) if d]


def _component(doc: dict) -> str:
    return (doc.get('metadata', {}).get('labels') or {}).get('app.kubernetes.io/component', '')


def _pod(doc: dict) -> dict:
    return doc['spec']['template']['spec']


def _env(container: dict) -> dict:
    return {e['name']: e.get('value') for e in container.get('env') or [] if 'name' in e}


def check(path: Path) -> list[str]:
    docs = _docs(path)
    errors: list[str] = []
    deployments = [d for d in docs if d.get('kind') == 'Deployment']
    services = {d['metadata']['name'] for d in docs if d.get('kind') == 'Service'}
    app_cm = next(
        (
            d
            for d in docs
            if d.get('kind') == 'ConfigMap' and d['metadata']['name'].endswith('-env')
        ),
        None,
    )

    # E05 ────────────────────────────────────────────────────────────────────
    pgbouncer = [d for d in deployments if _component(d) == 'pgbouncer']
    if pgbouncer:
        data = (app_cm or {}).get('data') or {}
        host = data.get('DB_HOST', '')
        if host not in services or not host.endswith('-pgbouncer'):
            errors.append(f'ConfigMap DB_HOST={host!r} is not the PgBouncer Service')
        if data.get('DB_POOLER') != 'pgbouncer':
            errors.append('ConfigMap DB_POOLER must be "pgbouncer" when PgBouncer is rendered')
        ini_cm = next(
            (
                d
                for d in docs
                if d.get('kind') == 'ConfigMap' and d['metadata']['name'].endswith('-pgbouncer')
            ),
            None,
        )
        ini = ((ini_cm or {}).get('data') or {}).get('pgbouncer.ini', '')
        if 'pool_mode = transaction' not in ini:
            errors.append('pgbouncer.ini: pool_mode is not transaction')
        if 'statement_timeout' not in ini:
            errors.append('pgbouncer.ini: no statement_timeout (connect_query) on the pools')
        for job in (d for d in docs if d.get('kind') == 'Job' and _component(d) == 'migrate'):
            env = _env(_pod(job)['containers'][0])
            if env.get('DB_HOST', host) == host:
                errors.append('migrate Job goes through PgBouncer; it must use the direct host')
            if env.get('DB_STATEMENT_TIMEOUT_MS') != '0':
                errors.append('migrate Job must run without a statement timeout')

    # F13 ────────────────────────────────────────────────────────────────────
    for dep in deployments:
        if not _component(dep).startswith('celery-worker'):
            continue
        pod = _pod(dep)
        claims = {
            v['name']
            for v in pod.get('volumes') or []
            if (v.get('persistentVolumeClaim') or {}).get('claimName', '').endswith('-media')
        }
        mounts = {
            m['name']
            for c in pod['containers']
            for m in c.get('volumeMounts') or []
            if m.get('mountPath') == '/app/media'
        }
        if not claims & mounts:
            errors.append(f'{dep["metadata"]["name"]}: media PVC is not mounted at /app/media')

    # F05 ────────────────────────────────────────────────────────────────────
    for dep in deployments:
        if _component(dep) != 'nginx':
            continue
        pod = _pod(dep)
        init = [c for c in pod.get('initContainers') or [] if (c.get('args') or [''])[0] == 'copy-static']
        if not init:
            errors.append(f'{dep["metadata"]["name"]}: no copy-static initContainer')
            continue
        target = init[0]['args'][1] if len(init[0]['args']) > 1 else ''
        init_vol = {m['name'] for m in init[0].get('volumeMounts') or [] if m['mountPath'] == target}
        nginx_vol = {
            m['name']
            for c in pod['containers']
            for m in c.get('volumeMounts') or []
            if m.get('mountPath') == '/var/www/static'
        }
        if not init_vol & nginx_vol:
            errors.append(f'{dep["metadata"]["name"]}: /var/www/static is not the copy-static volume')

    return errors


def main() -> int:
    failed = False
    for arg in sys.argv[1:]:
        errors = check(Path(arg))
        for e in errors:
            print(f'FAIL {arg}: {e}')
        if errors:
            failed = True
        else:
            print(f'ok   {arg}: pgbouncer wiring, worker media mounts, nginx static')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
