#!/usr/bin/env bash
# Proves scripts/check-dependabot-pip.py passes the real config and catches the
# directory that froze the pip dependency graph (/packages/backend/requirements).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-dependabot-pip.py"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

# expect <name> <0|1> <config>
expect() {
  local name="$1" want="$2" config="$3" got=0
  "$PY" "$CHECK" --root "$ROOT" --config "$config" > "$WORK/$name.out" 2>&1 || got=$?
  if [ "$got" -eq "$want" ]; then
    echo "ok    $name (exit $got)"
  else
    echo "FAIL  $name: exit $got, expected $want"; sed 's/^/      /' "$WORK/$name.out"
    failures=$((failures + 1))
  fi
}

expect "repo-config" 0 "$ROOT/.github/dependabot.yml"

sed 's#directory: /packages/backend$#directory: /packages/backend/requirements#' \
  "$ROOT/.github/dependabot.yml" > "$WORK/requirements-dir.yml"
grep -q 'directory: /packages/backend/requirements$' "$WORK/requirements-dir.yml"
expect "old-requirements-dir" 1 "$WORK/requirements-dir.yml"
grep -q 'drops base.txt, dev.txt, prod.txt' "$WORK/old-requirements-dir.out" \
  || { echo "FAIL  message does not name the dropped files"; failures=$((failures + 1)); }

printf 'version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /docs\n' > "$WORK/no-files.yml"
expect "dir-without-requirements" 1 "$WORK/no-files.yml"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
