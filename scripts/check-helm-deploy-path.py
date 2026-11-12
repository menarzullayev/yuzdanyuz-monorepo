#!/usr/bin/env python3
"""Check the deploy path in rendered Helm output (audit P0s H03, F14, E06).

Asserts, for every rendered file passed:
  * no backend container sets `command` (it would replace the image ENTRYPOINT
    tini + /entrypoint.sh) and each passes a known entrypoint mode as `args`;
  * the nginx Deployment points BACKEND_UPSTREAM / CHANNELS_UPSTREAM at the
    rendered web / channels Service name and port, and renders them into a
    writable emptyDir;
  * every httpGet probe sends a Host header that Django allows and hits a path
    exempt from SECURE_SSL_REDIRECT (read from core/settings/prod.py);
  * ALLOWED_HOSTS covers every Ingress host;
  * every ConfigMap / PVC a pod mounts is rendered by the chart, and every Secret
    it reads is one of the out-of-band secrets named with --external-secret.

Usage: check-helm-deploy-path.py [--prod-settings PATH] [--external-secret NAME ...]
                                 RENDERED.yaml [...]
"""

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROD_SETTINGS = ROOT / 'packages/backend/core/settings/prod.py'
BACKEND_IMAGE = 'yuzdanyuz-backend'
NGINX_IMAGE = 'yuzdanyuz-nginx'
ENTRYPOINT_MODES = {'web', 'asgi', 'worker', 'beat', 'flower', 'migrate', 'copy-static'}
# prod.py: ALLOWED_HOSTS always starts with these
ALWAYS_ALLOWED = {'127.0.0.1', 'localhost'}

POD_SPEC_PATHS = {
    'Deployment': ('spec', 'template', 'spec'),
    'StatefulSet': ('spec', 'template', 'spec'),
    'DaemonSet': ('spec', 'template', 'spec'),
    'Job': ('spec', 'template', 'spec'),
    'CronJob': ('spec', 'jobTemplate', 'spec', 'template', 'spec'),
}


def redirect_exempt_patterns(prod_settings: Path) -> list[re.Pattern]:
    tree = ast.parse(prod_settings.read_text(encoding='utf-8'))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'SECURE_REDIRECT_EXEMPT' for t in node.targets)
        ):
            return [re.compile(p) for p in ast.literal_eval(node.value)]
    return []


def pod_specs(docs):
    for doc in docs:
        if doc.get('kind') not in POD_SPEC_PATHS:
            continue
        spec = doc
        for key in POD_SPEC_PATHS[doc['kind']]:
            spec = spec[key]
        yield f'{doc["kind"]}/{doc["metadata"]["name"]}', doc, spec


def containers(spec):
    return list(spec.get('initContainers') or []) + list(spec.get('containers') or [])


