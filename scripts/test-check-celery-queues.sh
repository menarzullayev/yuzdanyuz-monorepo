#!/usr/bin/env bash
# Proves scripts/check-celery-queues.py passes this repo and catches an unconsumed
# routed queue and an uncapped AI worker, in docker-compose and in Helm output.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-celery-queues.py"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

# fixture <name> <compose worker services yaml>: celery.py routes ai + critical
fixture() {
  local dir="$WORK/$1"
  mkdir -p "$dir/packages/backend/core" "$dir/docker"
  printf '%s\n' "app.conf.task_routes = {'a.score': {'queue': 'critical'}, 'a.tutor': {'queue': 'ai'}}" \
    > "$dir/packages/backend/core/celery.py"
  printf '%s\n' '-Q "${CELERY_QUEUES:-default,celery,critical}"' > "$dir/docker/entrypoint.sh"
  printf 'services:\n%s\n' "$2" > "$dir/docker-compose.yml"
  echo "$dir"
}

# manifest <file> <ai replicas> <ai concurrency> <keda max for ai, 0 = none>
manifest() {
  {
    printf 'kind: Deployment\nmetadata: {name: w}\nspec:\n  replicas: 1\n  template: {spec: {containers: [{args: [worker], env: [{name: CELERY_QUEUES, value: "default,celery,critical"}]}]}}\n'
    printf -- '---\nkind: Deployment\nmetadata: {name: w-ai}\nspec:\n  replicas: %s\n  template: {spec: {containers: [{args: [worker], env: [{name: CELERY_QUEUES, value: ai}, {name: CELERY_CONCURRENCY, value: "%s"}]}]}}\n' "$2" "$3"
    if [ "$4" -gt 0 ]; then
      printf -- '---\nkind: ScaledObject\nmetadata: {name: so-ai}\nspec: {scaleTargetRef: {name: w-ai}, maxReplicaCount: %s}\n' "$4"
    fi
  } > "$1"
}

# expect <name> <0|1> <must-contain> <args...>
expect() {
  local name="$1" want="$2" must="$3" got=0
  shift 3
  "$PY" "$CHECK" "$@" > "$WORK/$name.out" 2>&1 || got=$?
  if [ "$got" -ne "$want" ] || { [ -n "$must" ] && ! grep -qF "$must" "$WORK/$name.out"; }; then
    echo "FAIL  $name: exit $got (want $want), output:"; sed 's/^/      /' "$WORK/$name.out"
    failures=$((failures + 1))
  else
    echo "ok    $name (exit $got)"
  fi
}

GOOD_WORKERS='  worker: {command: [worker]}
  ai: {command: [worker], environment: {CELERY_QUEUES: ai, CELERY_CONCURRENCY: "2"}}'

expect "this-repo" 0 "" --root "$ROOT"
expect "compose-ok" 0 "AI parallel 2" --root "$(fixture ok "$GOOD_WORKERS")"
expect "compose-no-ai-worker" 1 "no worker consumes ai" \
  --root "$(fixture noai '  worker: {command: [worker]}')"
expect "compose-ai-uncapped" 1 "up to 4 parallel AI calls" \
  --root "$(fixture uncapped '  worker: {command: [worker]}
  ai: {command: [worker], environment: {CELERY_QUEUES: ai}}')"

manifest "$WORK/helm-ok.yaml" 1 2 1
manifest "$WORK/helm-keda10.yaml" 1 2 10
expect "helm-ok" 0 "" --root "$(fixture h1 "$GOOD_WORKERS")" --manifests "$WORK/helm-ok.yaml"
expect "helm-keda-max-10" 1 "up to 20 parallel AI calls" \
  --root "$(fixture h2 "$GOOD_WORKERS")" --manifests "$WORK/helm-keda10.yaml"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
