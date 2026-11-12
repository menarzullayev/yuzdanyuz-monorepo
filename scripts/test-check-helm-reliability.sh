#!/usr/bin/env bash
# Proves scripts/check-helm-reliability.py passes on the real prod render and names
# each broken guarantee when one is removed from it.
# Usage: test-check-helm-reliability.sh RENDERED.yaml
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-helm-reliability.py"
PY="${PYTHON:-python}"
SRC="${1:?usage: $0 rendered.yaml}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

# mutate <out> <python statement over `docs`>
mutate() {
  "$PY" - "$SRC" "$WORK/$1.yaml" "$2" <<'PY'
import sys, yaml
src, out, stmt = sys.argv[1:]
docs = [d for d in yaml.safe_load_all(open(src, encoding='utf-8')) if d]
comp = lambda d: (d['metadata'].get('labels') or {}).get('app.kubernetes.io/component', '')
exec(stmt)
yaml.safe_dump_all(docs, open(out, 'w', encoding='utf-8'))
PY
}

# expect <name> <0|1> <must-contain>
expect() {
  local got=0
  "$PY" "$CHECK" "$WORK/$1.yaml" > "$WORK/$1.out" 2>&1 || got=$?
  if [ "$got" -ne "$2" ] || ! grep -qF "$3" "$WORK/$1.out"; then
    echo "FAIL  $1: exit $got (want $2)"; sed 's/^/      /' "$WORK/$1.out"; failures=$((failures + 1))
  else
    echo "ok    $1 (exit $got)"
  fi
}

mutate as-rendered 'pass'
mutate worker-no-media '[c.pop("volumeMounts", None) for d in docs if d["kind"] == "Deployment" and comp(d) == "celery-worker-batch" for c in d["spec"]["template"]["spec"]["containers"]]'
mutate app-skips-pgbouncer '[d["data"].update(DB_HOST="postgres-primary") for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-env")]'
mutate session-pooling '[d["data"].update({"pgbouncer.ini": d["data"]["pgbouncer.ini"].replace("pool_mode = transaction", "pool_mode = session")}) for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-pgbouncer")]'
mutate migrate-through-pooler '[c.pop("env", None) for d in docs if d["kind"] == "Job" and comp(d) == "migrate" for c in d["spec"]["template"]["spec"]["containers"]]'
mutate nginx-no-static '[d["spec"]["template"]["spec"].pop("initContainers", None) for d in docs if d["kind"] == "Deployment" and comp(d) == "nginx"]'

expect as-rendered 0 "pgbouncer wiring, worker media mounts, nginx static"
expect worker-no-media 1 "celery-worker-batch: media PVC is not mounted"
expect app-skips-pgbouncer 1 "is not the PgBouncer Service"
expect session-pooling 1 "pool_mode is not transaction"
expect migrate-through-pooler 1 "migrate Job goes through PgBouncer"
expect nginx-no-static 1 "no copy-static initContainer"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
