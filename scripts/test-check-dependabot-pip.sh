#!/usr/bin/env bash
# Proves scripts/check-dependabot-pip.py passes this repo and catches the layout
# that froze the pip dependency graph (base/dev/prod.txt with no manifest-named
# entry point), using throwaway git repos as fixtures.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$ROOT/scripts/check-dependabot-pip.py"
PY="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
failures=0

# fixture <name> <file=content>... : a git repo with requirements/ files staged
fixture() {
  local dir="$WORK/$1"; shift
  mkdir -p "$dir/requirements"
  git -C "$dir" init -q
  for spec in "$@"; do printf '%b' "${spec#*=}" > "$dir/requirements/${spec%%=*}"; done
  git -C "$dir" add -A
  echo "$dir"
}

# expect <name> <0|1> <root> [text the output must contain]
expect() {
  local name="$1" want="$2" root="$3" must="${4:-}" got=0
  "$PY" "$CHECK" --root "$root" > "$WORK/$name.out" 2>&1 || got=$?
  if [ "$got" -ne "$want" ]; then
    echo "FAIL  $name: exit $got, expected $want"; sed 's/^/      /' "$WORK/$name.out"
    failures=$((failures + 1))
  elif [ -n "$must" ] && ! grep -qF "$must" "$WORK/$name.out"; then
    echo "FAIL  $name: output lacks '$must'"; sed 's/^/      /' "$WORK/$name.out"
    failures=$((failures + 1))
  else
    echo "ok    $name (exit $got)"
  fi
}

BASE='base.txt=Django==5.2.19\nPillow==13.0.0\n'
DEV='dev.txt=-r base.txt\npytest==9.0.0\n'
PROD='prod.txt=-r base.txt\ngunicorn==23.0.0\n'

expect "this-repo" 0 "$ROOT"
expect "no-entry-point" 1 "$(fixture no-entry "$BASE" "$DEV" "$PROD")" \
  "drops base.txt, dev.txt, prod.txt"
expect "aggregator" 0 "$(fixture agg "$BASE" "$DEV" "$PROD" 'requirements.txt=-r prod.txt\n-r dev.txt\n')"
expect "aggregator-misses-dev" 1 "$(fixture partial "$BASE" "$DEV" "$PROD" 'requirements.txt=-r prod.txt\n')" \
  "drops dev.txt"
expect "several-entry-points" 1 "$(fixture several 'base-requirements.txt=Django==5.2.19\n' \
  'dev-requirements.txt=-r base-requirements.txt\npytest==9.0.0\n')" \
  "reports each shared package 2 times"
expect "aggregator-plain-names" 0 "$(fixture plain 'common.txt=Django==5.2.19\n' \
  'development.txt=-r common.txt\npytest==9.0.0\n' 'production.txt=-r common.txt\ngunicorn==27.0.0\n' \
  'requirements.txt=-r production.txt\n-r development.txt\n')"

if [ "$failures" -ne 0 ]; then
  echo "$failures case(s) failed"; exit 1
fi
echo "all cases passed"
