#!/usr/bin/env bash
# Smoke-test a built backend image the way the Helm chart runs it: as the pod
# user from charts/yuzdanyuz/values.yaml (securityContext.runAsUser/runAsGroup),
# with the image's default settings module.
#
# Checks that the user has a home directory (the claude CLI writes ~/.claude),
# that Python finds the installed packages, that the claude CLI starts, and that
# an AI call with no CLAUDE_CODE_OAUTH_TOKEN fails fast with a login error
# instead of hanging or crashing the worker.
#
# Usage: scripts/smoke-backend-image.sh <image>
set -euo pipefail

image="${1:?usage: $0 <image>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
values="$root/charts/yuzdanyuz/values.yaml"

uid="$(sed -nE 's/^  runAsUser: ([0-9]+).*/\1/p' "$values")"
gid="$(sed -nE 's/^  runAsGroup: ([0-9]+).*/\1/p' "$values")"
if [ -z "$uid" ] || [ -z "$gid" ]; then
  echo "FAIL: runAsUser/runAsGroup not found in $values" >&2
  exit 1
fi
echo "image: $image"
echo "pod user (from values.yaml): $uid:$gid"

run() {
  docker run --rm --user "$uid:$gid" \
    -e SECRET_KEY=smoke-test-only -e CLAUDE_CLI_TIMEOUT=60 \
    "$@"
}

run --entrypoint sh "$image" -c '
  set -e
  if [ "$HOME" != /home/app ]; then
    echo "FAIL: HOME is \"$HOME\" for uid $(id -u): the pod user is not the image user" >&2
    exit 1
  fi
  touch "$HOME/.write-test" && rm "$HOME/.write-test"
  echo "home: $HOME (writable)"
  echo "claude: $(claude --version)"
'

check_py='
import django

django.setup()

from apps.intelligence import tasks
from core import claude_cli

try:
    tasks._generate_text("ping")
except claude_cli.ClaudeCLIError as e:
    assert "login" in str(e).lower(), f"unexpected error: {e}"
    print(f"no-token AI call fails fast: {e}")
else:
    raise SystemExit("FAIL: expected ClaudeCLIError without CLAUDE_CODE_OAUTH_TOKEN")
'
run --entrypoint python "$image" -c "$check_py"

echo "OK: $image runs as $uid:$gid"
