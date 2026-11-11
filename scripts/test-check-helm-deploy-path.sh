#!/usr/bin/env bash
# Proves scripts/check-helm-deploy-path.py passes on a good render and catches each
# deploy-path defect the 2026-11-12 audit found (H03, F14, E06), by breaking one
# thing at a time in that render.
#
# Usage: test-check-helm-deploy-path.sh GOOD_RENDERED.yaml
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-helm-deploy-path.py"
PY="${PYTHON:-python}"
GOOD="${1:?usage: $0 GOOD_RENDERED.yaml}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
EXT=(--external-secret yuzdanyuz-secrets --external-secret yuzdanyuz-backup-rclone)
failures=0

# mutate <out> <python expression over `docs`, a list of manifests>
mutate() {
  "$PY" - "$GOOD" "$WORK/$1.yaml" "$2" <<'EOF'
import sys, yaml
src, dst, code = sys.argv[1:]
docs = [d for d in yaml.safe_load_all(open(src, encoding='utf-8')) if d]
def find(kind, suffix):
    return next(d for d in docs if d['kind'] == kind and d['metadata']['name'].endswith(suffix))
def pod(d):
    s = d['spec']
    return s['jobTemplate']['spec']['template']['spec'] if d['kind'] == 'CronJob' else s['template']['spec']
exec(code)
yaml.safe_dump_all(docs, open(dst, 'w', encoding='utf-8'))
EOF
}

# expect <name> <0|1> <must-contain> <file> [extra args]
expect() {
  local got=0
  "$PY" "$CHECK" "${EXT[@]}" "${@:5}" "$4" > "$WORK/$1.out" 2>&1 || got=$?
  if [ "$got" -ne "$2" ] || ! grep -qF -- "$3" "$WORK/$1.out"; then
    echo "FAIL  $1: exit $got (want $2)"; sed 's/^/      /' "$WORK/$1.out"; failures=$((failures + 1))
  else
    echo "ok    $1 (exit $got)"
  fi
}

mutate command "c = pod(find('Deployment', '-web'))['containers'][0]; c['command'] = c.pop('args')"
mutate upstream "c = pod(find('Deployment', '-nginx'))['containers'][0]; c['env'] = [e for e in c['env'] if e['name'] != 'BACKEND_UPSTREAM']"
mutate upstream-wrong "c = pod(find('Deployment', '-nginx'))['containers'][0]; [e.update(value='backend:8000') for e in c['env'] if e['name'] == 'CHANNELS_UPSTREAM']"
mutate probe-host "pod(find('Deployment', '-web'))['containers'][0]['readinessProbe']['httpGet'].pop('httpHeaders')"
mutate probe-path "pod(find('Deployment', '-web'))['containers'][0]['livenessProbe']['httpGet']['path'] = '/api/v1/ping/'"
mutate backup-pvc "pod(find('CronJob', '-backup'))['volumes'].append({'name': 'pgdata', 'persistentVolumeClaim': {'claimName': 'postgres-pvc'}})"
mutate backup-secret "pod(find('CronJob', '-backup'))['containers'][0]['envFrom'] = [{'secretRef': {'name': 'walg-credentials'}}]"
mutate ingress-host "find('Ingress', '')['spec']['rules'].append({'host': 'new.yuzdanyuz.uz'})"
grep -v SECURE_REDIRECT_EXEMPT "$ROOT/packages/backend/core/settings/prod.py" > "$WORK/prod-no-exempt.py"

expect good 0 "ok   " "$GOOD"
expect command 1 "sets command ['web'], which replaces the image ENTRYPOINT" "$WORK/command.yaml"
expect upstream 1 "BACKEND_UPSTREAM=None" "$WORK/upstream.yaml"
expect upstream-wrong 1 "CHANNELS_UPSTREAM='backend:8000'" "$WORK/upstream-wrong.yaml"
expect probe-host 1 "readinessProbe: no Host header" "$WORK/probe-host.yaml"
expect probe-path 1 "path /api/v1/ping/ is not in SECURE_REDIRECT_EXEMPT" "$WORK/probe-path.yaml"
expect backup-pvc 1 "uses PVC postgres-pvc, which the chart does not create" "$WORK/backup-pvc.yaml"
expect backup-secret 1 "envFrom Secret walg-credentials is not a known secret" "$WORK/backup-secret.yaml"
expect ingress-host 1 "host new.yuzdanyuz.uz is not in ALLOWED_HOSTS" "$WORK/ingress-host.yaml"
expect no-exempt 1 "SECURE_REDIRECT_EXEMPT is missing" "$GOOD" --prod-settings "$WORK/prod-no-exempt.py"
: > "$WORK/empty.yaml"
expect empty 1 "no pod specs rendered" "$WORK/empty.yaml"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
