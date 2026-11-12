#!/usr/bin/env bash
# Proves scripts/check-helm-least-privilege.py passes on a good render and catches each
# least-privilege regression (audit B15) by breaking one thing at a time in that render.
#
# Usage: test-check-helm-least-privilege.sh GOOD_RENDERED.yaml
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-helm-least-privilege.py"
PY="${PYTHON:-python}"
GOOD="${1:?usage: $0 GOOD_RENDERED.yaml}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

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
def ref(key):
    return {'name': key, 'valueFrom': {'secretKeyRef': {'name': 'yuzdanyuz-secrets', 'key': key}}}
exec(code)
yaml.safe_dump_all(docs, open(dst, 'w', encoding='utf-8'))
EOF
}

expect() {
  local got=0
  "$PY" "$CHECK" "$4" > "$WORK/$1.out" 2>&1 || got=$?
  if [ "$got" -ne "$2" ] || ! grep -qF -- "$3" "$WORK/$1.out"; then
    echo "FAIL  $1: exit $got (want $2)"; sed 's/^/      /' "$WORK/$1.out"; failures=$((failures + 1))
  else
    echo "ok    $1 (exit $got)"
  fi
}

mutate envfrom "pod(find('Deployment', '-web'))['containers'][0]['envFrom'].append({'secretRef': {'name': 'yuzdanyuz-secrets'}})"
mutate nginx-secret "c = pod(find('Deployment', '-nginx'))['containers'][0]; c['env'].append(ref('SECRET_KEY'))"
mutate migrate-payme "pod(find('Job', '-migrate-1'))['containers'][0]['env'].append(ref('PAYME_MERCHANT_KEY'))"
mutate beat-bot "pod(find('Deployment', '-celery-beat'))['containers'][0]['env'].append(ref('TELEGRAM_BOT_TOKEN'))"
mutate owner-pw "pod(find('Deployment', '-web'))['containers'][0]['env'].append(ref('DB_MIGRATE_PASSWORD'))"
mutate same-user "[e.update(value='yuzdanyuz') for e in pod(find('Job', '-migrate-1'))['containers'][0]['env'] if e['name'] == 'DB_USER']"
mutate rw-root "pod(find('Deployment', '-channels'))['containers'][0]['securityContext']['readOnlyRootFilesystem'] = False"
mutate no-tmp "c = pod(find('Deployment', '-celery-beat'))['containers'][0]; c['volumeMounts'] = []"
mutate nginx-80 "pod(find('Deployment', '-nginx'))['containers'][0]['ports'][0]['containerPort'] = 80"

expect good 0 "ok   " "$GOOD"
expect envfrom 1 "envFrom imports the whole Secret yuzdanyuz-secrets" "$WORK/envfrom.yaml"
expect nginx-secret 1 "reads secret keys ['SECRET_KEY'] but needs none" "$WORK/nginx-secret.yaml"
expect migrate-payme 1 "reads keys it does not need: ['PAYME_MERCHANT_KEY']" "$WORK/migrate-payme.yaml"
expect beat-bot 1 "reads keys it does not need: ['TELEGRAM_BOT_TOKEN']" "$WORK/beat-bot.yaml"
expect owner-pw 1 "only the migrate Job may read the DB owner password" "$WORK/owner-pw.yaml"
expect same-user 1 "migrate Job uses the runtime DB user" "$WORK/same-user.yaml"
expect rw-root 1 "readOnlyRootFilesystem is not true" "$WORK/rw-root.yaml"
expect no-tmp 1 "no writable emptyDir on /tmp" "$WORK/no-tmp.yaml"
expect nginx-80 1 "listens on privileged port 80" "$WORK/nginx-80.yaml"

[ "$failures" -eq 0 ] || { echo "$failures check(s) misbehaved"; exit 1; }
echo "all least-privilege checks behave"
