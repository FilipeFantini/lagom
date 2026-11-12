# ADR-0041 — The dashboards read a serving layer in Postgres; their server is built in its own repository

- **Status:** Proposed — accepted by the operator's approval of the pull
  request that adds it
- **Date:** 2026-11-12
- **Decided by:** the operator, in conversation on 2026-11-12, after a spike of
  the same day on serving the boards with `dct serve`.
- **Amends:** [ADR-0040](0040-dbt-charts-for-the-dashboards.md), decision 3 and
  *Open, for the publishing pull request*: the static page published to
  Cloudflare Pages is no longer the target, only the fallback of decision 7
  below; [ADR-0023](0023-sme-stack-duckdb-on-ducklake.md), *The SME stack*
  ("dashboards query with it") and *Dashboards are built in the cluster*: the
  dashboards do not read the lake;
  [ADR-0022](0022-platform-as-a-portfolio-of-architectures.md), *The site is
  static, the computation is ephemeral*, for the dashboards only: the site
  stays static, the dashboards get a resident server;
  [ADR-0039](0039-the-site-in-its-own-repository.md), decision 2: the boards
  stay in this repository, the tool that serves them does not.
- **Restores:** the serving role [ADR-0005](0005-duckdb-and-postgres-roles.md)
  gave Postgres, which [`architecture.md` §8](../architecture.md#8-serving--postgres)
  never dropped.

## Context and problem

The operator asked for dashboards that are a service, not a file: filters in
the page, many reports on different themes sharing one infrastructure without
touching one another, and a place in the pipeline. A streaming showcase comes
later ([roadmap](../roadmap.md#m6--streaming)): few events, at high frequency,
and the same public face must show them.

A first design was drafted and tried on 2026-11-12: `dct serve`, the server
inside dbt Charts 0.11.1, in a Deployment per environment, reading gold from
the lake with a read-only role on the DuckLake catalog. It was tried in a local
container against the CI fixture and a disposable Postgres, once, not on the
cluster; the draft was not merged.

**What the spike found.**

- **`dct serve` does not close its connections.** Each render leaves DuckDB
  connections open, and each holds a Postgres connection through the catalog
  attach: about 8 connections and 60 MiB a render with the default 8 workers;
  1 connection and about 9 MiB a render with `--max-workers 1`. Both clusters
  allow 25 connections, shared with the pipeline: about 25 page views would
  leave a run unable to connect.
- **Memory grows with use:** 178 MiB at start, about 520 MiB after five
  renders, 938 MiB of a 1 GiB limit after 20 concurrent requests.
- **It is a preview server.** It answers on `/data/`, `/inspect/*`,
  `/templates`, `/__livereload` and `/<board>.yaml`; it has no cache and no
  authentication.
- **The image is 877 MiB.**
- **It does not fit the budget.** 5,070 of 5,632 MiB are requested today; a
  request of 768 MiB per environment does not fit
  ([`architecture.md` §11](../architecture.md#11-resource-budget)).
- **What did work,** and stays useful for the open item on the catalog password
  in [M3](../roadmap.md#m3--the-8-gb-node-and-dagster): DuckLake's attach
  honours `PGPASSWORD`, so the password can leave the connection string; a
  failed attach then prints no password; a read-only catalog role with a
  `READ_ONLY` attach writes nothing. Seen locally, still to be verified in
  staging.

**The defect is in the design, not only in the tool.** The draft made a public
service a client of the catalog the pipeline depends on, with DuckDB resident
in the serving process. Every workaround of that week followed from it: the
scope on the secret, the password in the connection string, the catalog role,
the leaked connections, 0.7 GB a render.

**The documents disagreed.** ADR-0005 and `architecture.md` §8 give Postgres
the serving role, "holding only the small final result", and §10 explains why
an API, not the database, is the public channel. ADR-0023 then had the
dashboards query the lake with DuckDB, and did not supersede either.

## Requirements and constraints

- A public reader holds no privilege on the lake's catalog, on Dagster's
  database, or on R2.
- One place a dashboard reads, for batch and for streaming.
- A fault in the public face cannot stop a run.
- Reports of different themes are isolated from one another.
- Dashboards stay text in Git, checked in CI; a wrong number fails a build
  (protocol §16.2).
- Resident memory is small, measured before it is deployed, and inside the
  budget.
- What is served can be rebuilt from the lake.

## Alternatives considered

**`dct serve` reading the lake.** The draft. Rejected on the spike's findings
above.

**`dct serve` reading a DuckDB file published after each run.** No Postgres
connection to leak. Rejected: the memory growth is unmeasured there, the
preview routes and the 877 MiB image stay, and it has no answer for streaming.

**The static page of ADR-0040.** Works today. Rejected as the target: no
filters and no streaming. Kept as the fallback (decision 7).

**Grafana.** About 150 MB, mature, refreshes in seconds. Rejected for the
public face: it is an observability product, in its model and its look. It
keeps the role [`architecture.md` §9](../architecture.md#9-visualization)
gives it, the operator's view of the platform.

**Lightdash, Superset, Metabase.** Not tried here. Rejected on what they are:
600 MB to 1 GB or more each (§9), a dashboard's state kept in the tool's own
database and not in Git, and refresh on an interval, not streaming.

**Streamlit or Dash.** Rejected: a dashboard becomes a program, not a
declaration, and every security property is ours to write for each one.

## Decision

1. **The dashboards read a serving layer, and only it.** A database named
   `serving` in each environment's existing Postgres cluster holds what the
   public face shows. No dashboard, and no other public reader, connects to the
   DuckLake catalog or to R2.
2. **Roles.** One role writes `serving`, held by the pipeline. Each theme
   reads through a role of its own: read-only, limited to its schema, with a
   connection limit and a statement timeout. That is the isolation between
   reports, and what keeps the public face from using the connections a run
   needs.
3. **Batch: gold is published after each run.** A step of the same run copies
   the gold tables a board needs into `serving`, as an asset downstream of
   gold, and compares it with gold; a difference fails the run. The mechanism
   — a dbt model on an attached Postgres, or a step in Python — is chosen by a
   spike in the pull request that adds it.
4. **Streaming: events land in `serving`** and are archived to the lake on a
   schedule. This states the direction only; the showcase's own ADR decides the
   detail when [M6](../roadmap.md#m6--streaming) starts.
5. **`serving` is never a source.** It can be dropped and published again from
   the lake. Events not yet archived are the one exception, and the cluster's
   backup covers them
   ([ADR-0025](0025-postgres-backup-barman-cloud-to-r2.md),
   [ADR-0027](0027-hourly-wal-archiving-and-14-day-prod-retention.md)).
6. **The server of the dashboards is a tool of its own, in its own
   repository.** No tool found on 2026-11-12 is at once dashboards as code,
   batch and streaming, small enough for this node, and safe to expose. It is
   built separately, open source, and Lagom consumes it as a pinned image, like
   any upstream component. Its design document comes before its code and holds
   its requirements, what it will not do, its threat model and the criterion
   for abandoning it; this ADR does not restate them. The repository is named
   when it is created, and this ADR is amended with the link.
7. **Until that tool serves the earthquakes board with a filter, nothing is
   published,** and the dbt Charts project stays as it is: the board, the CI
   check, decisions 1, 2, 4 and 5 of ADR-0040. If the tool is abandoned, the
   fallback is ADR-0040's static page.
8. **`dct serve` is not deployed.** The draft's image, catalog role and grants
   step are not merged.

## Consequences

**Positive.**

- The public face is cut off from the pipeline's state: a reader of `serving`
  cannot read or exhaust the catalog.
- The documents agree again: Postgres serves, DuckDB computes, the lake keeps
  the history.
- Batch and streaming meet in one place, so a board does not care which path
  filled its table.
- No resident memory is added by this decision: a database in a cluster that
  already runs has no request of its own.

**Negative, and what it costs.**

- **A copy of gold.** It can fall behind or differ. The answer is decision 3:
  the same run publishes and compares, and fails on a difference. It is only as
  complete as the comparisons written.
- **Shared fate.** `serving` lives in the cluster that holds the catalog and
  Dagster's metadata: CPU, memory and the 25 connections are shared. The
  per-role limits contain it; they do not remove it. **Separated into its own
  cluster when** a run fails to connect or slows down because of public reads;
  that cluster is budgeted then.
- **The pooler's trigger arrives.** §8 defers PgBouncer to the first external
  BI consumer. Whether the dashboards' server needs it is measured when it is
  deployed.
- **Building a tool is the classic way to stop building the product.** The
  design document's list of what the tool will not do, and its criterion for
  abandoning it, are the limits. Its maintenance is ours.
- **The third change to the dashboards in a week,** after Evidence and the
  static page, and M4's gallery waits for it.
- **Nothing is public yet.** The board exists and is checked; no visitor can
  open it.

## Risks, cost, and reversal

Cost: none on the node now. The tool's own memory is measured and budgeted in
the pull request that deploys it.

Reversal: drop the `serving` database and its roles; the dashboards return to
ADR-0040's static page, which reads the lake at render time and needs no
serving layer.

**Reconsider when** the tool meets its abandonment criterion, or an existing
tool comes to cover the requirements above.

Sources checked on 2026-11-12: `dbt-charts` 0.11.1 (`dct serve`, `dct --help`);
the spike's logs, kept outside the repository.
