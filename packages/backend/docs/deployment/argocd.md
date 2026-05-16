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

### Staging — automatic sync

Har `main`'ga merge → CI build → image push → Argo detect drift → sync to
`yuzdanyuz-staging` namespace (auto, prune, selfHeal enabled).

### Production — manual sync from tag

```bash
# 1. Cut release tag
git tag v1.2.3
git push origin v1.2.3

# 2. Update prod app to point to tag
argocd app set yuzdanyuz-prod --revision v1.2.3

# 3. Diff + sync
argocd app diff yuzdanyuz-prod
argocd app sync yuzdanyuz-prod

# 4. Watch rollout
argocd app wait yuzdanyuz-prod --health
```

## Rollback

```bash
argocd app history yuzdanyuz-prod
argocd app rollback yuzdanyuz-prod <REVISION_ID>
```

## Limitations (TODO future PR)

- Image tag updates avtomatik emas — argocd-image-updater yoki PR-bot kerak
- Slack/Telegram notifications — `argocd-notifications-cm` separate config
- Multi-cluster — hozir bitta `kubernetes.default.svc`

To'liq yo'riqnoma: [`argocd/README.md`](../../../../argocd/README.md)
