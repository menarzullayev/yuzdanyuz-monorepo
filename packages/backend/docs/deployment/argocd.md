# Argo CD — GitOps

To'liq GitOps workflow: Argo CD reconciles Helm chart from `main` branch.

Manifestlar: [`argocd/`](../../../../argocd/)

```
argocd/
├── projects/yuzdanyuz.yaml        AppProject (RBAC + repo allowlist)
└── apps/
    ├── yuzdanyuz-staging.yaml      auto-sync from main
    └── yuzdanyuz-prod.yaml         manual sync from version tag
```

## Bootstrap (one-time)

```bash
# Pre-req: Argo CD installed in `argocd` ns
# https://argo-cd.readthedocs.io/en/stable/getting_started/

kubectl apply -f argocd/projects/yuzdanyuz.yaml
kubectl apply -f argocd/apps/yuzdanyuz-staging.yaml
kubectl apply -f argocd/apps/yuzdanyuz-prod.yaml

argocd app get yuzdanyuz-staging
argocd app get yuzdanyuz-prod
```

## Workflow

### Image tags (CI-gated, immutable)

- `build-and-push.yml` publishes images only for commits whose `CI` workflow passed:
  on `main` it runs after CI finished green (`workflow_run`); release tags (`v*.*.*`) and
  manual runs first check that "All Checks Passed" succeeded on the commit.
- Tags are `sha-<full git sha>` (plus `vX.Y.Z` for releases). There is no `latest` and no
  branch tag. The chart refuses an empty, `latest` or branch tag at render time
  (`templates/_helpers.tpl`, `yuzdanyuz.checkedTag`); CI checks that refusal.
- Each environment pins its tag in `argocd/apps/yuzdanyuz-<env>.yaml`
  (`spec.source.helm.parameters`, `image.tag`). nginx and backup images use the same tag.

```bash
# after the merge commit's images are pushed:
scripts/bump-image-tag.sh staging <merge commit sha>   # refuses unless CI was green and the image exists
git switch -c deploy/staging-<short sha> && git commit -am "chore(deploy): staging to <short sha>"
gh pr create ...   # the PR runs CI; after merge ArgoCD syncs staging
```

### Staging — automatic sync

After the bump PR is merged, Argo detects the drift and syncs the
`yuzdanyuz-staging` namespace (auto, prune, selfHeal enabled). A merge to `main` alone
no longer changes what staging runs.

### Production — manual sync

```bash
# 1. Pin the prod image (same script, same CI/image checks) in a PR and merge it
scripts/bump-image-tag.sh prod <sha>

# 2. Diff + sync
argocd app diff yuzdanyuz-prod
argocd app sync yuzdanyuz-prod

# 3. Watch rollout
argocd app wait yuzdanyuz-prod --health
```

## Rollback

```bash
argocd app history yuzdanyuz-prod
argocd app rollback yuzdanyuz-prod <REVISION_ID>
```

## Limitations (TODO future PR)

- The bump PR is opened by hand (`scripts/bump-image-tag.sh`): a PR opened with the
  workflow GITHUB_TOKEN would not trigger CI, so a bot needs its own token
- Slack/Telegram notifications — `argocd-notifications-cm` separate config
- Multi-cluster — hozir bitta `kubernetes.default.svc`

To'liq yo'riqnoma: [`argocd/README.md`](../../../../argocd/README.md)
