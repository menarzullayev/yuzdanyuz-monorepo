# Kubernetes — Helm Chart

`charts/yuzdanyuz` — production-ready Helm chart with HA, autoscaling, observability.

## Resurslar

| Resource                  | Replicas | Strategy        | Notes                              |
|---------------------------|----------|-----------------|------------------------------------|
| Deployment (web)          | 3-30     | RollingUpdate   | HPA on CPU/memory                  |
| Deployment (channels)     | 2-15     | RollingUpdate   | TCP probe (Daphne)                 |
| Deployment (celery-worker)| 2-N      | RollingUpdate   | terminationGracePeriod=300s (drain)|
| Deployment (celery-beat)  | 1        | Recreate        | Single-replica (no distributed lock)|
| Deployment (nginx)        | 2        | RollingUpdate   | Static + reverse proxy             |
| Service                   | 3        | ClusterIP       | web, channels, nginx               |
| Ingress                   | 1        | nginx + TLS     | cert-manager: letsencrypt-prod     |
| HorizontalPodAutoscaler   | 2        | v2              | web + channels                     |
| PodDisruptionBudget       | 1        | minAvailable=1  | web                                |
| NetworkPolicy             | 3        | default-deny + allow lists | DNS, PG, Redis, OTel, HTTPS|
| Job (migrate)             | 1        | Helm pre-install hook | runs migrate + collectstatic |
| CronJob (backup)          | daily    | WAL-G full base | opt-in via values.backup.enabled   |
| ServiceMonitor            | 1        | scrape /metrics | requires kube-prometheus-stack     |
| ConfigMap                 | 1        | env vars        | non-secret                         |
| ServiceAccount            | 1        | no token mount  | least privilege                    |

## Bootstrap

```bash
# 1. Namespace
kubectl create ns yuzdanyuz-prod

# 2. Secrets (out-of-band — see secrets.md)
kubectl -n yuzdanyuz-prod create secret generic yuzdanyuz-secrets \
  --from-env-file=secrets/prod.env

# 3. PVC for media (or use S3 + django-storages instead)
kubectl -n yuzdanyuz-prod apply -f - <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: yuzdanyuz-media
spec:
  capacity:
    storage: 20Gi
  accessModes: [ReadWriteMany]
  nfs:
    server: nfs.example.com
    path: /exports/yuzdanyuz/media
EOF

# 4. Install Helm release
helm install yuzdanyuz charts/yuzdanyuz \
  -n yuzdanyuz-prod \
  -f charts/yuzdanyuz/values.yaml \
  -f charts/yuzdanyuz/values-prod.yaml

# 5. Verify
kubectl -n yuzdanyuz-prod get pods,svc,ingress,hpa
```

## Upgrade

```bash
helm diff upgrade yuzdanyuz charts/yuzdanyuz \
  -n yuzdanyuz-prod \
  -f charts/yuzdanyuz/values.yaml \
  -f charts/yuzdanyuz/values-prod.yaml \
  --set image.tag=v1.2.3

helm upgrade yuzdanyuz charts/yuzdanyuz ...
```

## Rollback

```bash
helm history yuzdanyuz -n yuzdanyuz-prod
helm rollback yuzdanyuz <REVISION> -n yuzdanyuz-prod
```

## Per-environment overrides

| File                  | Differences                                         |
|-----------------------|-----------------------------------------------------|
| `values-dev.yaml`     | 1 replica, DEBUG logs, no TLS, no HPA, no backup   |
| `values-staging.yaml` | 2 replicas, full stack, daily backup                |
| `values-prod.yaml`    | 4-30 replicas, low traces sample (5%), full backup |

## Lokal lint + render

```bash
helm lint charts/yuzdanyuz \
  -f charts/yuzdanyuz/values.yaml \
  -f charts/yuzdanyuz/values-prod.yaml

helm template yuzdanyuz charts/yuzdanyuz \
  -f charts/yuzdanyuz/values.yaml \
  -f charts/yuzdanyuz/values-prod.yaml | less
```

CI'da avtomatik:
[`.github/workflows/build-and-push.yml`](../../../../.github/workflows/build-and-push.yml#helm-lint).
