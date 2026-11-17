# Roadmap

Execution order and entry triggers. Read before adding a component — several are
deliberately absent.

**Execution rule:** walking skeleton before any data source. Make the whole loop
work with the minimum, then thicken each piece.

**Two tiers** ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed): the **SME stack** — DuckDB on DuckLake, Postgres
as its catalog, data on R2 — and, later, a big-data stack for comparison.
**The platform is a portfolio of data architectures** ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)): each
*showcase* is one architecture end to end on the shared backbone, published as
static pages and dashboards on ofantini.com. A component enters because a
showcase needs it.

**Principle 7 — defer with a trigger.** The full design in
[`architecture.md`](architecture.md) is the destination, not the starting point.
The core of M1–M3 is eight pieces: k3s, Argo CD, Tailscale, Sealed Secrets, dlt,
dbt, Postgres, Dagster.

## Trigger table

| Piece | Enters when |
|---|---|
| `staging` namespace | M2 — once there is something to promote |
| Postgres backup | **entered 2026-10-28** ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)). The trigger was: Postgres holds state no source can rebuild (Dagster's metastore in M3, a non-replayable source in M6), or someone other than the operator relies on the database itself — the site does not: it serves pages built after each run — [ADR-0019](adr/0019-postgres-backup-deferred.md); accepted as debt, [architecture.md §12](architecture.md#12-accepted-risks) |
| Object storage (Cloudflare R2) | the SME-stack spike (M3), as the store of DuckLake's data files ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed) |
| DuckDB and DuckLake (the SME stack) | the spike in M3; the warehouse moves from Postgres when it passes ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed; until then [ADR-0008](adr/0008-postgres-for-all-layers.md)). USGS already runs on DuckLake under Dagster ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md)), and the Postgres CronJobs were retired with Dagster's alarm on 2026-11-07, before the spike closes ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)); if the spike fails, ADR-0023's reversal applies |
| Second VPS (real environment isolation) | a real client |
| Terraform for resources outside the cluster (Hetzner server and firewall, Tailscale ACL, Cloudflare DNS, R2 and Pages) | the next node created or rebuilt. **Deferred by the operator on 2026-10-28**, after the move to `lagom-shared-02` had been planned as its entry: it adds work outside the data showcases, and the candidate state backend, HCP Terraform's free plan, is one more freemium vendor. The CLI is free; the state backend is the choice that needs its ADR. Existing resources are adopted with `import`, which records them without recreating them, so `lagom-shared-02` can still be brought under code later ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)) |
| dbt Charts (the dashboards) | M4 — the dashboard gallery on ofantini.com ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)); replaced Evidence on 2026-11-10 ([ADR-0040](adr/0040-dbt-charts-for-the-dashboards.md)). Its static page is the target again since 2026-11-14 ([ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md)) |
| The `serving` database | The first server resident on the node that answers visitors; not needed by a static page ([ADR-0041](adr/0041-a-serving-layer-in-postgres-for-the-dashboards.md), amended by [ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md)). In M6 it is created to hold the streaming showcase's events ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md), accepted) |
| Cloudflare Access (login) | **entered 2026-11-05** with the first content that must not be public: the operator's own UIs, Dagster's first, at `ops.ofantini.com/<tool>` ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)). Partners' reports will use the same path, each with its own policy. The site and its showcases are public data and need no login |
| NATS | second consumer of the same stream, or a replay requirement. The streaming showcase (M6) meets it ([ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md), proposed) |
| Streamlit | an interactive app a static page cannot serve |
| Infrastructure observability (metrics and logs), shown on ofantini.com | the USGS showcase is live on ofantini.com (M4): the platform delivers data first, then shows how it runs. Candidate stack, chosen for resident memory and confirmed by its own ADR when it enters: VictoriaMetrics single-node (scrapes and stores, PromQL-compatible), node-exporter and kube-state-metrics as sources, VictoriaLogs for logs; Grafana only for the operator, over the tailnet. The site gets static snapshots exported after each run, like every showcase, never a live dashboard reaching the cluster ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)). Expected, not yet measured: lower resident memory than Prometheus and a simpler log store than Loki; the ADR measures the candidate before accepting it |
| PgBouncer (CloudNativePG `Pooler`) | first external BI consumer |
| Credentials per job, and short-lived ones (ingestion writes only bronze, dbt only the layers it builds; a secret store or workload identity in place of static secrets) | a real client, or a second team on the cluster. **Entered early and in part, on 2026-11-14,** by the operator's decision, for the dashboards' render ([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)): each job declares the Secrets its Pod gets, a read-only catalog role exists, and the dashboards' render holds only read-only credentials and its bucket's token. Still open: `usgs` and `lake_maintenance` hold the catalog owner's password and the R2 write token (not split into bronze-only and dbt-only), every run Pod still holds the alarm's ping URL and Dagster's own database password, and the credentials are static |
| Row Level Security | second consumer with segregated data |
| PostgREST | first API requirement |
| Metabase, Superset or Lightdash | a client needs ad-hoc, self-service exploration — behind login. Lightdash 3.116.0 measured locally on 2026-11-14: about 1.1 GiB at rest, an amd64-only image, embedding under a commercial licence ([ADR-0043](adr/0043-the-dashboards-filter-in-the-browser.md)) |
| 8 GB node | **decided 2026-10-27** for M3 ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)); obtained 2026-10-28 as a new node in another location, `lagom-shared-02`, and the platform moved to it the same day |
| Big-data stack (Apache Iceberg, Spark or Trino, Airflow) | later, as the comparison that shows where the SME stack stops; infrastructure sized for it and its own ADR ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed) |
| In-cluster policy engine (Kyverno, Gatekeeper) | a second writer to the cluster besides Argo CD — [ADR-0013](adr/0013-agent-driven-development-guardrails.md) |
| Automated staging digest bump (bot commit after a build) | manual staging bumps become routine; its pull requests pass the required checks and a reviewer like any agent's ([ADR-0029](adr/0029-agents-on-more-than-one-harness.md)) — [ADR-0015](adr/0015-images-on-ghcr.md) |
| Run jobs without an own image (official `uv` image, clone at run time) | GitHub announces billing for Container registry storage or bandwidth — [ADR-0015](adr/0015-images-on-ghcr.md) |
| A GitHub account of its own for agents | a second developer (M7), or an agent action that must be attributed apart from the operator; one created on 2026-11-07 was suspended by GitHub, and [ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md) works without it — [ADR-0029](adr/0029-agents-on-more-than-one-harness.md) |
| Staging folders out of the guarded list (autonomous staging deploys) | an Argo CD AppProject confines staging's Applications to their namespace and no cluster-scoped kind — [ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md) |
| A GitHub App instead of the operator's token in the runner | the runner's connector accepts a GitHub App, or agents need an identity apart from the operator's — [ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md) |

