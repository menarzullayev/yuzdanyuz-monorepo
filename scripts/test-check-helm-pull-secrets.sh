#!/usr/bin/env bash
# Proves scripts/check-helm-pull-secrets.py passes when every pod has a pull secret
# and names the pods that lack one (Deployment and CronJob shapes).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-helm-pull-secrets.py"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

SECRET='imagePullSecrets: [{name: ghcr-pull}], '
deploy() { printf -- '---\nkind: Deployment\nmetadata: {name: %s}\nspec: {template: {spec: {%scontainers: [{name: c}]}}}\n' "$1" "$2"; }
cron() { printf -- '---\nkind: CronJob\nmetadata: {name: %s}\nspec: {jobTemplate: {spec: {template: {spec: {%scontainers: [{name: c}]}}}}}\n' "$1" "$2"; }

# expect <name> <0|1> <must-contain> <file>
expect() {
  local got=0
  "$PY" "$CHECK" "$4" > "$WORK/$1.out" 2>&1 || got=$?
  if [ "$got" -ne "$2" ] || ! grep -qF "$3" "$WORK/$1.out"; then
    echo "FAIL  $1: exit $got (want $2)"; sed 's/^/      /' "$WORK/$1.out"; failures=$((failures + 1))
  else
    echo "ok    $1 (exit $got)"
  fi
}

{ deploy web "$SECRET"; deploy worker "$SECRET"; cron backup "$SECRET"; } > "$WORK/all.yaml"
{ deploy web "$SECRET"; deploy worker ""; cron backup "$SECRET"; } > "$WORK/worker-missing.yaml"
{ deploy web "$SECRET"; cron backup ""; } > "$WORK/cron-missing.yaml"

expect all-have-it 0 "3 pod specs all carry imagePullSecrets" "$WORK/all.yaml"
expect worker-missing 1 "no imagePullSecrets on Deployment/worker" "$WORK/worker-missing.yaml"
expect cronjob-missing 1 "no imagePullSecrets on CronJob/backup" "$WORK/cron-missing.yaml"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
