#!/usr/bin/env bash
# Post-deploy smoke test against the public edge (nginx / ingress), read-only.
#
# Checks: liveness and readiness, HTTP -> HTTPS redirect, the security headers the
# audit requires (HSTS, CSP, Permissions-Policy, X-Frame-Options, nosniff), a request
# id on every response, and that the API answers anonymous calls with 401 (not 403,
# so the web app refreshes an expired cookie).
#
# Usage: scripts/smoke-deploy.sh https://example.uz
#        scripts/smoke-deploy.sh http://127.0.0.1:8080 --forwarded-https   (local stack
#        behind no TLS: pretends the proxy terminated HTTPS)
set -uo pipefail

base="${1:?usage: $0 <base-url> [--forwarded-https]}"
base="${base%/}"
extra=()
[ "${2:-}" = "--forwarded-https" ] && extra=(-H "X-Forwarded-Proto: https")
fail=0

check() { # check <label> <condition-result 0|1> <detail>
  if [ "$2" -eq 0 ]; then echo "ok    $1"; else echo "FAIL  $1: $3"; fail=$((fail + 1)); fi
}

code() { curl -s -o /dev/null -w '%{http_code}' "${extra[@]}" "$@"; }

c=$(code "$base/health/"); check "liveness /health/" "$([ "$c" = 200 ]; echo $?)" "HTTP $c"
ready=$(curl -s "${extra[@]}" "$base/health/ready/")
echo "$ready" | grep -q '"status": *"ready"'; check "readiness /health/ready/" $? "$ready"

if [ ${#extra[@]} -gt 0 ] || [[ "$base" == https://* ]]; then
  plain="${base/https:/http:}"
  r=$(curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "$plain/api/auth/me/")
  [[ "$r" == 30[18]\ https://* ]]; check "plain HTTP redirects to HTTPS" $? "$r"
fi

headers=$(curl -s -D - -o /dev/null "${extra[@]}" "$base/api/auth/me/" | tr -d '\r')
status=$(echo "$headers" | head -1 | awk '{print $2}')
check "anonymous API call is 401" "$([ "$status" = 401 ]; echo $?)" "HTTP $status"
for h in 'Strict-Transport-Security: max-age=' 'Content-Security-Policy:' \
         'Permissions-Policy: camera=()' 'X-Frame-Options: DENY' \
         'X-Content-Type-Options: nosniff' 'X-Request-ID:'; do
  echo "$headers" | grep -qi "^$h"; check "header ${h%%:*}" $? "missing"
done

if [ "$fail" -ne 0 ]; then echo "$fail check(s) failed"; exit 1; fi
echo "all smoke checks passed"
