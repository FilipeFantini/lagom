# infra/

Files Argo CD applies to the cluster. For the runtime design see
[Platform](../docs/platform.md). For checks and recovery follow the
[runbook](../docs/runbook.md). The data code lives under
[data/](../data/README.md).

| Path | Contents |
|---|---|
| `apps/` | Root and component Argo CD Applications |
| `argocd/`, `sealed-secrets/`, `local-path/`, `metrics-server/` | Reconciliation, secret decryption, node-local volumes and Pod metrics |
| `cloudnative-pg/`, `barman-cloud/`, `cert-manager/` | Postgres operator, backup plugin and certificate management |
| `postgres-prod/`, `postgres-staging/` | Database clusters, per-environment roles, sealed credentials and access policy |
| `dagster-prod/`, `dagster-staging/` | Orchestration deployment values and image pins |
| `cloudflared/` | Operator access tunnel, routing and network policy |
