#!/usr/bin/env python3
"""Check that every routed Celery queue has a worker, and that AI calls stay capped.

core/celery.py routes tasks to the `critical`, `realtime`, `ai` and `batch`
queues. Before this check, docker-compose and the Helm chart only ran a worker
for `default,celery`: the per-queue workers existed in values.yaml but no
template rendered them, so routed tasks (exam scoring, notifications, AI
feedback, periodic jobs) waited forever.

Every AI task runs `claude -p` on one shared Claude subscription, so the number
of parallel AI calls (replicas, or the KEDA maximum, times CELERY_CONCURRENCY)
must stay within --ai-max-parallel.

Usage:
  check-celery-queues.py [--root PATH] [--manifests RENDERED.yaml ...] [--ai-max-parallel N]

Without --manifests only docker-compose.yml is checked. Pass the output of
`helm template` to check the chart too.
"""

import argparse
import ast
import re
import sys
from pathlib import Path

import yaml

DEFAULT_CONCURRENCY = 4  # docker/entrypoint.sh: --concurrency="${CELERY_CONCURRENCY:-4}"


def routed_queues(root: Path) -> set[str]:
    tree = ast.parse((root / 'packages/backend/core/celery.py').read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Attribute)
            and node.targets[0].attr == 'task_routes'
        ):
            routes = ast.literal_eval(node.value)
            return {r['queue'] for r in routes.values()}
    raise SystemExit('FAIL task_routes not found in core/celery.py')


def entrypoint_default_queues(root: Path) -> list[str]:
    text = (root / 'docker/entrypoint.sh').read_text(encoding='utf-8')
    m = re.search(r'CELERY_QUEUES:-([^}"]+)}', text)
    if not m:
        raise SystemExit('FAIL default CELERY_QUEUES not found in docker/entrypoint.sh')
    return m.group(1).split(',')


def compose_workers(root: Path, default_queues: list[str]) -> list[dict]:
    compose = yaml.safe_load((root / 'docker-compose.yml').read_text(encoding='utf-8'))
    workers = []
    for name, svc in compose.get('services', {}).items():
        if svc.get('command') != ['worker']:
            continue
        env = svc.get('environment') or {}
        queues = str(env['CELERY_QUEUES']).split(',') if 'CELERY_QUEUES' in env else default_queues
        workers.append(
            {
                'name': f'compose:{name}',
                'queues': queues,
                'concurrency': int(env.get('CELERY_CONCURRENCY', DEFAULT_CONCURRENCY)),
                'max_replicas': 1,
            }
        )
    return workers


def manifest_workers(path: Path, default_queues: list[str]) -> list[dict]:
    docs = [d for d in yaml.safe_load_all(path.read_text(encoding='utf-8')) if d]
    keda_max = {
        d['spec']['scaleTargetRef']['name']: int(d['spec']['maxReplicaCount'])
        for d in docs
        if d.get('kind') == 'ScaledObject'
    }
    workers = []
    for d in docs:
        if d.get('kind') != 'Deployment':
            continue
        for c in d['spec']['template']['spec']['containers']:
            if c.get('command') != ['worker']:
                continue
            env = {e['name']: e.get('value') for e in c.get('env') or []}
            name = d['metadata']['name']
            queues = env['CELERY_QUEUES'].split(',') if env.get('CELERY_QUEUES') else default_queues
            workers.append(
                {
                    'name': f'{path.name}:{name}',
                    'queues': queues,
                    'concurrency': int(env.get('CELERY_CONCURRENCY') or DEFAULT_CONCURRENCY),
                    'max_replicas': max(int(d['spec'].get('replicas', 1)), keda_max.get(name, 0)),
                }
            )
    return workers


def check(label: str, workers: list[dict], routed: set[str], ai_max: int) -> bool:
    ok = True
    consumed = {q for w in workers for q in w['queues']}
    missing = sorted(routed - consumed)
    if missing:
        print(f'FAIL {label}: no worker consumes {", ".join(missing)} (routed in core/celery.py)')
        ok = False
    ai = [w for w in workers if 'ai' in w['queues']]
    parallel = sum(w['max_replicas'] * w['concurrency'] for w in ai)
    if parallel > ai_max:
        detail = ', '.join(f'{w["name"]} {w["max_replicas"]}x{w["concurrency"]}' for w in ai)
        print(
            f'FAIL {label}: up to {parallel} parallel AI calls ({detail}); the shared Claude '
            f'subscription allows {ai_max}'
        )
        ok = False
    if ok:
        print(f'ok   {label}: {len(workers)} workers cover {", ".join(sorted(routed))}; AI parallel {parallel}')
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--manifests', type=Path, nargs='*', default=[])
    parser.add_argument('--ai-max-parallel', type=int, default=2)
    args = parser.parse_args()

    routed = routed_queues(args.root)
    default_queues = entrypoint_default_queues(args.root)
    results = [check('docker-compose', compose_workers(args.root, default_queues), routed, args.ai_max_parallel)]
    for path in args.manifests:
        results.append(check(f'helm {path.name}', manifest_workers(path, default_queues), routed, args.ai_max_parallel))
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