def check_file(path: Path, exempt: list[re.Pattern], external_secrets: set[str]) -> list[str]:
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding='utf-8')) if d]
    errors: list[str] = []
    if not any(d.get('kind') in POD_SPEC_PATHS for d in docs):
        return ['no pod specs rendered (empty or failed helm template?)']
    by_kind: dict[str, dict[str, dict]] = {}
    for d in docs:
        by_kind.setdefault(d['kind'], {})[d['metadata']['name']] = d

    # ALLOWED_HOSTS from the env ConfigMap
    allowed = set(ALWAYS_ALLOWED)
    for cm in by_kind.get('ConfigMap', {}).values():
        hosts = (cm.get('data') or {}).get('ALLOWED_HOSTS')
        if hosts:
            allowed |= {h.strip() for h in hosts.split(',') if h.strip()}
    for ing_name, ing in by_kind.get('Ingress', {}).items():
        for rule in ing['spec'].get('rules') or []:
            if rule.get('host') and rule['host'] not in allowed:
                errors.append(f'Ingress/{ing_name}: host {rule["host"]} is not in ALLOWED_HOSTS')

    services = by_kind.get('Service', {})

    def service_for(component: str):
        for name, svc in services.items():
            if svc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == component:
                return name, svc['spec']['ports'][0]['port']
        return None

    for name, doc, spec in pod_specs(docs):
        # H03: entrypoint must survive
        for c in containers(spec):
            image = c.get('image', '')
            if BACKEND_IMAGE in image:
                if 'command' in c:
                    errors.append(
                        f'{name}/{c["name"]}: sets command {c["command"]!r}, which replaces the '
                        'image ENTRYPOINT (tini + /entrypoint.sh); use args'
                    )
                args = c.get('args') or []
                if not args or args[0] not in ENTRYPOINT_MODES:
                    errors.append(f'{name}/{c["name"]}: args {args!r} is not an entrypoint mode {sorted(ENTRYPOINT_MODES)}')

            # F14: probes
            for probe_kind in ('livenessProbe', 'readinessProbe', 'startupProbe'):
                http = (c.get(probe_kind) or {}).get('httpGet')
                if not http:
                    continue
                where = f'{name}/{c["name"]} {probe_kind}'
                headers = {h['name'].lower(): h['value'] for h in http.get('httpHeaders') or []}
                host = headers.get('host')
                if not host:
                    errors.append(f'{where}: no Host header, kubelet sends the pod IP (not in ALLOWED_HOSTS)')
                elif host not in allowed:
                    errors.append(f'{where}: Host {host} is not in ALLOWED_HOSTS')
                probe_path = http.get('path', '/').lstrip('/')
                if not any(p.search(probe_path) for p in exempt):
                    errors.append(f'{where}: path /{probe_path} is not in SECURE_REDIRECT_EXEMPT, it gets a 301')

        # H03: nginx upstreams
        for c in containers(spec):
            if NGINX_IMAGE not in c.get('image', ''):
                continue
            env = {e['name']: e.get('value') for e in c.get('env') or []}
            for var, component in (('BACKEND_UPSTREAM', 'web'), ('CHANNELS_UPSTREAM', 'channels')):
                svc = service_for(component)
                if svc is None:
                    errors.append(f'{name}: no Service for component {component}')
                    continue
                want = f'{svc[0]}:{svc[1]}'
                got = env.get(var)
                if got != want:
                    errors.append(
                        f'{name}/{c["name"]}: {var}={got!r} (unset means the image default, a compose '
                        f'host name); the {component} Service is {want}'
                    )
            mounts = {m['mountPath']: m['name'] for m in c.get('volumeMounts') or []}
            vol = mounts.get('/etc/nginx/upstreams')
            vols = {v['name']: v for v in spec.get('volumes') or []}
            if vol is None or 'emptyDir' not in vols.get(vol, {}):
                errors.append(f'{name}/{c["name"]}: /etc/nginx/upstreams is not a writable emptyDir (pod is non-root)')

        # E06 (and every pod): referenced resources exist
        for v in spec.get('volumes') or []:
            if 'persistentVolumeClaim' in v:
                claim = v['persistentVolumeClaim']['claimName']
                if claim not in by_kind.get('PersistentVolumeClaim', {}):
                    errors.append(f'{name}: volume {v["name"]} uses PVC {claim}, which the chart does not create')
            if 'configMap' in v and v['configMap']['name'] not in by_kind.get('ConfigMap', {}):
                errors.append(f'{name}: volume {v["name"]} uses ConfigMap {v["configMap"]["name"]}, which the chart does not create')
            if 'secret' in v and v['secret']['secretName'] not in external_secrets:
                errors.append(f'{name}: volume {v["name"]} uses Secret {v["secret"]["secretName"]}, not a known secret')
        for c in containers(spec):
            for ef in c.get('envFrom') or []:
                if 'configMapRef' in ef and ef['configMapRef']['name'] not in by_kind.get('ConfigMap', {}):
                    errors.append(f'{name}/{c["name"]}: envFrom ConfigMap {ef["configMapRef"]["name"]} is not rendered')
                if 'secretRef' in ef and ef['secretRef']['name'] not in external_secrets:
                    errors.append(f'{name}/{c["name"]}: envFrom Secret {ef["secretRef"]["name"]} is not a known secret')
            for e in c.get('env') or []:
                ref = (e.get('valueFrom') or {}).get('secretKeyRef')
                if ref and ref['name'] not in external_secrets:
                    errors.append(f'{name}/{c["name"]}: env {e["name"]} reads Secret {ref["name"]}, not a known secret')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--prod-settings', type=Path, default=DEFAULT_PROD_SETTINGS)
    parser.add_argument(
        '--external-secret', action='append', default=[],
        help='Secret created out of band (kubectl create secret); repeatable',
    )
    parser.add_argument('rendered', nargs='+', type=Path)
    args = parser.parse_args()

    exempt = redirect_exempt_patterns(args.prod_settings)
    failed = False
    if not exempt:
        print(f'FAIL {args.prod_settings}: SECURE_REDIRECT_EXEMPT is missing, health probes get a 301')
        failed = True
    for path in args.rendered:
        errors = check_file(path, exempt, set(args.external_secret))
        if errors:
            failed = True
            print(f'FAIL {path}:')
            for e in errors:
                print(f'       {e}')
        else:
            print(f'ok   {path}: entrypoint args, nginx upstreams, probes, ALLOWED_HOSTS, referenced resources')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
