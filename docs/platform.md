# Platform

How the infrastructure under Lagom is built today: the node, the cluster, how
changes reach it, secrets, backups, environments, images, access, and the
memory budget. The data that runs on it is in [`data.md`](data.md); how to
operate it is in [`runbook.md`](runbook.md); why each choice was made, and
what it replaced, is in the ADRs ([`adr/`](adr/README.md)).

This is the part of the project the agents lead. See the [agent rules](../AGENTS.md) for how infrastructure changes are explained.

---

## Principles

1. **Ephemeral compute.** The scarce resource is resident memory, not tool
   size. Work that can run as a job starts, does its work and exits; only what
   must answer at any moment stays resident.
2. **Desired state in Git.** Argo CD reconciles managed objects with
   `main`. See [GitOps](#gitops--argo-cd) for deletion and bootstrap details.
3. **The resource budget is a contract.** Every container declares a memory
   request, a memory limit and a CPU request, and CI fails if the sum exceeds
   the ceiling ([Resource budget](#resource-budget)). The ceiling follows the
   node; the discipline does not: every resident process justifies its memory
   ([ADR-0011](adr/0011-memory-viability-on-4gb.md)).
4. **Defer with a trigger.** A piece enters when a concrete need appears, not
   because it is on a diagram
   ([Deliberately absent](#deliberately-absent-and-when-it-enters)).
5. **Nothing blocks a second developer.** Personal accounts, no shared
   credential, every change through a pull request.

## The node

| Resource | What it is |
|---|---|
| Hetzner CAX21, `lagom-shared-02` in `fsn1` | the one server: 4 ARM vCPU, 8 GB, Debian 13, no swap. It is the Kubernetes control plane and runs both environments ([ADR-0002](adr/0002-single-node-k3s-hetzner.md), [ADR-0020](adr/0020-move-to-the-8-gb-node.md)). Its price is the one the Hetzner Console shows |
| `ofantini.com`, registered and served by Cloudflare | the public entry: the site at the apex, the dashboards at `lagom.ofantini.com`, the operator's tools at `ops.ofantini.com` ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)) |
| Cloudflare R2 | object storage: the lake's files, the dashboards' pages, prod's Postgres backups. One bucket per purpose and environment |

**Outside the cluster, principle 2 does not hold yet.** The server, its
firewall, the Tailscale policy, the Cloudflare zone and its redirect rule,
Access application, R2 buckets and tokens, and the Healthchecks.io checks are
created by hand, by the [runbook](runbook.md). Argo CD reconciles only what runs inside the cluster.
Terraform takes them over by its trigger (below).

The provisioning settings (image, type, firewall, labels, name) are the ones
in the runbook's [*Provision the node*](runbook.md#provision-the-node).

## Kubernetes — k3s

k3s is certified Kubernetes in one process: same API, same manifests, same
`kubectl` ([ADR-0002](adr/0002-single-node-k3s-hetzner.md)). Three of its
defaults differ from a typical cluster:

| k3s default | Here |
|---|---|
| Traefik as ingress, servicelb to publish it | **disabled**: nothing uses an ingress, and servicelb published it on the public address ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)) |
| local-path storage and metrics-server as k3s add-ons | the same components, installed by Argo CD from Git with declared resources ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)). local-path pins a volume to the node: fine with one node |
| SQLite as the datastore | kept: no high availability, so no etcd |

CoreDNS is the only component k3s still installs itself. `k3s-server` runs
with `GOMEMLIMIT=900MiB`: without a limit the Go runtime lets its heap grow to
about twice its live size ([ADR-0011](adr/0011-memory-viability-on-4gb.md)).
Secrets are encrypted in the datastore with a key on the node.

## GitOps — Argo CD

**Reconciliation** is the loop the platform rests on. Every few minutes Argo
CD compares what `main` says should exist with what runs, and changes the
cluster until they match. A manual change is undone on the next pass
(`selfHeal`); an object removed from Git is deleted (`prune`), except where an
Application sets `prune: false` on purpose (the Postgres directories, so that
removing a file never deletes a database).

**App of apps.** The Application `root` watches `infra/apps/` and creates
every Application it finds there; each one points at a directory of
`infra/` or at a pinned Helm chart. Argo CD manages itself the same way, from
[`infra/argocd/values.yaml`](../infra/argocd/values.yaml), with no Dex (login),
no notifications and its server at zero replicas: the operator uses the CLI
with `--core` ([ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md),
[ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).
It reads this public repository anonymously over HTTPS, so there is no
repository credential to keep ([ADR-0017](adr/0017-public-repository-from-a-snapshot.md)).

No Application carries Argo CD's resources finalizer: deleting an Application
leaves what it created running, and a namespace made by `CreateNamespace` is
never pruned. Removing a component is therefore a merge plus a clean-up by the
operator (runbook: [*Deploy*](runbook.md#deploy)).

## Secrets — Sealed Secrets

A secret reaches Git only encrypted. `kubeseal` encrypts it on the operator's
machine with the controller's public key; only the controller in the cluster
holds the private key and turns the `SealedSecret` into a Secret. A sealed file
is bound to its name and namespace, so it cannot be replayed elsewhere
([ADR-0010](adr/0010-sealed-secrets.md)). Key renewal is off, and the key is
backed up in the operator's password manager: without it, a rebuilt cluster
cannot decrypt anything in Git.

Agents never handle a secret's value: sealing is the operator's step, and the
commands that would print or create one are denied to Claude Code
([CONTRIBUTING](../CONTRIBUTING.md#what-is-enforced-for-agents)).

## Postgres — CloudNativePG

An operator (a controller that runs a database for you) in `cnpg-system`
reconciles each committed `Cluster` object into a Postgres Pod, its volume,
Services and credentials. Roles and databases are declared in the same files
([ADR-0012](adr/0012-cloudnative-pg.md)). One `Cluster` per environment, one
instance each, PostgreSQL 18.

What each Postgres holds:

- the **DuckLake catalog**, the lake's metadata, in the database `lagom`
  ([data.md](data.md#storage--ducklake-on-r2));
- **Dagster's metadata** (run history, schedules, asset state) in the database
  `dagster` ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md));
- in staging, the developer's database `lagom_dev`
  ([ADR-0014](adr/0014-develop-against-staging.md));
- in `lagom`, the bronze, silver and gold schemas the retired CronJobs built,
  no longer updated ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)).

**Credentials.** The operator generates the `lagom` owner's password into the
Secret `postgres-app`; nothing about it is committed, and a rebuild generates a
new one, so consumers read the Secret and never cache it. There is no
superuser password (`enableSuperuserAccess: false`); administration goes
through the Pod's local socket. Other logins (`dagster`, `lagom_dev`,
`lake_reader`) use passwords from sealed Secrets.

**Isolation.** A NetworkPolicy on each Postgres admits only its own namespace
and the CloudNativePG operator, so staging cannot reach prod.

## Backups

The Barman Cloud plugin runs beside the operator and adds a sidecar to prod's
Postgres Pod, which ships every closed WAL segment (the write-ahead log, the
record of every change) and a daily base backup to the bucket
`lagom-prod-backups`, kept 14 days
([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md),
[ADR-0027](adr/0027-hourly-wal-archiving-and-14-day-prod-retention.md)).
cert-manager issues the TLS certificates the plugin and the operator use.

- **RPO** (how much may be lost): about one hour of writes, since a segment is
  closed at least hourly (`archive_timeout`). Pipeline data is rebuilt by a
  re-load; Dagster's run history of that hour is not.
- **Restore** into a new `Cluster` is tested (runbook:
  [*Backup and restore*](runbook.md#backup-and-restore)).
- **RTO** (how long a rebuild takes): a rebuild from Git plus a re-load took
  about 20 minutes once the server existed, in the Postgres pipeline drill ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)).
  A re-load brings back the last 30 days of USGS events, the limit the operator
  accepted ([ADR-0019](adr/0019-postgres-backup-deferred.md)).

Staging is not backed up: its data is rebuilt by loading the source again, and
prod's backup is the protection ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).

## Dagster's deployment

Each environment runs its own Dagster in its namespace, from a pinned Helm
chart and `infra/dagster-<env>/values.yaml`
([ADR-0026](adr/0026-dagster-one-instance-per-environment.md)). Resident Pods:

- the **daemon**, which evaluates schedules and sensors and starts queued runs;
- the **code server**, which loads the code location (the image
  `lagom-orchestration`) and answers the others;
- the **webserver**, the UI, in prod only; staging's is at zero replicas.

A **run** is a Kubernetes Job the daemon creates, with the code server's image
and the Secrets its job names; it is deleted an hour after it ends, and its
history stays in Postgres. **One run at a time per environment**, of any job
(`maxConcurrentRuns: 1`): the budget counts one run per environment, and the
budget check fails an Application that declares a run without that limit
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).

The UI has no login of its own, so reaching it is the access control: prod's
is at `ops.ofantini.com/dagster` behind Cloudflare Access
([Access channels](#access-channels)). The jobs themselves are in
[data.md](data.md#orchestration--dagster-jobs).

## Environments

Three, on one node ([ADR-0007](adr/0007-three-environments.md)):

| Environment | Where | What runs there |
|---|---|---|
| `dev` | the operator's machine, data in staging | dbt and dlt from local `uv` environments against the database `lagom_dev` in staging's Postgres, through `kubectl port-forward` over the tailnet; unit tests need no database ([ADR-0014](adr/0014-develop-against-staging.md)) |
| `staging` | namespace on the node | its own Postgres (no backup) and Dagster (no UI), the same jobs as prod on its own buckets and secrets; a ResourceQuota (2,432 MiB of memory requests, 4,608 MiB of limits, 10 Pods; the arithmetic is in [`quota.yaml`](../infra/postgres-staging/quota.yaml)) and a LimitRange |
| `prod` | namespace on the node | its Postgres (backed up), its Dagster with the UI, and the image staging ran, promoted by digest |

Staging is real, not nominal, because of four enforced conditions: its own
Postgres and credentials, a quota, a NetworkPolicy that keeps it from prod,
and separate secrets. Staging runs continuously, without a webserver. The
next scheduled run validates a change there. Recreate staging on demand
for structural validation ([ADR-0007](adr/0007-three-environments.md),
[ADR-0052](adr/0052-streaming-leaves-the-platform.md)). The accepted limit: neither environment
is isolated from node failure or from memory pressure on the node.

**Promotion is by image digest** ([ADR-0015](adr/0015-images-on-ghcr.md)).
Staging and prod each pin their own digest, so a new image reaches staging
through one pull request and prod through another.
[`tools/promotion_check.py`](../tools/promotion_check.py) fails a change whose
prod manifests pin an image without a digest, or a digest staging had not
pinned on `main` before. It cannot see whether staging's runs succeeded; the
operator confirms that before the prod pull request.
[`tools/manifest_policy.py`](../tools/manifest_policy.py) fails an Application
with an implicit `prune`, a chart not pinned to an exact version, a Git source
outside this repository, or an image without a tag or digest
([`tools/README.md`](../tools/README.md#manifest-policy-check)).

## Images and CI

One image, `ghcr.io/filipefantini/lagom-orchestration`, the Dagster code
location, built from the repository root by
[`images.yml`](../.github/workflows/images.yml) on a native arm64 runner
([`data/orchestration/Dockerfile`](../data/orchestration/Dockerfile)). It
bundles every source's code, the dbt project, dbt Charts and the boards. A pull
request builds and smoke-tests it; a push to `main` publishes it tagged with
the commit SHA and prints the digest. The image is multi-stage, pinned by
digest at the base, runs as UID 10001 on a read-only root filesystem, and
writes only under `/tmp` (an `emptyDir`). The first pull of a digest takes
minutes. Later runs reuse the node's copy.

The package is private: an anonymous pull is refused. Pods pull it with the
sealed `ghcr-pull` Secret of their namespace.

CI ([`ci.yml`](../.github/workflows/ci.yml)) runs on GitHub's machines and
costs nothing on the node. What each check covers, and which are required, is
in [CONTRIBUTING](../CONTRIBUTING.md#checks).

## Access channels

From most to least public. A new access request is answered by choosing one of
them, not by opening a port.

| Channel | Who | What it grants |
|---|---|---|
| **Public site and dashboards** (`ofantini.com`, `lagom.ofantini.com`) | anyone | static pages of public data; no path to the cluster |
| **Cloudflare Access** (`ops.ofantini.com/<tool>`) | the operator; later partners | a web UI behind a login, nothing to install |
| **Tailscale** | operators | the Kubernetes API and SSH to the node |

**Tailscale** is a private network between machines (a WireGuard mesh) with
rules by identity. The node is `tag:lagom-node`: operators reach its SSH and
API ports and nothing else, and the node can reach no one. The Hetzner
firewall has **no inbound rule**, so the node has no port open to the internet
(runbook: [*Tailscale ACL*](runbook.md#tailscale-acl-and-zero-inbound-ports)).

**Cloudflare Tunnel and Access.** `cloudflared`, a Pod in the cluster, dials
out to Cloudflare and keeps the connection open; requests come back down it, so
nothing listens on the node. Access is the login in front: a one-time PIN by
e-mail to the addresses in its policy. The tools share one hostname, one path
each; what the tunnel serves is the list of rules in
[`cloudflared/config.yaml`](../infra/cloudflared/config.yaml), anything else
gets a 404, and `cloudflared` also checks the token Access signs, so a request
that skipped the login is refused ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)).

**Node shell (SSH)** is not a channel but a break-glass path for the node
itself: personal accounts only, `sudo` for operators only, no root login, no
passwords, reachable only over Tailscale. Day-to-day work never needs it.

Public APIs are a future access channel. Restricting one to defined
endpoints allows authentication, request limits and narrow tokens at the
proxy. A direct database connection would expose the full SQL query engine
and its resource costs, so database access stays private.

## Resource budget

The node shows **7,741 MiB** of memory to the OS. Two budgets share it, kept
apart because they are enforced differently.

**Node overhead outside Kubernetes: 1,687 MiB** (the planning figure in
[ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)). It uses resident set
size (RSS), which counts shared pages per process and errs high.

**Pod requests — the CI ceiling.**
**Ceiling enforced in CI: 5.5 GB of declared requests.** (The check reads GB
as GiB: 5,632 MiB.) The arithmetic: 7,741 visible − 1,687 outside Kubernetes
leaves ~6,050 MiB for Pods; the ceiling sits ~420 MiB under that, for k3s's
growth over days and the shims of new Pods, which requests do not count
([ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)).

**How CI enforces it.** [`tools/resource_budget.py`](../tools/resource_budget.py)
renders every Application the way Argo CD does and fails if:

- any container lacks a memory request, a memory limit or a CPU request (for a
  CloudNativePG `Cluster`, whose Pods the operator creates, its
  `spec.resources` and `spec.instances` stand in, plus the backup sidecar
  declared on its `ObjectStore`);
- an Application declares run Pods with the annotation
  `lagom.dev/ephemeral-memory-request` but does not limit runs to one at a
  time;
- the total exceeds the ceiling, which it reads from the sentence above.

The total is resident requests, plus **every ephemeral job at once** (the safe
error for a memory gate: it overstates jobs whose schedules never meet), plus
an allowance for what k3s installs outside Git (CoreDNS's 70 MiB request).
CPU limits are not set: CPU is compressible, so a container over its request
runs slower rather than being killed
([engineering protocol §16.5](engineering-protocol.md#165-resource-budget-as-a-quality-gate)).

**Today: 4,734 MiB, 898 under the ceiling.** By component, as the check counts
them (requests in MiB; how each was set from measurement is in the ADR named):

| Component | Request | Basis |
|---|---|---|
| Argo CD: controller 384, repo-server 128, redis 32 | 544 | controller with `GOMEMLIMIT` 540MiB, limit 640; server and ApplicationSet at zero ([ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md), [ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)) |
| CoreDNS 70 (outside Git), metrics-server 80, local-path 64 | 214 | [ADR-0021](adr/0021-k3s-packaged-components-under-git.md) |
| Sealed Secrets controller | 32 | [ADR-0010](adr/0010-sealed-secrets.md) |
| CloudNativePG operator | 64 | `GOMEMLIMIT` 150MiB, limit 192 ([ADR-0012](adr/0012-cloudnative-pg.md)) |
| Barman Cloud plugin 32; cert-manager controller 48, cainjector 48, webhook 32 | 160 | each with `GOMEMLIMIT` ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md), [ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)) |
| Postgres prod: 300 + backup sidecar 64 | 364 | limit 512; `shared_buffers` 96MB, `max_connections` 25, instance manager `GOMEMLIMIT` 96MiB ([`cluster.yaml`](../infra/postgres-prod/cluster.yaml)) |
| Postgres staging | 300 | the same settings, no sidecar |
| Dagster prod: webserver 160, daemon 320, code server 320 | 800 | [ADR-0026](adr/0026-dagster-one-instance-per-environment.md); the daemon grows with uptime and is watched |
| Dagster staging: daemon 320, code server 320 | 640 | no webserver |
| `cloudflared` | 48 | limit 128, `GOMEMLIMIT` 100MiB ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)) |
| Ephemeral: one Dagster run per environment (768 each), the Argo CD chart's hook Job (32) | 1,568 | a run's limit is 1 GiB; its peak on real data is in [data.md](data.md#memory-of-a-run) |
| **Total** | **4,734** | of 5,632 |

Staging requests 1,708 MiB of its 2,432 MiB quota (Postgres 300, Dagster 640,
its run 768). The room under the ceiling is the margin the node allows, not a
budget to fill (principle 3). A new batch job takes turns in the one run slot,
so it adds no request.

## Accepted risks

- **No high availability.** One node; maintenance takes everything down. A
  choice, not an oversight; the trigger for a second server is a real client.
- **No swap, on purpose.** With swap the node does not die, it becomes slow
  enough to look dead. Capped Pod memory and a quick, explicit OOM kill are
  easier to diagnose.
- **Scaling.** Vertical is a Hetzner resize and one reboot. Horizontal needs
  etcd instead of SQLite (three nodes) and storage that is not pinned to a node.
- **Full DuckLake recovery is untested.** The Postgres-era drill tested a
  node rebuild and a database restore. The current
  [rebuild procedure](runbook.md#disaster-recovery) includes the lake files
  and their catalog. Neither a catalog restore nor a reload from USGS has
  been exercised end to end on a replacement DuckLake node.
- **Some gates hold by convention for one token.** A change to a gate waits for
  the operator's approval of the `guardrail` environment, which no agent's
  token can give, and the reviewer runs from `main`. The Mac session's token
  can, as an administrator, edit rulesets and environments; for it the gates
  hold by convention ([CONTRIBUTING](../CONTRIBUTING.md#branch-protection)).
- **Scanners find known defects, not intent.** Dependabot reports published
  vulnerabilities and CodeQL reports findings
  ([ADR-0048](adr/0048-dependency-and-code-scanning-for-the-public-repository.md));
  harmful code that looks ordinary passes both. The reviewer and the
  operator's approval of each pin are what stop it reaching the node.

## Deliberately absent, and when it enters

| Piece | Enters when |
|---|---|
| A second server (real environment isolation, or high availability) | a real client |
| Terraform for what is outside the cluster (server, firewall, Tailscale policy, Cloudflare DNS, R2) | the next node is created or rebuilt. Deferred by the operator: the state backend is the choice that needs its ADR; existing resources are adopted with `import` ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)) |
| Infrastructure observability (metrics and logs) | the USGS showcase is published, and this follow-up is prioritized in the Project. Measure the candidate metrics and logs stack before adoption ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)) |
| PgBouncer (a CloudNativePG `Pooler`) | the first external BI consumer: each Postgres connection costs 5–10 MB, and BI tools open many |
| Short-lived credentials (a secret store or workload identity), and the writers' credentials split by layer | a real client, or a second team on the cluster ([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md) built the first part) |
| Automated secret rotation | its mechanism is chosen in an ADR; secrets are sealed by hand today ([ADR-0010](adr/0010-sealed-secrets.md)) |
| An in-cluster policy engine (Kyverno, Gatekeeper) | a second writer to the cluster besides Argo CD ([ADR-0013](adr/0013-agent-driven-development-guardrails.md)) |
| Automated staging digest bump | manual bumps become routine; its pull requests pass the same checks and reviewer ([ADR-0015](adr/0015-images-on-ghcr.md)) |
| Jobs without an image of their own | GitHub announces billing for Container registry storage or bandwidth ([ADR-0015](adr/0015-images-on-ghcr.md)) |
| A GitHub account or App for agents, apart from the operator's token | a second developer, an agent action that must be attributed apart, or a runner connector that accepts an App ([ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md)) |
| Staging folders out of the guarded list | an Argo CD AppProject confines staging's Applications to their namespace ([ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md)) |
| Per-developer RBAC | a second developer: the operator's kubeconfig is cluster-admin and is not shared |

Status of the work in progress is in the
[roadmap project](https://github.com/users/FilipeFantini/projects/2).
