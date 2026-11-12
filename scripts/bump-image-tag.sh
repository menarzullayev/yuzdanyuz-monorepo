#!/usr/bin/env bash
# Pin the image an environment deploys (audit H02).
#
#   scripts/bump-image-tag.sh <staging|prod> <git sha>
#
# Refuses unless the commit's CI aggregator ("All Checks Passed") succeeded and the
# backend image sha-<full sha> exists in GHCR, then rewrites image.tag in
# argocd/apps/yuzdanyuz-<env>.yaml. Commit that change in a PR: the PR's own CI runs,
# and after merge ArgoCD (staging: auto-sync; prod: manual sync) rolls it out.
set -euo pipefail

env="${1:?usage: $0 <staging|prod> <git sha>}"
ref="${2:?usage: $0 <staging|prod> <git sha>}"
root="$(cd "$(dirname "$0")/.." && pwd)"
app="$root/argocd/apps/yuzdanyuz-$env.yaml"
[ -f "$app" ] || { echo "no such environment: $app" >&2; exit 1; }

sha="$(git -C "$root" rev-parse --verify "$ref^{commit}")"
repo="${GITHUB_REPOSITORY:-menarzullayev/yuzdanyuz-monorepo}"
owner="${repo%%/*}"
tag="sha-$sha"

ci="$(gh api "repos/$repo/commits/$sha/check-runs?check_name=All%20Checks%20Passed" \
  --jq '[.check_runs[].conclusion] | if length == 0 then "missing" else (if all(. == "success") then "success" else "failure" end) end')"
if [ "$ci" != "success" ]; then
  echo "refused: CI 'All Checks Passed' on $sha is '$ci'" >&2
  exit 1
fi

if ! gh api "users/$owner/packages/container/yuzdanyuz-backend/versions" --paginate \
  --jq '.[].metadata.container.tags[]' | grep -qx "$tag"; then
  echo "refused: ghcr.io/$owner/yuzdanyuz-backend:$tag does not exist (build-and-push not finished or failed)" >&2
  exit 1
fi

python3 - "$app" "$tag" <<'PY'
import re, sys
path, tag = sys.argv[1], sys.argv[2]
text = open(path, encoding='utf-8').read()
new, n = re.subn(r'(- name: image\.tag\n\s+value: )\S+', r'\g<1>' + tag, text)
if n != 1:
    sys.exit(f'expected exactly one image.tag parameter in {path}, found {n}')
open(path, 'w', encoding='utf-8', newline='\n').write(new)
PY
echo "pinned $env to $tag in ${app#"$root"/}"