Re-introducing a practice excluded in
[`engineering-protocol.md`](engineering-protocol.md) §17 requires an ADR.

## M0 — Accounts and provisioning

- [x] Confirm pricing and CAX11 availability in the Hetzner Console, and record
      the real figure in [ADR-0002](adr/0002-single-node-k3s-hetzner.md) —
      available in `nbg1`, EUR 7.37/month as shown in the Console
- [x] Create the VPS with the settings below — `lagom-shared-01`, `nbg1`;
      steps and verification in the
      [runbook](runbook.md#provision-the-node-m0)
- [x] Point the domain at Cloudflare — `getlagom.app`, registered with
      Cloudflare Registrar, so registrar and DNS are the same account and there
      was nothing to repoint. Replaced on 2026-11-07 by `ofantini.com`, also
      registered at Cloudflare ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md))
- [x] Create the GitHub repository — private at first. It is meant to become
      public as a portfolio, where readable infrastructure is worth more than a
      CV bullet; going public is a deliberate step taken by the operator, since
      published history cannot be withdrawn
- [x] Publish it — as a new public repository whose history starts at a
      snapshot; the private history is kept in an archive
      ([ADR-0017](adr/0017-public-repository-from-a-snapshot.md))

### Provisioning settings

| Field | Value | Why |
|---|---|---|
| Location | `nbg1` (Nuremberg) for `lagom-shared-01`; `fsn1` (Falkenstein) for `lagom-shared-02` | EU, Cost-Optimized line available; the CAX21 was not available as a resize in `nbg1` ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)) |
| Image | **Debian 13** | no snapd, less aggressive auto-upgrade; ~150 MB lighter resident than Ubuntu, which is ~6% of the job budget |
| Type | CAX11 (2 ARM vCPU, 4 GB) for `lagom-shared-01`; CAX21 (4 ARM vCPU, 8 GB) since `lagom-shared-02` | [ADR-0002](adr/0002-single-node-k3s-hetzner.md), [ADR-0020](adr/0020-move-to-the-8-gb-node.md). ARM, because the images are built for arm64 only |
| IPv4 | yes | IPv6-only breaks registries and endpoints that still lack v6 |
| IPv6 | yes | free |
| Private network | no | only links Hetzner servers; the Pi joins over Tailscale |
| SSH key | existing ed25519 (1Password) | password auth never enabled; root gets the key but cannot use it |
| Cloud config | [runbook](runbook.md#provision-the-node-m0) | creates a personal sudo user, disables root and password login |
| Volume | no | 40 GB local is enough for megabytes in Postgres; disk pressure is a lakehouse trigger ([ADR-0008](adr/0008-postgres-for-all-layers.md)) |
| Firewall | yes — inbound port 22 from the operator's IP only, until Tailscale is verified; then **no inbound rules** | runs outside the VM, so no RAM cost. **Never expose 6443.** Tailscale and Cloudflare Tunnel are outbound, so inbound drops to zero ([runbook](runbook.md#tailscale-acl-and-zero-inbound-ports-m1)) |
| Backups | no | VM rollback is not needed: the machine is reproducible from Git. Postgres is backed up on its own, to R2, since 2026-10-28 ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)) |
| Placement group | no | only spreads multiple servers across physical hosts |
| Labels | `project=lagom`, `env=shared`, `role=k3s-server` | free, and saves effort in the CLI and the invoice |
| Name | `lagom-shared-01`, then `lagom-shared-02` | `<project>-<environment>-<index>`. The node hosts both the prod and the staging namespace ([ADR-0007](adr/0007-three-environments.md)), so it is `shared`; `lagom-prod-01` is left for a dedicated prod node |

