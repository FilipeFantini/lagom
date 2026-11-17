# platform/infra/

Everything Argo CD applies to the cluster. **Nothing outside this directory can
change the cluster**, and nothing inside it should be applied by hand after
bootstrap — merge to `main` and Argo CD converges.

| Path | What it is |
|---|---|
| `apps/` | One Argo CD Application per component. `root.yaml` watches this directory, so adding a file here adds a component |
| `argocd/values.yaml` | Helm values for Argo CD itself, which it applies to itself |
| `sealed-secrets/values.yaml` | Helm values for the controller that decrypts sealed secrets |
| `local-path/` | The storage provisioner every Postgres volume uses, vendored from upstream with k3s's settings ([ADR-0021](../../docs/adr/0021-k3s-packaged-components-under-git.md)) |
| `metrics-server/values.yaml` | Helm values for metrics-server, which serves `kubectl top` |
| `cloudnative-pg/values.yaml` | Helm values for the operator that runs Postgres from `Cluster` objects |
| `barman-cloud/values.yaml` | Helm values for the Barman Cloud plugin, which backs up each Postgres `Cluster` to R2 ([ADR-0025](../../docs/adr/0025-postgres-backup-barman-cloud-to-r2.md)) |
| `cert-manager/values.yaml` | Helm values for cert-manager, which issues the plugin's TLS certificates |
| `postgres-prod/` | The prod database: one CloudNativePG `Cluster`. The operator generates its credentials in the cluster; nothing is committed. Its `NetworkPolicy` admits only `prod` and the operator, so staging cannot reach it. Backed up to R2 (`ObjectStore`, `ScheduledBackup`, the sealed R2 token). Dagster's role, database and sealed secrets; the catalog's read-only login `lake_reader` (its sealed password is added by the operator, runbook: *The catalog's read-only role*) |
| `postgres-staging/` | The staging database: a `Cluster` mirroring prod's, the developer's `lagom_dev` role and database, a ResourceQuota and LimitRange, the sealed credential for that role (no backup since ADR-0046), and Dagster's role, database and sealed secrets, and the same `lake_reader` login; since ADR-0047 the database `serving` and its roles, `serving` (its owner, dbt's), `events_writer` (Redpanda Connect's, insert only) and `grafana_reader` (Grafana's, the views of `metrics` only, refused every other database by `pg_hba`), with their sealed passwords; and a `NetworkPolicy` that admits to the Postgres Pod only `staging`, the operator and Grafana's Pod on port 5432 |
| `streaming-staging/` | The streaming showcase in staging ([ADR-0047](../../docs/adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md); [ADR-0050](../../docs/adr/0050-the-pi-streaming-runtime-and-private-hub-link.md)): the NATS hub (a StatefulSet with its 4Gi volume, the stream `EVENTS` declared in its ConfigMap, and the leaf listener on 7422 for the Pi), one Redpanda Connect process in streams mode (the synthetic producer and the writer to `serving`), the sealed `nats-credentials`, and each workload's NetworkPolicy (among Pods only Connect reaches the hub on 4222, and no Pod reaches Connect; the Pi leaf reaches 7422 from the node through Tailscale Serve, [ADR-0050](../../docs/adr/0050-the-pi-streaming-runtime-and-private-hub-link.md) decision 5) |
| `grafana/` | One Grafana for both environments, at `ops.ofantini.com/grafana` ([ADR-0047](../../docs/adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md), decision 7): the Deployment with its SQLite volume, its settings and data source (`config.yaml`; staging's `serving` only, as `grafana_reader`), the streaming showcase's dashboard (`dashboard-streaming.yaml`, one panel per question of ADR-0045's decision 5, each reading a view of `metrics`), the role's sealed password, and a NetworkPolicy that admits only `cloudflared`. No Grafana login: Access is the login, and everyone past it is a Viewer |
| `dagster-staging/values.yaml` | Helm values for staging's Dagster: daemon and the platform's code location (the webserver at zero replicas, no UI since ADR-0046), with its metadata in staging's Postgres ([ADR-0026](../../docs/adr/0026-dagster-one-instance-per-environment.md)). Its secrets (`dagster-db`, `r2-lake`) are sealed in `postgres-staging/`, which Argo CD applies as a directory |
| `dagster-prod/values.yaml` | Prod's Dagster, mirroring staging's file except for the environment's names and its bucket `lagom-prod-lake`; the image is staging's digest, promoted. Its secrets are sealed in `postgres-prod/` |
| `cloudflared/` | The tunnel that serves the operator's tools at `ops.ofantini.com/<tool>` behind Cloudflare Access: its routing rules (`config.yaml`, the whole list of what the internet can ask for), the Deployment, the sealed tunnel token, and the NetworkPolicies that let it reach prod's Dagster webserver and nothing reach it; Grafana's own policy, in `grafana/`, admits it ([ADR-0030](../../docs/adr/0030-operator-uis-behind-cloudflare-access.md)). The Access login policy is in Cloudflare's dashboard (runbook) |

How it fits together, and why: [ADR-0009](../../docs/adr/0009-argocd-bootstrap-and-self-management.md).
Bootstrap and recovery commands: the runbook in
[`docs/runbook.md`](../../docs/runbook.md).
