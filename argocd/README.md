# Argo CD — GitOps Deployment

GitOps source-of-truth for production: Argo CD watches this repo and reconciles
the `charts/yuzdanyuz` Helm chart into the cluster.

## Layout

```
argocd/
├── projects/
│   └── yuzdanyuz.yaml          AppProject — RBAC + sourceRepo + destination scoping
└── apps/
    ├── yuzdanyuz-staging.yaml   automated sync from main → yuzdanyuz-staging ns
    └── yuzdanyuz-prod.yaml      manual sync from tag      → yuzdanyuz-prod ns
```

## Bootstrap (one-time, per cluster)

Prerequisite: Argo CD installed in `argocd` namespace
([install guide](https://argo-cd.readthedocs.io/en/stable/getting_started/)).

```bash
# 1. Apply project (RBAC scoping)
kubectl apply -f argocd/projects/yuzdanyuz.yaml

# 2. Apply application(s)
kubectl apply -f argocd/apps/yuzdanyuz-staging.yaml
kubectl apply -f argocd/apps/yuzdanyuz-prod.yaml

# 3. Verify
argocd app get yuzdanyuz-staging
argocd app get yuzdanyuz-prod
```

## Workflow

### Staging — auto-sync from main

Every push to `main` is auto-deployed to staging:
1. CI builds + pushes Docker image to `ghcr.io/menarzullayev/yuzdanyuz-backend:<sha>`
2. Image tag in Helm values is updated (via `argocd-image-updater` or PR bot)
3. Argo CD detects drift, syncs Helm release to `yuzdanyuz-staging` namespace
4. Healthcheck (Argo `Healthy` status) gates downstream tests

### Production — tagged release, manual sync

```bash
# 1. Cut a release tag
git tag v1.2.3
git push origin v1.2.3

# 2. Update prod app to point to the tag
argocd app set yuzdanyuz-prod --revision v1.2.3

# 3. Review diff, then sync
argocd app diff yuzdanyuz-prod
argocd app sync yuzdanyuz-prod
```

## Rollback

```bash
# Find the last healthy revision
argocd app history yuzdanyuz-prod

# Roll back
argocd app rollback yuzdanyuz-prod <REVISION_ID>
```

## Secrets

Argo CD does **not** manage secrets — they are out-of-band:

```bash
# Create from .env (one-time per env)
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-secrets \
  --from-env-file=secrets/prod.env
```

See [docs/deployment/secrets.md](../packages/backend/docs/deployment/secrets.md)
for the full Secret schema and rotation procedure.

## Known limitations

- Image tag updates require an external trigger (argocd-image-updater or PR bot)
  — not implemented in this PR. For now, bump the tag in `values-prod.yaml`
  manually before tagging a release.
- Notifications (Slack on sync failure) — configure separately via
  `argocd-notifications-cm`.