**Cloud-init hardens SSH only — it does not install k3s.** Installing the
cluster from user-data would hide the mechanism this milestone exists to teach.
No swap, deliberately: on this node swap turns an OOM into undiagnosable
slowness.

Half an hour. The only code is the cloud-config, kept in the runbook.

## M1 — The empty GitOps loop

VPS with k3s, Tailscale, Argo CD, and a trivial `nginx` deployed **only via
commit**. No data yet.

Goal: prove that changing a YAML file on GitHub makes the cluster converge on
its own.

- [x] k3s on the VPS — `v1.36.5+k3s1`, see the
      [runbook](runbook.md#install-k3s-m1)
- [x] Tailscale on the node and the operator's machine, `kubectl` over the
      tailnet
- [x] Tailscale ACL: the node is `tag:lagom-node`; operators reach only SSH
      and the API, and the node reaches no one
- [x] Public port 22 closed — zero inbound ports on IPv4 and IPv6; break-glass
      in the [runbook](runbook.md#tailscale-acl-and-zero-inbound-ports-m1)
- [x] Decide whether the 2.6 GB request ceiling stands — it does; memory
      viability measured in a spike, with the 8 GB fallback criteria —
      [ADR-0011](adr/0011-memory-viability-on-4gb.md)
- [x] Confirm `k3s-server` stays bounded over 24 h with `GOMEMLIMIT` — 761 MiB
      RSS after 67 h on 2026-10-16, against 1,235 MB after 17 h without it
- [x] Argo CD (no Dex) watching `platform/infra/` —
      [ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md)
- [x] Sealed Secrets configured — [ADR-0010](adr/0010-sealed-secrets.md):
      installed, a sealed secret decrypted in the cluster, the same file refused
      in another namespace, and the sealing key backed up to 1Password
- [x] nginx deployed via commit — a merged change reached the page in ~1.5
      minutes with no manual command; see the runbook's Deploy section
- [x] Resource budget check in CI — `tools/resource_budget.py`, see
      [architecture.md](architecture.md#11-resource-budget) §11
- [x] Memory limits on k3s's own components — not through `HelmChartConfig`,
      which reaches Traefik only: Traefik and servicelb disabled (nothing used
      them), local-path and metrics-server moved to Argo CD with requests and
      limits, CoreDNS kept (it declares both)
      ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md))
- [x] PR workflow active from the first commit (only `main` is watched by Argo
      CD) — enforced by the ruleset on `main` since 2026-10-27; see
      [CONTRIBUTING](../CONTRIBUTING.md#branch-protection)
- [x] Runbook: bootstrap and deploy sections

## M2 — One dataset end to end

A single source, the simplest available: **USGS** (United States Geological
Survey) earthquake feed — public GeoJSON, refreshed every few minutes, no API
key, no practical rate limit, ready-made windows (past hour, past day, past 30
days). Each event carries magnitude, depth, coordinates, timestamp, and place.

It is the right source for this milestone because there is no authentication to
debug, the schema is stable, and the volume is small — the focus stays on the
conveyor, not the source.

Full path: **dlt** -> Postgres bronze -> `dbt run` -> silver and gold in the
same Postgres. Still no Dagster, only a CronJob. No object storage — see
[ADR-0008](adr/0008-postgres-for-all-layers.md).

Done when: a `SELECT` on gold returns rows, and reconciliation between layers
passes.

- [x] dlt pipeline for USGS (`rest_api` source, incremental) — first real
      load 2026-10-17: 10,168 events (30 days) into `lagom_dev` in 15 s
- [x] Verify whether dlt source config can live in separate YAML — it can: the
      whole `rest_api` block is in `services/usgs/ingestion/source.yaml`. One
      thing cannot be written in YAML, a Python callable (here `convert`, which
      turns the cursor into the ISO date the API wants); the entry point adds
      it. [ADR-0003](adr/0003-ingestion-split-dlt-benthos.md) is history and is
      not edited
- [x] CloudNativePG operator, memory measured — 31 MiB reconciling one
      Cluster; [ADR-0012](adr/0012-cloudnative-pg.md)
- [x] Postgres `Cluster` for prod (no PgBouncer yet); budget check counts the
      `Cluster`'s resources, since its Pods are not in Git — PostgreSQL 18.6,
      47 MiB idle; runbook section in [runbook.md](runbook.md#postgres-m2)
- [x] Bronze in Postgres via dlt; set `max_table_nesting` against the real
      payload — on the real USGS payload a column hint on
      `geometry__coordinates` (`data_type: json`) does the job without
      `max_table_nesting`: it keeps the one list as JSON and leaves any future
      list visible as a child table. On Postgres: `jsonb` coordinates,
      `double precision` magnitude; re-loading a loaded day left the 10,168
      rows unchanged (merge idempotent)
- [x] dbt project (`dbt-postgres`) with bronze/silver/gold — `dbt build` on
      real data: PASS=32 (3 models, 22 data tests, 7 unit tests)
- [x] Measure Postgres memory under `dbt run` against its 300 MB budget —
      peak 87 MiB in staging during `dbt build` at 10k rows
- [x] CronJob running dlt and dbt — staging verified 2026-10-18 (9,929 events,
      dbt PASS=32, 37 s); prod promoted with the same digests
- [x] Read-only roles in Postgres (RLS comes later) — group role
      `gold_reader` in both Clusters, granted by dbt on every build and
      asserted by a data test; staging verified 2026-10-27 (dbt PASS=34,
      `gold_reader_privileges` included), prod promoted with the same digest.
      No consumer login role exists yet
- [x] Reconciliation tests between layers — bronze → silver → gold → daily
      aggregate, as dbt singular tests
- [x] `staging` namespace with its own Postgres, ResourceQuota, LimitRange,
      NetworkPolicy denying staging to prod, and separate secrets. The
      operator's cluster-wide RBAC was revisited and kept — see the note in
      [ADR-0012](adr/0012-cloudnative-pg.md). Isolation verified 2026-10-17
- [x] Dev database `lagom_dev` in staging's Postgres, reached by port-forward —
      replaces the local dev environment
      ([ADR-0014](adr/0014-develop-against-staging.md)); `pg_hba` reject verified
- [x] **Decide the CI ceiling** now that the overhead is ~1.5 GB, not ~1.1 GB —
      lowered to 2.2 GB ([ADR-0016](adr/0016-ci-ceiling-2-2-gb.md))
- [x] Image build in CI (arm64, GHCR, private) and sealed pull secrets per
      namespace — [ADR-0015](adr/0015-images-on-ghcr.md); sizes and the
      private-package check in [architecture.md](architecture.md) §9a, Images
- [x] **Pipeline heartbeat** — each successful run pings Healthchecks.io, a
      missing ping e-mails the operator
      ([ADR-0018](adr/0018-pipeline-heartbeat.md)); after a 90-minute failure
      on 2026-10-27 that alerted nobody. Verified 2026-10-27: staging on
      demand (`heartbeat sent: HTTP 200`), prod's 12:00 run (`ingest`,
      `transform` and `heartbeat` all exit 0, in 18 s)
- [ ] Runbook: disaster recovery and secret rotation — moved to M3: the
      move to the 8 GB node is the drill ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md))

