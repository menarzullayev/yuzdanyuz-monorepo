#!/usr/bin/env python3
"""Check that every pod in rendered Helm output carries imagePullSecrets.

The GHCR packages (yuzdanyuz-backend, yuzdanyuz-nginx) are private since
2026-11-11. Until then only the web Deployment used global.imagePullSecrets, so
workers, channels, beat, nginx and the migrate Job could not pull their image.
Render the chart with a pull secret set and pass the output here.

Usage: check-helm-pull-secrets.py RENDERED.yaml [...]
"""

import sys
from pathlib import Path

import yaml

POD_SPEC_PATHS = {
    'Deployment': ('spec', 'template', 'spec'),
    'StatefulSet': ('spec', 'template', 'spec'),
    'DaemonSet': ('spec', 'template', 'spec'),
    'Job': ('spec', 'template', 'spec'),
    'CronJob': ('spec', 'jobTemplate', 'spec', 'template', 'spec'),
}


def pod_specs(path: Path):
    for doc in yaml.safe_load_all(path.read_text(encoding='utf-8')):
        if not doc or doc.get('kind') not in POD_SPEC_PATHS:
            continue
        spec = doc
        for key in POD_SPEC_PATHS[doc['kind']]:
            spec = spec[key]
        yield f'{doc["kind"]}/{doc["metadata"]["name"]}', spec


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    failed = False
    for arg in sys.argv[1:]:
        specs = list(pod_specs(Path(arg)))
        missing = [name for name, spec in specs if not spec.get('imagePullSecrets')]
        if not specs:
            print(f'FAIL {arg}: no pod specs found')
            failed = True
        elif missing:
            print(f'FAIL {arg}: no imagePullSecrets on {", ".join(missing)}')
            failed = True
        else:
            print(f'ok   {arg}: {len(specs)} pod specs all carry imagePullSecrets')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
