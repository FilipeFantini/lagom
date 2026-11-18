# Architecture Decision Records

Each ADR records one decision with lasting consequence: context, alternatives,
decision, consequences, and how to reverse it.

**Rules**

- A decision is reversed by a **new ADR that supersedes the old one**. The
  superseded ADR stays in place, marked `Superseded by ADR-NNNN`. History is
  never edited to look consistent.
- Numbering is sequential and never reused.
- Status is one of: `Proposed`, `Accepted`, `Superseded`, `Rejected`.
- Write an ADR when the decision shapes the system — not for routine choices.
  Re-introducing a practice excluded in
  [`../engineering-protocol.md`](../engineering-protocol.md) §17 requires one.

**Template:** copy [`0001-use-adrs.md`](0001-use-adrs.md) and keep its headings.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-use-adrs.md) | Record architecture decisions in ADRs | Accepted; the Obsidian vault's role amended by 0034 |
| [0002](0002-single-node-k3s-hetzner.md) | Single-node k3s on a Hetzner CAX11 | Accepted, partially superseded by 0021; the Pi as an agent node superseded by 0045 (proposed) |
| [0003](0003-ingestion-split-dlt-benthos.md) | Split ingestion: dlt for batch, Redpanda Connect for events | Accepted; NATS's deferral amended by 0045 (proposed) |
| [0004](0004-table-format.md) | Open table format: Iceberg preferred, Delta as fallback | Proposed, deferred by 0008 |
| [0005](0005-duckdb-and-postgres-roles.md) | DuckDB as compute engine, Postgres as operational spine | Accepted, partially superseded by 0008 |
| [0006](0006-monorepo-layout.md) | Monorepo layout: platform, services, products | Accepted |
| [0007](0007-three-environments.md) | Three environments: dev local, staging and prod on one node | Accepted, partially superseded by 0008, 0014, 0015 and 0026; staging on demand, lean and switched by agents, amended by 0046 |
| [0008](0008-postgres-for-all-layers.md) | Postgres for every layer until the lakehouse has a measured trigger | Accepted, partially superseded by 0014 |
| [0009](0009-argocd-bootstrap-and-self-management.md) | Argo CD: pinned Helm chart, app of apps, self-managed | Accepted, decision 4 superseded by 0017; the headless install's rejection reversed by 0046 |
| [0010](0010-sealed-secrets.md) | Sealed Secrets for secrets in Git, key renewal off | Accepted |
| [0011](0011-memory-viability-on-4gb.md) | Memory viability on the 4 GB node, measured before M2 | Accepted, decision 3 superseded by 0016 |
| [0012](0012-cloudnative-pg.md) | CloudNativePG runs Postgres; backup mechanism chosen at its trigger | Accepted, partially superseded by 0019 |
| [0013](0013-agent-driven-development-guardrails.md) | Agent-driven development behind enforced guardrails | Accepted; rule 3 superseded and rule 2 amended by 0029 |
| [0014](0014-develop-against-staging.md) | Develop against staging, with no local services | Accepted; staging's Postgres awake for development amended by 0046 |
| [0015](0015-images-on-ghcr.md) | Images built in CI, published privately to GHCR, promoted by digest | Accepted |
| [0016](0016-ci-ceiling-2-2-gb.md) | CI ceiling lowered to 2.2 GB from the measured overhead | Superseded by 0024 |
| [0017](0017-public-repository-from-a-snapshot.md) | Publish as a new public repository from a single-commit snapshot | Accepted |
| [0018](0018-pipeline-heartbeat.md) | Pipeline liveness by heartbeat to Healthchecks.io | Accepted; check and Secret pass to Dagster by 0033 |
| [0019](0019-postgres-backup-deferred.md) | Postgres backup deferred until the node holds state no source can rebuild | Accepted |
| [0020](0020-move-to-the-8-gb-node.md) | Move to the 8 GB node (CAX21) for M3, pending Hetzner capacity | Accepted |
| [0021](0021-k3s-packaged-components-under-git.md) | Traefik and servicelb disabled; local-path and metrics-server managed by Argo CD | Accepted |
| [0022](0022-platform-as-a-portfolio-of-architectures.md) | The platform as a portfolio of data architectures, shown on getlagom.app | Accepted, domain superseded by 0030; the site's home amended by 0039; the dashboards' tool amended by 0040; static dashboards amended by 0041, and again by 0042 |
| [0023](0023-sme-stack-duckdb-on-ducklake.md) | The SME stack: DuckDB on DuckLake, Postgres as catalog, data on R2 | Proposed; the dashboards' tool amended by 0040; the dashboards reading the lake amended by 0041, and again by 0042 |
| [0024](0024-ci-ceiling-on-the-8-gb-node.md) | CI ceiling raised to 5.5 GB, measured on the 8 GB node | Accepted |
| [0025](0025-postgres-backup-barman-cloud-to-r2.md) | Postgres backup: the Barman Cloud plugin to Cloudflare R2, staging first | Accepted; retention checked 2026-11-05; RPO and prod retention superseded by 0027; staging's backup removed by 0046 |
| [0026](0026-dagster-one-instance-per-environment.md) | Dagster, one instance per environment, running USGS on DuckLake | Accepted, UI access superseded by 0028, then 0030; heartbeat and CronJob retirement amended by 0033; staging's Dagster and the run queue amended by 0046 |
| [0027](0027-hourly-wal-archiving-and-14-day-prod-retention.md) | WAL archived hourly, prod backups kept 14 days | Accepted |
| [0028](0028-dagster-ui-over-the-tailnet.md) | Dagster's UI over the tailnet, by `tailscale serve` on the node | Superseded by 0030 |
| [0029](0029-agents-on-more-than-one-harness.md) | Agents on more than one harness: one rulebook, and who may merge | Accepted; Decision 3 amended and Tucano's rejection reversed by 0032; Decision 4's guardrail go-ahead enforced by 0035; Decision 4 amended by 0036; Decision 3 waived for the handoff through T3 Code by 0051 |
| [0030](0030-operator-uis-behind-cloudflare-access.md) | ofantini.com as the domain; operator UIs at ops.ofantini.com behind Cloudflare Access | Accepted; its paths amended by 0046 |
| [0031](0031-repository-named-lagom.md) | The repository is named `lagom`; a rename to Robusta was tried and reverted | Accepted |
| [0032](0032-agents-in-credential-free-containers.md) | Agents run in containers that hold no credential, whatever runner starts them | Accepted; Decisions 2, 4 and 5 amended by 0035 |
| [0033](0033-dagster-alerts-through-healthchecks.md) | Dagster raises the alarm: a failure sensor and a heartbeat, both through Healthchecks.io | Accepted; decision 5 amended by 0044; staging's alarm amended by 0046 |
| [0034](0034-notion-as-the-one-documentation-space-outside-the-repo.md) | Notion is the one documentation space outside the repository | Proposed |
| [0035](0035-agents-use-the-operators-scoped-token.md) | Agents use the operator's repository-scoped token; an environment approval guards what reaches the cluster | Accepted; Decision 5 amended by 0036; Decisions 3 and 4 by 0038; the staging mode file outside the guarded list, by 0046 |
| [0036](0036-the-reviewer-as-a-required-check.md) | The reviewer runs in CI as a required check | Accepted; Decision 6 amended by 0037, and by 0048 (a Dependabot secret, reversed by 0049); Decisions 1, 2 and 4 by 0038 |
| [0037](0037-the-reviewer-runs-on-sonnet.md) | The reviewer runs on Sonnet, to spend less of the subscription | Accepted |
| [0038](0038-leaner-agent-gates.md) | Leaner agent gates: the guarded list is the security core, and the reviewer runs less and blocks less | Accepted; the staging mode file outside the guarded list, by 0046 |
| [0039](0039-the-site-in-its-own-repository.md) | The site at ofantini.com lives in its own repository | Accepted; decision 2's tool amended by 0040, and where the server of the dashboards lives by 0041 |
| [0040](0040-dbt-charts-for-the-dashboards.md) | The dashboards are written in dbt Charts, not Evidence | Accepted; decision 3 and the publishing path amended by 0041; the static page is the target again by 0042; the publishing path and decision 5 amended by 0044 |
| [0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md) | The dashboards read a serving layer in Postgres; their server is built in its own repository | Accepted; decisions 6 and 7 superseded, and 1, 3 and 8 amended, by 0042; decision 4 amended by 0045 (proposed); `serving` created in M6 by 0047 |
| [0042](0042-the-dashboards-return-to-dbt-charts.md) | The dashboards return to dbt Charts; the server of our own is stopped | Accepted; decision 6 settled by 0045 (proposed) |
| [0043](0043-the-dashboards-filter-in-the-browser.md) | The dashboards filter in the visitor's browser: a static page drawn with Vega-Lite | Rejected; its spike's measurements stand |
| [0044](0044-per-job-credentials-and-the-dashboards-publishing-path.md) | Credentials per job, and the path the dashboards take to be published | Accepted; decisions 1 to 6 built in #80, 7 to 9 in the dashboards' render |
| [0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md) | The streaming showcase: a Raspberry Pi outside the cluster, NATS JetStream as the log, RisingWave measured first | Proposed; RisingWave's request, decision 9, shared components and the archive's validation amended by 0046; decision 6 superseded, and decisions 3, 7, 8 and 9 amended, by 0047 |
| [0046](0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md) | Memory for streaming: a lean staging on demand, one Grafana, one run slot per environment | Accepted (#101); being built; the four modes of decisions 1 and 2 and decision 9 superseded, and decisions 4, 7 and 8 amended, by 0047 |
| [0047](0047-streaming-without-a-stream-processor-and-staging-on-or-off.md) | Streaming without a stream processor: events in Postgres, and a staging that is on or off | Accepted (#106); being built |
| [0048](0048-dependency-and-code-scanning-for-the-public-repository.md) | Dependency and code scanning for the public repository: Dependabot, CodeQL, pinned actions, approval for outside contributors' CI | Accepted; Decision 2 superseded by 0049 |
| [0049](0049-the-reviewer-runs-on-dependabots-pull-requests.md) | The reviewer runs on Dependabot's pull requests: the action allows `dependabot[bot]`, and no Dependabot secret | Accepted |
| [0050](0050-the-pi-streaming-runtime-and-private-hub-link.md) | The Pi streaming runtime and its private link to the hub | Proposed; deployment requires the operator's PR approval |
| [0051](0051-handoff-to-another-provider-through-t3-code.md) | Handing off to another provider through T3 Code, on the Mac | Proposed |