The Postgres backup left M2 on 2026-10-27
([ADR-0019](adr/0019-postgres-backup-deferred.md)): everything in Postgres today
is a copy of data USGS keeps, so a re-load rebuilds it. It enters by its
trigger in the table above — at the latest with Dagster's metastore in M3. The
mechanism and destination are still chosen then, measured on the node
([ADR-0012](adr/0012-cloudnative-pg.md)).

## M3 — The 8 GB node and Dagster

The node that M3 needs, and the orchestration every showcase uses.

- [x] **Move to `lagom-shared-02`** (8 GB) from Git and the runbook — this is
      the **disaster-recovery drill**: time each step, fix every gap the
      runbook shows, record the measured RTO
      ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)). Done 2026-10-28: 20 min
      31 s from the first command on the node to prod's first green run
      ([runbook](runbook.md#disaster-recovery))
- [x] Delete `lagom-shared-01` — by the checklist in the
      [runbook](runbook.md#retire-the-old-node); deleted by the operator
      on 2026-11-07
- [x] Runbook: disaster recovery, written from the drill
- [ ] Runbook: secret rotation — the drill rotated nothing, so it is written
      when a credential is first rotated
- [ ] **Secret rotation, automated and on a schedule** — the operator's
      requirement of 2026-11-10, beyond the runbook above. The mechanism is not
      chosen and needs its own ADR: Sealed Secrets are sealed by hand today
      ([ADR-0010](adr/0010-sealed-secrets.md))
- [ ] **The catalog password out of the connection string, everywhere** — today
      `LAGOM_LAKE_CATALOG` embeds the Postgres password, so DuckDB echoes it
      when an attach fails ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)),
      and the only protection is pattern-based redaction
      (`lagom_orchestration/lake.py`, `redact.py`). This weakness exists today,
      before the dashboards; dbt's `lake` target reads the same variable, and
      dlt's destination takes the catalog in a variable of its own
      (`DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG`), to be checked for the
      same embedding. Open: apply the first defence listed in
      [ADR-0040](adr/0040-dbt-charts-for-the-dashboards.md) to lake
      maintenance, dbt, dlt and the dashboards' render step, verified in
      staging first. Started 2026-11-14 ([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)):
      the dashboards' render attaches read-only with a catalog string without a
      password (`PGPASSWORD`, seen to work against a throwaway Postgres with the
      role's real grants; staging is still to verify it on the cluster); the
      writers still embed it
- [x] Measure the new node's overhead and set its CI ceiling, as ADR-0016 did —
      1,687 MiB outside Kubernetes, ceiling 5.5 GB
      ([ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)); re-measured after
      three days of uptime
- [ ] **Spike: the SME stack** — the USGS pipeline into DuckLake (Postgres
      catalog, data on R2) with dbt-duckdb; the dashboards reading it (Evidence
      was tried and replaced by dbt Charts, [ADR-0040](adr/0040-dbt-charts-for-the-dashboards.md));
      compaction of small files; memory and time against Postgres, including
      the dashboards' render and the Pages deploy. Accept or revise
      ADR-0023 with the numbers
- [x] Postgres backup — before Dagster's metastore, whose run history exists
      only on the node. With the SME stack the state to protect is the catalog
      ([ADR-0019](adr/0019-postgres-backup-deferred.md)). Barman Cloud plugin
      to R2 ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)), in
      staging and prod since 2026-10-28; a restore in staging was tested
      ([runbook](runbook.md#backup-and-restore-m3))
- [x] Check backup retention on 2026-11-05: staging's 7-day retention must
      delete its first expired backup without error. Passed on 2026-11-05
      (ADR-0025 notes)
- [x] Dagster with Postgres metastore, one instance per environment
      ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md)): the code
      location first (image `lagom-orchestration`), then each environment's
      instance, staging before prod. Staging since 2026-10-28, prod since
      2026-11-05 ([runbook](runbook.md#dagster-m3))
- [x] Dagster's UI at `ops.ofantini.com/dagster` and
      `ops.ofantini.com/dagster-staging` behind a Cloudflare Access login, instead of
      `kubectl port-forward`
      ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md);
      [runbook](runbook.md#operator-uis-behind-cloudflare-access-m3)).
      Live since 2026-11-07; the operator logged in to both
- [x] dlt and dbt assets — USGS on DuckLake, run by Dagster in staging and
      prod. Staging's first week: 372 of 373 runs succeeded; prod's first run,
      2026-11-05 12:00 UTC, loaded 30 days (10,042 events), `PASS=33`
- [x] Native failure alerting, and the CronJobs on Postgres retired in the
      same change — a failure sensor that pings the check's `/fail` at once,
      and a success heartbeat for runs that never start, on the CronJob's
      existing check and Secret; staging first. The operator decided on
      2026-11-07 not to wait for 2026-11-12. Code merged 2026-11-07 (PR #55);
      staging switched over the same day (PR #57) and passed both a ping and a
      deliberate `/fail`; prod switched the same day (PR #62), and its first
      ping came from Dagster at 21:01 UTC, after its CronJob was deleted
      ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md);
      [ADR-0026](adr/0026-dagster-one-instance-per-environment.md))
- [x] Retire the images `lagom-usgs` and `lagom-warehouse`: no manifest pins
      them since the CronJobs went on 2026-11-07. Their rows in
      `.github/workflows/images.yml` and their Dockerfiles went together, the
      same day; their packages stay in GHCR, no longer updated
- [x] `dbt test` in CI — `dbt build` on the `lake` target over a 12-row
      hand-made bronze fixture in a DuckLake on the runner, every pull
      request: models, contracts, unit and data tests, reconciliations
      (`PASS=33`, 2026-11-07). What it does not cover is in the
      [warehouse README](../platform/warehouse/README.md#tests)
- [ ] Freshness threshold declared and asserted
- [ ] Runbook: on-failure section

End of M3 = the backbone on its final node, orchestrated. **Natural checkpoint
to stop and reassess before adding anything.**

## M4 — ofantini.com

The platform's public face: a static site and a gallery of dashboards, with
the first showcase in it ([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)). Public data, no login.

- [x] The site at ofantini.com, in its own repository
      `FilipeFantini/ofantini.com`, with Lagom as its first project
      ([ADR-0039](adr/0039-the-site-in-its-own-repository.md)). Live since
      2026-11-08 on Cloudflare Pages; its deployment and its gates are in that
      repository's README and ADR-0001
- [ ] Dashboards rendered to a static page by dbt Charts, shown as a gallery
      of cards on the site
      ([ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md))
  - [x] The dbt Charts project and its CI gate: `products/dashboards/`, the
        first board (earthquakes), and a check that fails a render that
        disagrees with gold ([ADR-0040](adr/0040-dbt-charts-for-the-dashboards.md))
  - [x] A server of our own, designed and stopped: two pull requests in
        `FilipeFantini/enlightenviz`, never deployed (ADR-0042, decision 1)
  - [x] One spike, written up before the publishing pull request: the static
        page against `dct serve` on Postgres behind Cloudflare Access, and
        Lightdash tried locally (ADR-0042, decisions 3 and 4), plus a static
        page that filters in the browser. Done 2026-11-14; the measurements and
        the proposal are in [ADR-0043](adr/0043-the-dashboards-filter-in-the-browser.md),
        which the operator rejected the same day: the target stays `dct
        render`'s static page, designed with no filters
  - [ ] Per-job credentials and the catalog's read-only role, the first half
        of publishing: each job declares its own Secrets, the role
        `lake_reader` with its grants, and a read-only attach without a
        password in the string ([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md),
        proposed). Built; open until the operator accepts it, seals the role's
        Secrets and the rollout has run in staging, then prod (runbook: *The
        catalog's read-only role*)
  - [ ] The publishing pull request: the Dagster asset that renders the boards
        after each `usgs` run, in a job of its own with read-only credentials,
        published to an R2 public bucket per environment on a custom domain
        (`lagom-staging.ofantini.com`, `lagom.ofantini.com`) with a
        bucket-scoped token, not to Cloudflare Pages, with dbt Charts telemetry
        left on (ADR-0044, decisions 7 to 9). **Built,** and `r2-lake-reader` and
        `r2-dashboards` sealed for both namespaces (2026-11-14). Staging passed
        its checks on 2026-11-15 (runbook: *The dashboards' publishing path*):
        the run after each `usgs` run, the page at its hostname with its
        watermark, a failed attach whose error held no password (the first run,
        refused by the role's connection limit, fixed by rendering one query
        at a time), telemetry left on with every run done in about a minute,
        and the render's memory on real data, 804 MiB against the 1 GiB limit.
        Open until prod runs the same digest and its page is up. Two
        requirements the operator added on 2026-11-14, both met in the code:
    - **The catalog password is not exposed,** rated high risk: none of the
      render step's output can carry it. ADR-0040's defences are in the step:
      the password out of the connection string (`PGPASSWORD`), a read-only
      catalog role and a read-only R2 token, and redaction as the safety net;
      verified on the cluster in staging before prod
    - **The page says when it was built,** above the data's own times
      (`Page built at <UTC time>`, a dbt Charts setting the step checks on every
      page), so a page left in place by a failed run shows its age
- [ ] **Showcase: the SME stack on USGS** — the pipeline, with its real numbers
      (rows, run time, memory)
- [ ] **Showcase: the platform itself**, after the USGS showcase is live —
      infrastructure observability (node and pod memory against the budget,
      job durations, logs), with the stack in the
      [trigger table](#trigger-table) and its own ADR; the site shows exported
      snapshots, not a live dashboard

First thing worth showing.

## M5 — The SME stack at scale

The showcase the platform exists for: more data than the node has memory, on
the same SME stack — most of what is sold as big-data work does not need a
big-data stack ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed).

- [ ] A public dataset larger than the node's memory, on the SME stack
- [ ] Benchmarks published: rows, gigabytes, time, peak memory — Postgres
      against DuckDB, local disk against R2 — with the method on the page
- [ ] Lakehouse trigger measures visible: `dbt run` duration and data size —
      [ADR-0008](adr/0008-postgres-for-all-layers.md)

## M6 — Streaming

Continuous events. Candidates in [`use-case-globe.md`](use-case-globe.md).

This is where the platform proves it grows by composition: **if adding a source
is only new configuration, the design worked.**

The design is [ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md) (proposed): a
Raspberry Pi 3B+ at the operator's home is the source, outside the cluster,
and NATS JetStream is the log. It replaces the earlier plan of joining the Pi
to k3s as an agent node, which needed flannel moved to `tailscale0` on the
node that carries prod. [ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)
(accepted) takes the stream processor out of it: Redpanda Connect writes the
events to Postgres, their windows are views computed when read, and
RisingWave is tried afterwards, as a second consumer of the same hub.

- [ ] Spike on the operator's machine: a NATS leaf node and a hub, the hub's
      stream sourcing the leaf's, with the link cut and restored; nothing
      lost, nothing duplicated
- [x] Spike: RisingWave measured (a 768 MiB request). Not built: its
      measurements are the evidence for ADR-0047, and arrive in ADR-0045's
      notes with the spikes' pull request
- [ ] In staging: the hub, one Redpanda Connect process (the synthetic
      producer and the consumer), the `serving` database with its unlogged
      `events` table, and the views in dbt with their contracts and tests
      (the views, their tests and the `streaming` job since 2026-11-17, #113;
      the manifests, with a CI job building the `serving` target on Postgres,
      in the pull request that follows it)
- [ ] The criteria of ADR-0047's decision 8, in staging: a restart, a cut
      link, Postgres deleted, the table rebuilt from the hub, and 24 hours
      of memory, the hub's size on disk and query times
- [ ] Measure before deploying: memory of NATS and Redpanda Connect on the
      Pi, the events' size per day in the live table and in the lake, what
      the showcase adds to R2 (limit: 1 GB of the free tier's 10), bytes
      written per day to the Pi's SD card
- [ ] One Grafana in its own namespace behind Cloudflare Access, reading
      each environment's `serving` through its own read-only role, with
      staging's data source first; one panel per question of ADR-0045's
      decision 5
      (staging's built in the pull request that adds `platform/infra/grafana/`;
      the image that grants the role pinned in staging by #119, and
      prod's data source comes with the hub and Connect in prod)
- [ ] Archive to the lake on a schedule, with the raw events' retention,
      validated in staging (the hourly `streaming_archive` job, 7 days live
      and 30 in the lake, in the pull request that adds
      `platform/orchestration/lagom_orchestration/archive.py`, #121; its
      image pinned in staging by #122). The archive and its re-run pass in
      staging since 2026-11-17 (ADR-0047's notes). Still to see there: the
      first delete from the live table, when the first archived hour turns
      7 days old on 2026-11-24, and the bytes of a merged day
- [ ] The Pi: Redpanda Connect and a NATS leaf node under systemd, its
      tailnet tag and ACL, and its runbook section; it points at staging
      first
- [ ] The hub and Connect in prod, and the Pi moved to it
- [ ] The measurements written in the ADRs, as the source for an article on
      what streaming needs at this load and where a stream processor would
      pay. The article itself is written in the site's repository
      ([ADR-0039](adr/0039-the-site-in-its-own-repository.md))
- [ ] What the site shows of this showcase: aggregates only, under
      ADR-0045's privacy rule (decision 11) — not decided

**Memory.** With RisingWave, M6 did not fit the CI ceiling, and
[ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)
freed room for it. Without it (ADR-0047), prod with M6 and a whole staging
total 5,374 of 5,632 MiB, with 258 to spare (5,246 and 386 in ADR-0047, which
counted Grafana at 128; measured, it is requested at 256), so staging stays
on while M6 is built ([architecture.md §11](architecture.md#11-resource-budget)). Staging
that can be switched off comes after M6, for the room it frees to try an
idea.

- [x] Remove the `hello` Application (16 MiB), and the runbook steps that
      use it (2026-11-16)
- [x] Trim platform requests to what was measured: `argocd-server` at zero
      replicas, cert-manager's controller and cainjector at 48 MiB
      (2026-11-16)
- [x] One run slot per environment: `maxConcurrentRuns: 1` in both Dagster
      instances, and the budget check failing a run slot without it
      (2026-11-16)
- [x] Staging without its Dagster UI and its backup (2026-11-16)
- [ ] Both Postgres at 256 MiB, once a reading taken with events being
      written confirms it
- [ ] After M6 is in prod: staging on or off. Postgres hibernated and
      Dagster removed while off; one schema-checked state file outside the
      guarded list, operated by agents (a guardrail change, approved once by
      the operator); the budget check counting staging as on whatever the
      file says; the quota at the `on` total; staging's alarm on failures
      only
- [ ] Second phase, with its own ADR: RisingWave as a second consumer of
      prod's hub, an experiment that runs while staging is off, the same
      metrics compared with the views computed on read
- Parked, by ADR-0047: the Flux spike, until a resident component for prod
  does not fit beside a whole staging

---|---|
| `docs/` | now |
| `platform/infra/`, `.github/` | M1 |
| `platform/warehouse/`, `services/usgs/` | M2 |
| `platform/orchestration/` | M3 |
| `products/` | M4 |

Argo CD watches `platform/infra/` only. GitHub Actions handles CI and consumes
nothing on the VPS.

**The test the layout must keep passing:** adding a source creates one directory
under `services/` and one under `platform/warehouse/models/`, and changes
nothing else. If a new source ever requires touching more, the layout needs
revisiting — with a new ADR.
