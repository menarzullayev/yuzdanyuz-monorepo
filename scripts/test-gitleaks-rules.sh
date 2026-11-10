#!/usr/bin/env bash
# Proves the custom rules in .gitleaks.toml catch Redis/password leaks
# (positive cases) and ignore env-var and <placeholder> values (negative cases).
#
# Fixtures are generated at runtime and the fake secret is assembled from parts,
# so this file itself never matches the rules it tests.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$ROOT/.gitleaks.toml"
GITLEAKS="${GITLEAKS:-gitleaks}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fake="Tr0ub4dor""Horse""77"
failures=0

# expect_rule <name> <rule-id> <file> <content>
expect_rule() {
  local name="$1" rule="$2" file="$3" content="$4" dir="$WORK/$1"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/$file"
  if "$GITLEAKS" dir "$dir" --config "$CONFIG" --no-banner --redact \
       --report-format json --report-path "$dir.json" >/dev/null 2>&1; then
    echo "FAIL  $name: no finding (expected $rule)"; failures=$((failures + 1))
  elif grep -q "\"RuleID\": \"$rule\"" "$dir.json"; then
    echo "ok    $name: caught by $rule"
  else
    echo "FAIL  $name: caught, but not by $rule"; failures=$((failures + 1))
  fi
}

# expect_clean <name> <file> <content>
expect_clean() {
  local name="$1" file="$2" content="$3" dir="$WORK/$1"
  mkdir -p "$dir"
  printf '%s\n' "$content" > "$dir/$file"
  if "$GITLEAKS" dir "$dir" --config "$CONFIG" --no-banner --redact >/dev/null 2>&1; then
    echo "ok    $name: no finding"
  else
    echo "FAIL  $name: unexpected finding"; failures=$((failures + 1))
  fi
}

# Positive cases: each mirrors a shape the leaked password actually had.
expect_rule redis-url       redis-url-password  docker-compose.yml "    REDIS_URL: redis://:${fake}@redis:6379/1"
expect_rule requirepass-cfg redis-requirepass   redis.conf         "requirepass ${fake}"
expect_rule requirepass-list redis-requirepass  docker-compose.yml "$(printf '    command:\n      - redis-server\n      - --requirepass\n      - %s' "$fake")"
expect_rule redis-cli-a     redis-cli-password  docker-compose.yml "      test: [\"CMD\", \"redis-cli\", \"-a\", \"${fake}\", \"ping\"]"
expect_rule runbook-inline  inline-password     runbook.md         "- Redis: localhost:6379, password=${fake}"

# Negative cases: env-var references and placeholders must stay quiet.
expect_clean env-url        docker-compose.yml '    REDIS_URL: redis://:${REDIS_PASSWORD:-yuzdanyuz_dev_redis}@redis:6379/1'
expect_clean env-list       docker-compose.yml "$(printf '      - --requirepass\n      - ${REDIS_PASSWORD:-yuzdanyuz_dev_redis}')"
expect_clean placeholder-md runbook.md         '- Redis: localhost:6379, password=<redis-password>, url redis://:<redis-password>@<host>:6379'
expect_clean code-variable  login.py           'user = authenticate(request, username=login_val, password=password)'
expect_clean md-code-expr   SECURITY.md        "        password = request.POST.get('password')"
expect_clean md-fixture     TESTING.md         "        password='testpass123'"
expect_clean env-template   DEPLOYMENT.md      'CELERY_BROKER_URL=redis://:secure_redis_password@127.0.0.1:6379/1'
# The template allowlist must not swallow a real-looking value with the same prefix.
expect_rule env-template-real redis-url-password DEPLOYMENT.md   "CELERY_BROKER_URL=redis://:secure${fake}@127.0.0.1:6379/1"
# The doc allowlist must not swallow a real-looking password in Markdown.
expect_rule md-real         inline-password     TESTING.md         "        password='${fake}'"

if [ "$failures" -ne 0 ]; then
  echo "$failures gitleaks rule check(s) failed"; exit 1
fi
echo "All gitleaks rule checks passed"
