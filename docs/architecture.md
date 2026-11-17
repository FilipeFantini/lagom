# Architecture

Source of truth for how this platform is built. Read before touching any
component. Decisions with lasting consequence are recorded as ADRs in
[`adr/`](adr/); this document describes the resulting system.

---

## 1. Design principles

1. **Ephemeral compute.** The enemy is not tool size, it is resident RAM. Jobs
   start, process, and exit. We do not hold memory; we rent it by the second.
2. **Decoupled layers.** Storage, compute, transformation, and serving are
   replaceable pieces. This is what buys flexibility — swapping the compute
   engine touches neither ingestion nor consumers.
3. **Everything declarative in Git.** Infrastructure, ingestion,
   transformation, and reports are versioned code. No manual change in the
   cluster.
4. **Ingestion is configuration, not code.** A new source is new config, not a
   new script.
5. **Resource budget as a contract.** Memory requests and limits, and CPU
   requests, on every workload, with a CI check that fails if the sum exceeds
   the ceiling.
   **Frugal by design, not capped by the node.** The ceiling follows the
   node: moving to a bigger one is a resize and one number changed in §11, on
   the criteria in [ADR-0011](adr/0011-memory-viability-on-4gb.md). What does
   not change with the node is the discipline — every resident process
   justifies its memory, and work that can run as a job does not stay
   resident.
6. **Multi-dev from day one.** Nothing is built in a way that blocks a second
   developer from joining.
7. **Defer with a trigger.** Each piece enters when a concrete trigger appears,
   not because it is on the diagram. See [`roadmap.md`](roadmap.md).

## 2. Data flow

```
External sources (APIs, feeds, sensors)
        |
Ingestion — two patterns:
   batch/API  -> dlt (ephemeral job)
   event      -> Redpanda Connect [-> NATS]
        |
Postgres — bronze / silver / gold schemas
   transformation: dbt (ephemeral job; its SQL runs in Postgres)
        |
Access channels
```

**Proposed: the SME stack** ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed). DuckDB as the engine, DuckLake as the
table format, Postgres as its catalog, the data files on Cloudflare R2. It
replaces the Postgres warehouse only after a spike in M3 passes; until then
every layer lives in Postgres ([ADR-0008](adr/0008-postgres-for-all-layers.md)).

Dagster orchestrates from the outside: it triggers jobs and records state,
lineage, and retries. It is not a step in the flow because data does not pass
through it.

## 3. Infrastructure

| Resource | Role |
|---|---|
| Hetzner CAX21 (4 ARM vCPU, 8 GB), `lagom-shared-02` in `fsn1` | control plane and main node; hosts the prod and staging namespaces since 2026-10-28 ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)) |
| Hetzner CAX11 (2 ARM vCPU, 4 GB), `lagom-shared-01` in `nbg1` | the previous node, deleted on 2026-11-07 by the checklist in the runbook (*Disaster recovery*) |
| Raspberry Pi 3B+ (1 GB), at the operator's home | an external source for the streaming showcase, outside the cluster: it produces events and forwards them over the tailnet ([ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md), proposed) |
| `ofantini.com` domain, registered and served by Cloudflare | public entry: the site at the apex (M4), the operator's tools at `ops.ofantini.com`. It replaced `getlagom.app` on 2026-11-07 ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)) |

Cost: the CAX11 was EUR 7.37/month, as shown in the Hetzner Console on
2026-10-12 ([ADR-0002](adr/0002-single-node-k3s-hetzner.md)); the CAX21 is
roughly double ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)), its exact
price the one the Console shows. Object storage (R2) enters with the SME-stack
spike in M3, on its free tier ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed); the prices checked earlier are in ADR-0008.

**Outside the cluster, principle 3 does not hold yet.** The server, its
firewall, the Tailscale ACL, the Cloudflare Access application and its DNS
records, and the Healthchecks.io checks are created by hand, following the
[runbook](runbook.md); the domain's zone at Cloudflare was set up by
hand in its console. Argo CD
reconciles only what runs inside the cluster. Terraform takes over these
resources by trigger — see [`roadmap.md`](roadmap.md#trigger-table).

### Kubernetes — k3s

k3s is CNCF-certified Kubernetes, not a reduced variant: same API, same
manifests, same `kubectl`. See [ADR-0002](adr/0002-single-node-k3s-hetzner.md).

Three k3s defaults to be aware of, so they are not mistaken for upstream
behaviour:

| Default | Market standard | Our choice |
|---|---|---|
| Traefik as ingress | ingress-nginx | **disabled**: nothing uses an ingress, and servicelb published it on the public address ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)) |
| local-path storage | a real CSI driver | fine on a single node; volume is pinned to the node. Installed by Argo CD, not as a k3s addon ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)) |
| SQLite datastore | etcd | no HA; k3s can run embedded etcd if needed |

None of this appears in our manifests. Deployments, Services, RBAC, CRDs,
Secrets, and Argo CD itself are identical to any Kubernetes.

### GitOps — Argo CD

Git as the source of truth, continuous reconciliation. Dex (SSO) is disabled to
save memory. Secrets never go to Git in plain text — they are sealed with
Sealed Secrets ([ADR-0010](adr/0010-sealed-secrets.md)).

**Reconciliation** is the loop that makes this work: every few minutes Argo CD
compares what `main` says should exist with what runs in the cluster, and
changes the cluster until they match. A manual change in the cluster is undone
on the next pass (`selfHeal`); an object removed from Git is deleted from the
cluster (`prune`).

**App of apps.** A `root` Application watches `platform/infra/apps/` and creates
every Application found there; each Application points at a directory or a Helm
chart. Argo CD manages itself the same way, from a pinned upstream chart and
[`platform/infra/argocd/values.yaml`](../platform/infra/argocd/values.yaml). It
reads this public repository anonymously over HTTPS. See
[ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md) and
[ADR-0017](adr/0017-public-repository-from-a-snapshot.md).

## 4. Ingestion — two patterns

| Source nature | Tool |
|---|---|
| Batch API with incremental state | **dlt** as a job/asset |
| Continuous events (MQTT, websocket, GTFS-RT) | **Redpanda Connect** -> NATS |

Not duplication: two shapes of problem. See
[ADR-0003](adr/0003-ingestion-split-dlt-benthos.md).

### dlt — batch

dlt is a library, not a server: it runs inside the job and exits. Zero resident
RAM, which matches the ephemeral compute principle.

It natively solves the five points where a streaming tool is weak at batch
extraction: pagination; incremental state across runs; schema inference and
evolution; normalization of nested JSON; and backfill.

The `rest_api` source declares endpoints, paginator, auth, and incremental
cursor as data, keeping "ingestion is configuration" true.

It integrates natively with Dagster, so each pipeline becomes an asset with
lineage and no glue code.

The `rest_api` config lives in a YAML file next to the pipeline, loaded with
`yaml.safe_load`. The first source is [`services/usgs`](../services/usgs/README.md).

### Redpanda Connect — events

Go binary, roughly 80 MB resident. Declarative YAML: input, pipeline, output.
Connectors for most transports, including MQTT, which matters for future IoT
sources.

### NATS JetStream — the log of the streaming showcase

CNCF, roughly 40 MB (an estimate, not measured), with persistence, replay, and
consumer groups. It was deferred with a trigger: multiple consumers or a
replay requirement. If a source is polled every 20 seconds, Redpanda Connect
can write straight to the destination and NATS adds nothing functionally.

**The streaming showcase meets the trigger** ([ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md),
proposed). Its source is a Raspberry Pi outside the cluster; **in staging,
the hub, Redpanda Connect and the table run since 2026-11-17, fed by a
synthetic producer in the same Connect process** (no leaf, no Pi yet;
runbook: *The streaming showcase in staging*):

```
Pi:    sensors, ping -> Redpanda Connect -> NATS leaf node (stream on disk)
                                                 |  tailnet, outbound only
node:  NATS hub (stream sources the Pi's) -> Redpanda Connect -> Postgres, serving.events
                                                                    |-> views (dbt) -> Grafana (operator)
                                                                    |-> Dagster -> lake (history)
```

NATS stores; Redpanda Connect reads and writes. The leaf node keeps events
while the home link is down and the hub copies them when it returns, hours
late.

**No stream processor** ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md), accepted, superseding ADR-0045's choice of
RisingWave). On the node, Redpanda Connect reads the hub through a durable
consumer, a position the hub keeps for it, and writes each event to an
append-only table in the `serving` database, acknowledging only after the
write; a primary key on the event's identifier stores a repeated delivery
once. The table is `UNLOGGED`, so the events stay out of the backup and R2;
a crash of Postgres empties it, and a dbt test on a schedule makes that
loud. The windows over event time are views, defined in dbt and computed when
read, so a late event is counted by the next query and each metric is defined
once, behind a dbt contract that Grafana's panels read. The hub keeps 7
days, and the table is to keep the same 7 days, so the table can be rebuilt
by reading the stream again. **Until the archive job exists** (ADR-0047,
decision 6) nothing deletes from the table: it keeps every event since the
start, and a rebuild restores only the hub's last 7 days. This is aggregation on read, not stream processing.
RisingWave is tried next, as a second consumer of the same hub, in the room
staging frees while it is off (§9a), and stays only if the comparison earns
its memory.

What is collected, the questions it answers, the limit on R2 and the privacy
rule are in ADR-0045; the criteria this design must pass in staging are in
ADR-0047.

## 5. Storage — Postgres now, open table format by trigger

**Now:** bronze, silver, and gold are schemas in each environment's Postgres.
Bronze is append-only, so a transformation is replayed by re-running dbt, never
by re-reading the source. dlt normalizes nested JSON into typed child tables;
objects nested beyond `max_table_nesting` are kept as `JSONB`. Binary payloads
are not stored in Postgres — a source that carries files is a lakehouse
trigger. See [ADR-0008](adr/0008-postgres-for-all-layers.md).

**When the lakehouse enters**, the rest of this section applies.

Plain Parquet is good at reading and writing but has no transactions, no
updates, and no controlled schema evolution. For multi-source and IoT that hurts
quickly: late data, record corrections, a sensor that gains a field.

A table format provides ACID over files, time travel, schema evolution, and real
`MERGE`.

**Iceberg is the preference; Delta is the fallback. Decided by testing when
the lakehouse enters** — see [ADR-0004](adr/0004-table-format.md). **Proposed
change:** for the SME stack, [ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed puts DuckLake in place of Iceberg, with a
catalog in Postgres; Iceberg stays the table format of the big-data stack.

**Rule for this phase, to avoid overengineering:** no catalog, no optimization,
no snapshot maintenance. Write and read by path only.

## 6. Transformation — dbt

**Now:** `dbt-postgres`. The container starts, runs `dbt run`, and exits; the
SQL executes inside the environment's Postgres. The job has no idle cost, but
the work lands on a resident process that also serves reads — the trade-off
ADR-0008 accepts, and whose limit is the first lakehouse trigger. Models prefer
portable SQL, because a later move to DuckDB has to port them.

**Proposed, with the SME stack** ([ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md), proposed): `dbt-duckdb` on DuckLake.

**When the lakehouse enters:** DuckDB reads the table format directly from
object storage, processes with a configured `memory_limit` (spilling to disk
rather than dying), and writes back. Compute becomes fully ephemeral.

Layers: bronze (raw, typed), silver (cleaned, conformed), gold (modelled for
consumption). Each layer's guaranteed schema is a contract — see
[`engineering-protocol.md`](engineering-protocol.md) §16.3.

**The project** is `platform/warehouse/`. For USGS, silver is
`stg_usgs__events` (the latest revision of each event, typed, times in UTC) and
gold is `fct_earthquakes` (earthquakes only, with magnitude and depth classes —
the USGS showcase's main table) and `agg_earthquakes_daily_region`. Both gold models have an
enforced contract. `dbt build` runs the tests with the models: keys, ranges and
accepted values; reconciliation of row counts between the layers
(`tests/reconcile_*.sql`); and unit tests of the dedup, the region parsing and
the class boundaries. Freshness of bronze (warn 2 h, error 6 h) is checked by
`dbt source freshness`. CI lints and parses the project but runs no model: how
to run it is in [`platform/warehouse/README.md`](../platform/warehouse/README.md).

**The streaming showcase's views** are the same project on a second Postgres
target, `serving`, the database of that name
([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md),
decision 3). Under it only `models/streaming/` is built, and it is built
under no other target. A hook creates the live table `live.events` if it is
missing (`UNLOGGED`, its key the event identifier, inserted into by Redpanda
Connect and by nothing else); `streaming/` types the events into views;
`metrics/` holds five views, each with an enforced contract, which are what
Grafana reads: the link per minute (loss, median and 95th-percentile round
trip, jitter), the link by hour of the day and day of the week, the link's
outages, the source's health per minute, and the pipeline per minute (events
against one a second, and how late). They group by `collected_at`, so a late
event is counted by the next query. A test fails when any of the five streams
held fewer than half its events over the last hour: it is what makes an
emptied table loud.

## 7. Orchestration — Dagster

Assets, lineage, backfill, declarative retries, native dbt and dlt integration.
Metastore in Postgres, since the SQLite default does not serve multiple users.

**One instance per environment** ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md)),
in the environment's namespace, with its metadata in a database `dagster` in
that environment's Postgres, backed up with it. Runs are Kubernetes Jobs in
the same namespace, so staging never reaches prod.

**One code location for the platform**, the image `lagom-orchestration`
([`platform/orchestration/`](../platform/orchestration/README.md)). It holds
each service's assets (`services/<service>/assets/`) and the dbt project,
whose models dagster-dbt turns into assets downstream of the sources they
read. Its first content is USGS on DuckLake:

- the job `usgs` every 30 minutes: dlt loads into DuckLake, then `dbt build`
  runs on the `lake` target;
- the job `lake_maintenance` once a day: it merges small files, expires
  snapshots older than 7 days, and deletes the files nothing references, then
  gives the read-only catalog role its `SELECT` grants;
- the job `dashboards` after each successful `usgs` run: it renders the boards
  against the lake with read-only credentials and publishes them (§9).
- the job `streaming` every hour at minute 45, only in an environment whose
  values set `LAGOM_STREAMING: "true"`: `dbt build` on the `serving` target
  (§6), as the database's owner `serving` and with no lake credential. A
  failed contract or test fails the run and raises the alarm. Staging sets
  the variable since 2026-11-17; prod does not, until the showcase reaches it
  ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)).

**One run at a time per environment**, of any job: the run queue starts a run
only when the one before it has ended (`maxConcurrentRuns: 1` in
`platform/infra/dagster-<env>/values.yaml`), because the budget in §11 counts
one run's memory per environment
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).
A new batch source takes turns in that slot. Two `usgs` runs are also kept
apart by their tag, a rule of their own: they would share dlt's cursor.

Dagster raises its own alarm through the same Healthchecks.io check
([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)): a
sensor pings it after each successful `usgs` run, and another calls its
`/fail` with the error when any run fails. A password inside that error is
removed first: DuckDB echoes the catalog URI, and the body is stored and
e-mailed by Healthchecks.io. The sensors are in the code
location ([`alarm.py`](../platform/orchestration/lagom_orchestration/alarm.py)).
**They are live in both environments, and the CronJobs on Postgres are
retired** (2026-11-07: removed from Git, then deleted by hand, since the
`postgres-<env>` Applications never prune; runbook: *Retire a manifest from
`postgres-<env>`*). The check is the one the CronJob pinged
([ADR-0018](adr/0018-pipeline-heartbeat.md); runbook: *Pipeline heartbeat*).

**Each job declares the Secrets its run Pod gets**
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md),
amends ADR-0033 decision 5). The chart copies the code server's whole
environment into every run Pod, so that environment holds only what loading
the definitions needs (`HEARTBEAT_URL`) and the settings that are not secret
(the bucket, the schema). The catalog owner's connection string and the R2
write token are named in each job's `dagster-k8s/config` tag
(`WRITER_ENV` in [`definitions.py`](../platform/orchestration/lagom_orchestration/definitions.py)),
which dagster-k8s adds to the code server's variables when it builds the Pod.
A Secret is per namespace, so the same name gives staging's value in staging and
prod's in prod. `usgs` and `lake_maintenance` hold the same credentials as before; a
job that needs fewer names fewer: the dashboards' render holds `lake_reader`'s
password, a read-only R2 token on the lake and a token for the dashboards
bucket, and nothing of the owner's or the write token
(`READER_ENV`; runbook: *The catalog's read-only role* and *The dashboards'
publishing path*).

**The UI has no login**, so reaching it is the access control. Each
environment's UI is at a path of `ops.ofantini.com` behind a Cloudflare Access
login, served through an outbound-only tunnel
([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md); runbook:
*Operator UIs behind Cloudflare Access*).

Airflow and Prefect are excluded on resident RAM.

## 8. Serving — Postgres

Postgres is the operational spine, with two permanent roles independent of
geographic data:

1. **Metastore** for Dagster (run history, asset state).
2. **Serving layer** for dashboards, API, and BI — concurrent reads, low
   latency.

PostGIS is a bonus when data is geographic and leaves the picture when it is
IoT. See [ADR-0005](adr/0005-duckdb-and-postgres-roles.md) for why DuckDB does
not take this role.

**How it runs: CloudNativePG** ([ADR-0012](adr/0012-cloudnative-pg.md)). An
operator in `cnpg-system` reconciles each committed `Cluster` object into
Postgres Pods, disks, Services, and credentials. Roles, databases, and the
pooler are declared the same way when their triggers arrive.

**Backup** ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)): the
Barman Cloud plugin, running beside the operator, archives every WAL segment
and takes a daily base backup into an R2 bucket per environment. Each Cluster
names an `ObjectStore` in `spec.plugins`, and the plugin adds a sidecar to its
Pod. Prod has its bucket; staging was backed up to its own until 2026-11-16,
when its backup went
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)). Procedures are in the
runbook's *Backup and restore*. cert-manager issues the TLS certificates the plugin and
the operator use.

**Credentials.** The prod `Cluster` ([`platform/infra/postgres-prod/`](../platform/infra/postgres-prod/cluster.yaml))
creates the `lagom` database and role, and the operator generates that role's
password into the Secret `postgres-app` in the `prod` namespace. Nothing is
sealed or committed to Git. Consumers read their credentials from that Secret
and must not cache the password: on a rebuild of the cluster a new one is
generated. No superuser password is exposed (`enableSuperuserAccess: false`).

Until the lakehouse trigger it has a third role: **storage and transformation
engine for every layer** ([ADR-0008](adr/0008-postgres-for-all-layers.md)).
Afterwards it returns to holding only the small final result, with raw history
in the table format.

**The `serving` database** (decided; built in staging for the streaming showcase:
[ADR-0041](adr/0041-a-serving-layer-in-postgres-for-the-dashboards.md), amended by
[ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md)). It is what a server resident on the node
reads when it answers visitors: a database named `serving` in the
environment's cluster, and nothing else, not the DuckLake catalog, not R2. A
step of each run publishes the gold tables a board needs into it and fails the
run if they differ from gold; streaming events will land in it directly and be
archived to the lake. Each theme reads through its own read-only role, with a
connection limit and a statement timeout. It is never a source: it can be
dropped and published again from the lake, except the streaming events not
yet archived, which the hub can replay. The static page of §9 is rendered
inside the run and reads gold, so nothing of USGS is published into it.
**It is created for the streaming showcase**
([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)),
staging first: **in staging since 2026-11-17**, owned by the role `serving`,
which dbt runs as, with `events_writer` for Redpanda Connect
([`platform/infra/postgres-staging/`](../platform/infra/postgres-staging/)).
No theme reads it yet; Grafana's read-only role comes with Grafana.

**PgBouncer** — deferred with a trigger: it enters with the first external BI
consumer. Each Postgres connection costs 5–10 MB, and BI tools open them
freely; without pooling a 300 MB Postgres becomes 1 GB. It comes as a
CloudNativePG `Pooler` object, not a separate install.

## 9. Visualization

The site is static: nothing on the node serves it
([ADR-0022](adr/0022-platform-as-a-portfolio-of-architectures.md)). The
dashboards are static too: dbt Charts renders them inside a run and the page
is published as files. A server of our own was designed and stopped
([ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md)).

| Tool | Role | RAM on the node | Enters when |
|---|---|---|---|
| **Astro** | the site at ofantini.com: pages, copy, the gallery of showcases. It lives in its own repository, `FilipeFantini/ofantini.com`, and links to the dashboards by URL ([ADR-0039](adr/0039-the-site-in-its-own-repository.md)) | 0 (static, Cloudflare Pages) | M4 |
| **dbt Charts** | the dashboards: boards in YAML over SQL on gold, rendered to static HTML. The boards and their CI check are in `products/dashboards/`; the job `dashboards` renders and publishes them, built but not yet run in the cluster ([ADR-0040](adr/0040-dbt-charts-for-the-dashboards.md), [ADR-0042](adr/0042-the-dashboards-return-to-dbt-charts.md), [ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)) | 0 resident (a render is about 0.5 GiB at its peak on the CI fixture, in the built image, 2026-11-14) | M4 |
| **Streamlit** | interactive apps | 200–250 MB | an app a static page cannot serve |
| **Grafana** | time series, pipeline health, alerting — for the operator, behind Cloudflare Access ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)) | 150 MB (an estimate, not measured) | with the streaming showcase, reading its live store ([ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md), proposed), or with infrastructure observability, whichever comes first ([roadmap](roadmap.md#trigger-table)); the stack's other components are not in §11 until measured |

**How a dashboard reaches a visitor**
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md),
the operator's decisions of 2026-11-14; built, not yet run in staging). After
each successful `usgs` run a sensor requests the job `dashboards`, whose one
asset runs, in order and stopping at the first thing wrong:

1. `dct validate --strict --warehouse` against the real lake, attached
   **read-only** with the role `lake_reader` (its password is `PGPASSWORD`, not
   part of the connection string) and a read-only R2 token;
2. `dct render` of every board to one HTML file, and to the rows it was drawn from;
3. `check_boards.py`, which compares the rows with gold;
4. the **watermark**: each page says `Page built at <UTC time>` once, above the
   title (a dbt Charts setting, `style.timestamp`), and the step refuses a page
   whose stamp is missing, repeated or more than 15 minutes old, so a page
   left in place by a failed run shows its age;
5. the **upload** of the pages to the environment's R2 public bucket
   (`lagom-staging-dashboards`, `lagom-prod-dashboards`), served on a custom
   domain (`lagom-staging.ofantini.com`, `lagom.ofantini.com`), with a token
   scoped to that bucket; stale pages are deleted only after the last upload.

The render's Pod never holds the owner's password or the lake's write token,
and `dct`, a tool we do not control, never sees the bucket's token or the ping
URL. The job carries the pipeline's tag, so it queues behind a `usgs` run and
the budget in §11 does not change. dbt Charts telemetry stays on in the step
(every command has a timeout, so a stalled request cannot hang a run); Cloudflare
Pages is not used because, as far as is known, its API token is account-wide.
What is not verified yet (R2, the cluster's catalog, telemetry on the node's
network, memory on real data) is the staging checklist in the runbook.

`dct serve`, the server inside dbt Charts, was tried on 2026-11-12 against the
lake (ADR-0041) and on 2026-11-14 against Postgres, and is not deployed: about
400 MiB resident, filter values rendered into SQL as text, a cache that serves
stale numbers, and data routes no setting turns off. Lightdash was tried
locally the same day: about 1.1 GiB at rest and login-only in its open-source
version. A static page that filters in the visitor's browser, drawn with
Vega-Lite, was proposed and rejected
([ADR-0043](adr/0043-the-dashboards-filter-in-the-browser.md)): the target is
the `dct render` page, designed with no filters.

dbt Charts is Apache-2.0 with no per-seat cost, and beta: its syntax is
pre-1.0 and it is pinned. It replaced Evidence on 2026-11-10 after a trial
(frozen npm release, a DuckDB that cannot read the lake, a Node toolchain
beside the Python one). It is built for reviewed reports, not ad-hoc
exploration, and the page `dct render` makes is static: no in-page filters,
search or map zoom. Its render exits 0 on a query that returns nothing, so CI checks every
column of every query a board draws against gold ([`products/dashboards/`](../products/dashboards/README.md)).

Two cautions: the data is **embedded in the page**, and the page has **no
authentication and no row-level security**. A public showcase therefore
publishes only public data. Private data means one build per audience behind
Cloudflare Access — not a permission inside the app.

Metabase, Superset and Lightdash are the self-service BI options, at 600 MB to
1 GB or more each. They enter when a client needs ad-hoc exploration, behind
login.

## 9a. Environments

Three, decided in [ADR-0007](adr/0007-three-environments.md).

| Environment | Where | What runs there |
|---|---|---|
| `dev` | code on the operator's machine, data in staging | dbt and dlt run from the machine's `uv` environments against the `lagom_dev` database in staging's Postgres, through `kubectl port-forward` over the tailnet; unit tests need no database; manifests are checked with a server-side dry run ([ADR-0014](adr/0014-develop-against-staging.md)); agents use `kubectl diff -n <namespace> -f`, since `apply` is denied to them ([CONTRIBUTING](../CONTRIBUTING.md#workflow)) |
| `staging` | namespace on the node | own Postgres `Cluster` mirroring prod's configuration, holding three databases: `lagom` for staging's pipeline, `lagom_dev` for the developer, who cannot connect to `lagom`, and `serving` for the streaming showcase; own secrets; no backup since 2026-11-16 ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md): prod's is the protection); a ResourceQuota (2432Mi of memory requests, 4608Mi of limits, 10 Pods; the arithmetic is in `quota.yaml`) and a LimitRange ([`platform/infra/postgres-staging/`](../platform/infra/postgres-staging/)); **its own Dagster** ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md), superseding ADR-0007's "no Dagster in staging"): daemon and the platform's code location, with no UI since 2026-11-16 (its webserver at zero replicas, [ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)), with its metadata in the database `dagster` of staging's Postgres, running USGS on DuckLake and raising staging's alarm through Healthchecks.io ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)). Staging's CronJob on Postgres was retired on 2026-11-07. **The streaming showcase** since 2026-11-17 ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)): a NATS hub and one Redpanda Connect process, with a synthetic producer, writing to `serving` ([`platform/infra/streaming-staging/`](../platform/infra/streaming-staging/)) |
| `prod` | namespace on the node | the full stack: its Postgres `Cluster` ([`platform/infra/postgres-prod/`](../platform/infra/postgres-prod/)), and its own Dagster with the database `dagster` in that Postgres, running the image staging ran, promoted by digest ([`platform/infra/dagster-prod/`](../platform/infra/dagster-prod/values.yaml)), raising prod's alarm through Healthchecks.io ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)). Prod's CronJob on Postgres was retired on 2026-11-07 |

**Staging on or off** ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md), accepted, replacing the four modes of
[ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md); not built — the table above is how staging runs today, always on).
Staging has two states:

| State | What runs | Requests |
|---|---|---|
| `on` | Postgres, Dagster's daemon and code server, its run, and M6's hub and Redpanda Connect process | 1,900 MiB |
| `off` | nothing: Postgres hibernated (`cnpg.io/hibernation: "on"`: the Pod goes, the volume and its data stay; Argo CD shows it Suspended), no Dagster (daemon and code server removed, since the chart cannot scale them to zero), the hub and Connect at zero replicas | 0 |

- **Lean:** no Dagster UI in staging (`ops.ofantini.com/dagster-staging`
  gone) and no backup (prod's, with its restore test, is the protection),
  both done on 2026-11-16.
- **Switched by agents:** the state is one field in a small file outside the
  guarded list, checked in CI to hold `on` or `off` and nothing else. Only
  staging's Applications read it.
- **The budget check counts staging as on, whatever the file says** (§11), so
  neither value can break the ceiling and a component added to prod can never
  take the room staging needs. What `off` frees is real memory on the node,
  and room to try an experiment.
- **Runs follow the schedules:** with no UI, a change is validated by the next
  `usgs` run; a job off schedule is waited for or launched by the operator.
- **Its alarm reports failures only,** so silence while off raises nothing
  (ADR-0046, decision 3).
- **Built last,** after M6 is in prod: until then staging stays on, which
  gives the streaming pipeline its run of days.

The namespace, the Postgres volume, the secrets and the quota stay
permanent, and prod's NetworkPolicy keeps denying staging, so the four
conditions below still hold. The
cost: while staging is off it does not exercise the pipeline, so a change in
the source between validations is caught by prod and its alarm, not by
staging first; a backup change is validated in prod; and a digest pinned in
staging may never have run there, so the operator's confirmation before
promotion carries more weight. One Grafana, in a namespace of its own, reads
the `serving` database of both environments, each through its own read-only
role; it is the operator's viewer, not a link between the pipelines.

**Promotion** is by image digest: staging and prod each pin their own, so a new
image reaches staging through one pull request and prod through another, both
from `main` ([ADR-0015](adr/0015-images-on-ghcr.md)). CI enforces the order:
[`tools/promotion_check.py`](../tools/promotion_check.py) fails a change whose
prod manifests pin an image without a digest, or a digest staging had not
pinned on `main` before that change — Postgres's `imageName` and the image
in a chart's values file, such as Dagster's, included. It
cannot see whether staging's run succeeded; the operator confirms that before
opening the prod pull request. Beside it,
[`tools/manifest_policy.py`](../tools/manifest_policy.py) fails an Application
with no explicit `prune`, a chart not pinned to an exact version, a Git source
outside this repository, or a rendered image without a tag or digest (this
repository's own images need a digest); what it does not check is in
[`tools/README.md`](../tools/README.md#manifest-policy-check).

**Images.** One, built by [`images.yml`](../.github/workflows/images.yml) on a
native arm64 runner: `ghcr.io/filipefantini/lagom-orchestration`, the Dagster
code location, [`platform/orchestration/Dockerfile`](../platform/orchestration/Dockerfile).
It is built from the repository root and bundles every service's code and the
dbt project. Until 2026-11-07 two more were built, `lagom-usgs` (dlt) and
`lagom-warehouse` (`dbt build`), for the CronJobs on Postgres; they were
retired with them ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)).
Their packages stay in GHCR, no longer updated or pinned.

A pull request builds and smoke-tests it without pushing. A push to `main`
publishes it tagged with the commit SHA, and the digest is printed in the
run summary. The image:

- is multi-stage, with uv only in the build stage;
- is pinned by digest at the base;
- runs as UID 10001 on a read-only root filesystem;
- writes only under `/tmp`, so mount an `emptyDir` there.

Sizes, measured on the first builds: `lagom-usgs` 206 MiB and
`lagom-warehouse` 397 MiB (archive PR #27, 2026-10-17, built in 40 and 52 s),
and `lagom-orchestration` 961 MiB (PR #31, 2026-10-28). The orchestration image
bundles Dagster, dbt, dlt, DuckDB and pyarrow, and since the dashboards' render
dbt Charts and the boards: locally, unpacked, it grew from 1,376 MiB to 1,612 MiB
(+237 MiB; `docker image inspect`, 2026-11-14). The 961 MiB above is the
registry's figure and the growth in it is read from GHCR after CI builds the
image.

The first two packages are private: an anonymous manifest request to ghcr.io
returns HTTP 403 (checked 2026-10-17; the same request against a public image
succeeds). `lagom-orchestration` is checked the same way once it is
published. The CronJob `usgs-pipeline` ran the first two every 30 minutes
until 2026-11-07, when it was retired in both environments
([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)); no manifest
pins them since, and only `lagom-orchestration` runs. Its Pods pull from GHCR
with the sealed `ghcr-pull` Secret, which the operator adds to each
namespace's directory.

Staging is real rather than nominal because of four enforced conditions: its own
Postgres pod and credentials, a ResourceQuota and LimitRange, a NetworkPolicy
denying staging to prod, and separate secrets. A staging that shares prod's
database would prove nothing.

Staging is recreated on demand before structural infrastructure changes, not on a
schedule.

**Accepted limitation:** neither environment is isolated from node failure or
global memory pressure. The trigger for a second VPS is a real client.

## 10. Access channels

Progressively less privileged. Any new access request is answered by choosing
one of the three.

| Channel | Who | What it grants |
|---|---|---|
| **Public site** (ofantini.com) | anyone | static pages and dashboards of public data; no path to the cluster |
| **Tailscale** | operators and admin | raw Postgres and the whole cluster |
| **Cloudflare Access** | operators (web UIs such as Dagster's); later partners, cloud BI | a tool or report in the browser behind a login, no install |
| **Signed file URL** | partners, external pipelines | bytes with an expiry, access to nothing |

### Node shell (SSH)

Not an access channel: a break-glass path for administering the node itself —
installing k3s, upgrading the OS. Day-to-day work never needs it: changes reach
the cluster through a pull request and Argo CD, and inspection goes through
`kubectl` over Tailscale with per-developer RBAC (M7).

- **Personal accounts only.** One Linux user per person, with their own key. A
  shared account makes the log say what happened but not who did it, and
  revoking one person means rotating everyone's key.
- **`sudo` for operators only.** A developer who ever needs a shell gets a user
  outside the `sudo` group.
- Root login and password authentication are disabled; `AllowUsers` lists every
  permitted account explicitly. See the provisioning steps in the
  [runbook](runbook.md#provision-the-node-m0).
- **Reachable only over Tailscale.** The node has no inbound port open to the
  internet; the break-glass path is in the [runbook](runbook.md).

### Tailscale

WireGuard mesh, ACL by identity, **no open ports**. Headscale is the
self-hosted control plane if a fully open-source server is required — the
Tailscale client is already open source.

### Cloudflare Tunnel + Access

Entered on 2026-11-05 with the first content that must not be public: the
operator's own web UIs, Dagster's first
([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)). A light
agent (`cloudflared`) opens an **outbound-only** connection. Access puts
identity in front with per-application policy. The user gets a link, logs in,
and sees the tool — no install, with WAF and rate limiting included. The same
path will serve partners' reports, each with its own Access application and
policy.

The operator's tools share one hostname, `ops.ofantini.com`, one path per
tool (`/dagster`; `/dagster-staging` until staging's UI was removed on
2026-11-16), so a new tool needs no DNS or Access
change; each tool serves itself under its path. What the tunnel serves is the
list of rules in
[`cloudflared/config.yaml`](../platform/infra/cloudflared/config.yaml); any
other hostname or path gets a 404. `cloudflared` also checks the token Access signs
after login, so a request that did not pass the login is refused even if the
Access application is misconfigured.

### Signed URL

**Requires object storage, so it is unavailable until R2 enters (M3, if the
SME-stack spike passes)**
([ADR-0008](adr/0008-postgres-for-all-layers.md)); a consumer that needs it is
itself one of the triggers.

An HMAC over a single object with an embedded expiry, verified by the storage
layer. This is the same mechanism Delta Sharing uses underneath.

**It carries no identity authentication.** Within the TTL, whoever holds the
link has access, and it cannot be revoked before expiry. Therefore: short TTL
(hours, not weeks), one URL per recipient, and emission logged.

Natural evolution: when auditing and revocation are needed, a minimal endpoint
validates a JWT and mints the URL on demand.

### Permission model — one for all channels

| Control | When |
|---|---|
| Read-only role per consumer, `GRANT` on the `gold` schema only | from the start; it is trivial |
| Row Level Security for per-client isolation, enforced by the database | trigger: second consumer with segregated data |
| PgBouncer in front | trigger: first external BI |

**How the read-only role is built.** Each `Cluster` declares a group role,
`gold_reader`: NOLOGIN, so it cannot connect, and no attribute beyond the
default. Its privileges come from dbt (`platform/warehouse/dbt_project.yml`),
which grants `SELECT` on every gold model and `USAGE` on the `gold` schema each
time it builds one, since a rebuilt table loses the grants of the one it
replaces; a missing role fails the build. The CronJobs that built gold on
Postgres were retired on 2026-11-07, so the grants stay on the last tables
they built and are not renewed; on DuckLake, read access takes the form
[ADR-0023](adr/0023-sme-stack-duckdb-on-ducklake.md) gives it (a read-only R2
token and a read-only catalog role). The catalog role exists:
`lake_reader`, a login with at most 3 connections and `SELECT` on the schema
`ducklake`, whose grants the `lake_maintenance` job keeps
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)).
The render stays within that limit by running `dct render --max-workers 1`: its
default of 8 workers, each on its own connection, failed the first staging run
with "too many connections for role" (2026-11-15).
The read-only R2 token comes with the dashboards' render. A data test checks the outcome in the
catalogue: gold readable, bronze and silver not, nothing writable
([warehouse README](../platform/warehouse/README.md)). A consumer gets its own login role inside that group
(`inRoles: [gold_reader]` in the `Cluster`, with its own sealed password), so
each one is revoked alone. No consumer exists yet, so no login role does.

RLS with a single user is theatre, so it is not required in the first commit.
The underlying argument still holds: when PostgREST arrives it maps JWT claims
to Postgres roles, which trigger the same policies. Model once, serve four
channels.

### Why the API, not the database, is the public channel

The asymmetry is structural:

1. **Where authentication happens.** With an API, the proxy terminates TLS and
   rejects the unauthenticated before anything reaches the application. With
   Postgres, the database itself parses the initial packet — the attack surface
   *is* the database.
2. **What is exposed.** An API exposes defined endpoints. Postgres exposes an
   **arbitrary query engine**: whoever authenticates writes any SQL, including
   one that consumes the whole CPU. RLS limits rows, not cost.
3. **Defensive ecosystem.** HTTP has WAF, rate limiting, OAuth, bot detection.
   The Postgres wire protocol has almost none of it.
4. **Credentials.** An API token is scoped, short-lived, and revocable. A
   database password saved in a BI connection is long-lived and stored
   somewhere unknown.

PostgREST is the planned API: it reads the schema and **generates the REST API
itself** — no code, just configuration, around 50 MB, with authorization via
RLS.

## 11. Resource budget

The node, `lagom-shared-02`, shows **7,741 MiB** of RAM to the OS (the rest of
the nominal 8 GB is reserved by firmware and kernel). Two budgets share it,
kept apart because they are enforced differently.

### Node overhead — outside Kubernetes, enforced by nothing

**On `lagom-shared-02`**, 2026-10-28, two hours after k3s started, 12 Pods
running: **1,687 MiB** by RSS — `k3s-server` 1,022, `containerd` 200, 12 shims
254, `tailscaled` 55, the rest of the OS ~156
([ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)). It is to be measured
again after at least three days of uptime.

**On `lagom-shared-01`**, the 4 GB node, the history behind those figures —
measured from 2026-10-12, first with the cluster idle and only k3s's own
system pods running (RSS from `ps`):

| Process | Measured | Earlier estimate |
|---|---|---|
| `k3s-server` — API server, controllers, scheduler, kubelet, kube-proxy, flannel, network policy controller, and the SQLite datastore in one process | 627 MB fresh; 1,235 MB after 17 h; ~730 MB with `GOMEMLIMIT=600MiB`, 761 MiB after 67 h; **983 MiB with `GOMEMLIMIT=900MiB`** on 2026-10-17, after 600MiB caused GC thrash ([ADR-0011](adr/0011-memory-viability-on-4gb.md) notes) | 300 MB "k3s" |
| `containerd` + one shim per running pod | ~265 MB with 7 pods; **~473 MiB with 14** (2026-10-17) | not counted |
| `tailscaled` | 47 MB | "negligible" |
| sshd, journald, rest of the OS | ~20 MB | not counted |
| **Total** | ~1.1 GB in M1; **~1.5 GB** on 2026-10-17 (RSS, overstated by shared pages; 1,622 MiB available on the node) | **0.3 GB** |

Without a memory limit, Go lets the heap grow to about twice its live size and
keeps idle memory from the OS; `GOMEMLIMIT` on every resident Go process is the
largest lever found ([ADR-0011](adr/0011-memory-viability-on-4gb.md)).

The estimate of 300 MB was the commonly quoted figure for k3s; on the 4 GB
node the server process alone was twice that, and the container runtime was missing from
the count. RSS overstates slightly because shared pages are counted per process;
it is the conservative measure.

### Pod requests — the CI ceiling

**Ceiling enforced in CI: 5.5 GB of declared requests.** A change that would
exceed it fails the build — see
[`engineering-protocol.md`](engineering-protocol.md) §16.5. The arithmetic,
from the overhead above: 7,741 MiB visible − 1,687 MiB outside Kubernetes
leaves ~6,050 MiB for pods; the ceiling (5,632 MiB) sits ~420 MiB under that,
for k3s's growth over days and the shims of new Pods, which requests do not
count ([ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)). It was 2.2 GB on
the 4 GB node ([ADR-0016](adr/0016-ci-ceiling-2-2-gb.md)), where M3 did not
fit, hence the move ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)). Today's
total is 4,926 MiB: Dagster in both environments (800 resident in prod, 640 in
staging, which has no UI, and 768 per run), `cloudflared`, 48, which serves their UIs behind a login
([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)), and staging's
streaming showcase, 192 (the NATS hub 64, Redpanda Connect 128;
[ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)).
It was 4,734 before the showcase, and 5,070
until 2026-11-16, when `hello` (16), `argocd-server` (64), 32 of
cert-manager's requests, staging's webserver (160) and staging's backup
sidecar (64) went
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)). It was 5,582,
50 MiB under the ceiling, until the two CronJobs on Postgres were retired on
2026-11-07, which freed 256 each
([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)).

**How CI enforces it.** [`tools/resource_budget.py`](../tools/resource_budget.py)
runs on every pull request ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)).
It renders every Application the way Argo CD does and fails if any container
lacks a memory request, a memory limit, or a CPU request (for a CloudNativePG
`Cluster`, whose Pods the operator creates, its `spec.resources` and
`spec.instances` stand in for the Pod spec, plus the Barman Cloud sidecar
declared on the `ObjectStore` it names; Pods a controller creates at run
time, such as Dagster's runs, are declared on their Application with the
annotation `lagom.dev/ephemeral-memory-request` and counted as ephemeral
jobs), or if an Application that declares runs that way does not limit them
to one at a time (its rendered Dagster instance must queue runs with
`max_concurrent_runs: 1`, since the annotation counts one run;
[ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)),
or if the total
exceeds the ceiling above, which it reads from this file. The total is resident
requests, plus **every ephemeral job at once**, plus an **allowance for
components k3s installs outside Git**.

**Known exception: CoreDNS.** Since [ADR-0021](adr/0021-k3s-packaged-components-under-git.md)
it is the only component k3s still installs: Traefik and servicelb are
disabled, and local-path and metrics-server are Argo CD Applications that the
check counts like any other. CoreDNS is outside Git, so the check cannot see
it, but it declares a request (70 MiB) and a limit (170 MiB), and the check
adds that 70 MiB request as its allowance for components outside Git.

**Core (M1–M3)** — workloads that run as pods:

| Component | Request | Source |
|---|---|---|
| kube-system (CoreDNS, metrics-server) and local-path | ~215 MB | requests: CoreDNS 70, metrics-server 80, local-path 64 MiB; Traefik disabled ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)). Measured 2026-10-27: 71, 73 and 47 MiB |
| Argo CD (no Dex, no UI) | 544 MB | set from measurement with three Applications: application-controller 290–465 MiB before `GOMEMLIMIT`, 190–285 MiB after (request 384, limit 640, `GOMEMLIMIT` 540MiB), repo-server ~105, redis ~10 ([ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md)). `argocd-server` (~37 measured, 64 requested) runs at zero replicas since 2026-11-16 ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)) |
| CloudNativePG operator | 64 MB | measured 24 MiB with no Cluster, 31 MiB reconciling one (request 64, limit 192, `GOMEMLIMIT` 150MiB) ([ADR-0012](adr/0012-cloudnative-pg.md)) |
| Backup: Barman Cloud plugin and cert-manager | 160 MB | plugin 32, cert-manager controller 48, cainjector 48, webhook 32; each with `GOMEMLIMIT`; measured idle on 2026-10-28: 19, 34, 31 and 17 MiB, and on 2026-11-16 the cert-manager three at 39, 35 and 22, from which the controller and cainjector came down from 64 ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)). Each backed-up Postgres instance adds a 64 MiB sidecar (18 MiB measured during a base backup), counted with its Cluster |
| Postgres (prod `Cluster`, 1 instance) | 364 MB | 300 for Postgres plus 64 for the backup sidecar. Postgres: request 300 MiB, limit 512 MiB; `shared_buffers` 96MB, `max_connections` 25, `work_mem` 4MB, `maintenance_work_mem` 32MB, `effective_cache_size` 256MB, instance manager `GOMEMLIMIT` 96MiB. Measured idle on 2026-10-16: pod working set 47 MiB, cgroup 55 MiB (25 anonymous, 25 page cache), instance manager ~62 MiB RSS including shared pages. The request is kept for dbt load, measured in M2. Counted by the CI check, which reads the `Cluster` object because the operator creates its Pods ([`platform/infra/postgres-prod/`](../platform/infra/postgres-prod/cluster.yaml)) |
| Dagster | 800 MB | in prod; staging 640, its webserver at zero replicas since 2026-11-16 ([ADR-0026](adr/0026-dagster-one-instance-per-environment.md), [ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)): webserver 160, daemon 320 and code server 320 MiB of requests, plus each run as an ephemeral job of 768, declared on its Application. Measured in staging by the kubelet: over an hour of runs on 2026-10-28, webserver 127–141, daemon 104–206 and code server 203–291 MiB; after seven days, on 2026-11-05, 138, 263 and 258 MiB. The daemon grows with uptime and is watched. A run peaked at 699 and 674 MiB, during `dbt build` (6–8 samples each, so the true peak may be higher), when each step still ran in a second Python process; its limit is 1 GiB. With steps in one process (image d6716cc), on 2026-11-05: 623, 631 and 640 MiB in staging; 550 MiB in prod's first run, which loaded 30 days (10,042 events), and 442 in its next. The request stays at 768: the samples are 2–15 s apart and miss short peaks, and lowering it by 64–128 MiB would need a new image. A run's resources are a tag on each job in the code location, since a run would otherwise inherit the code server's; runs execute their steps in one process ([`definitions.py`](../platform/orchestration/lagom_orchestration/definitions.py)) |
| `cloudflared` | 48 MB | the tunnel that serves the operator's UIs behind Cloudflare Access, both environments ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)): request 48 MiB, limit 128 MiB, `GOMEMLIMIT` 100MiB. Measured 24 MiB at start and 31 MiB a few minutes later, on 2026-11-07, idle (Cloudflare's figure is ~30 MB); the request is re-set from `kubectl top` after a week |
| **Core requests** | **~2.20 GB** | |
| **Left within the ceiling for ephemeral jobs** | **~3.4 GB** | |

Staging adds ~1.13 GB:

- its own Postgres, 300 MB ([ADR-0007](adr/0007-three-environments.md)): a
  second `Cluster` with the same request and limit as prod's
  ([`platform/infra/postgres-staging/`](../platform/infra/postgres-staging/cluster.yaml)),
  with no backup sidecar since 2026-11-16;
- its own Dagster, 640 MB: daemon and code server, with no webserver since
  2026-11-16;
- the streaming showcase, 192 MB: the NATS hub (request 64, limit 128,
  `GOMEMLIMIT` 96MiB) and one Redpanda Connect process (request 128, limit
  256, `GOMEMLIMIT` 200MiB), from the spikes' 58 and 121 MiB of working set
  ([`platform/infra/streaming-staging/`](../platform/infra/streaming-staging/)).
  Both are re-set from 24 hours in staging (ADR-0047, decision 8).

That leaves **~2.3 GB** for jobs. Staging's ResourceQuota caps everything
staging requests at 2432 MiB. Staging requests 1,900 MiB in all (Postgres 300,
Dagster 640, its run 768, the hub and Connect 192; 1,708 before the
showcase and 1,932 until 2026-11-16, when its UI and backup went); under
[ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md) (accepted)
it will be 0 while staging is off (below).
dlt and dbt do not appear above: they are ephemeral jobs, and their requests
count against what is left. Since 2026-11-07 they run inside each Dagster
run (768 MiB, above). Until then they ran as one Pod per environment, the
CronJob `usgs-pipeline`, retired that day: dlt and dbt each requested 256 MiB and run one after the other
as init containers, and the `heartbeat` container requests 32 MiB, so the Pod
requests 256 MiB, not 544 (a Pod's request is the larger of its biggest init
container and the sum of its regular ones). Prod ran the same Pod on the same schedule, so at :00 and :30 both
were placed together, and a run started by hand can land on either. The CI
check therefore adds every ephemeral job rather than taking the largest: until
2026-10-19 it took the largest, which left out the second pipeline's 256 MiB
and the 32 MiB Job that the Argo CD chart runs as a hook on its syncs. The
total rose by 288 MiB, to 2,034 of the 2,253 MiB ceiling of the time. The sum overstates
jobs whose schedules never meet, which is the safe error for a memory gate.
The 256 MiB is a starting
value. First scheduled run, 2026-10-18 20:30 (8 new events, 15 s): one
kubelet sample per container gave dlt ~60 MiB and dbt ~151 MiB — a floor,
not a peak, since the stats refresh every ~10 s; the request stays until a
longer run is sampled. dbt's SQL runs inside Postgres while every layer
lives there ([ADR-0008](adr/0008-postgres-for-all-layers.md)). Sealed Secrets
declares 32 MB and uses 16 MiB measured ([ADR-0010](adr/0010-sealed-secrets.md)), small enough to leave
the totals above unchanged at this precision. The dashboards' render is an
ephemeral job too, in the same one-run slot as `usgs` (the job carries its tag,
so the queue never runs both): the budget does not change. On the CI fixture,
in the built image under a 1 GiB limit, a render peaked at 540 MiB (2026-11-14).
On real data, the whole run Pod in staging (Dagster's run process, `dct` and
`check_boards`, from the container's cgroup) peaked at 804 MiB on 2026-11-15:
above the 768 MiB request, which the node can give for the minute the render
lasts, and 220 MiB under the 1 GiB limit
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md); runbook: *The dashboards' publishing path*).

**Full design**, adding what enters by trigger. The figures below were
estimated before M6's spikes; the streaming rows are replaced by the next
table:

| Addition | RAM |
|---|---|
| NATS + Redpanda Connect | 120 MB |
| Streamlit | 250 MB |
| Grafana | 150 MB |
| PgBouncer | 5 MB |
| **Core + staging + additions** | **~4.0 GB of requests** |
| **Left within the ceiling for ephemeral jobs** | **~1.5 GB** |

On the 4 GB node the full design left no room for jobs, which is why the
platform moved to the CAX21 ([ADR-0020](adr/0020-move-to-the-8-gb-node.md)).
On the 8 GB node it fits with about 1.5 GB to spare; that room is the margin
the node allows, not a budget to fill (§1).

**M6, streaming, and what makes room for it** ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md), accepted, and
[ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md), accepted, which takes the stream processor out of it). M6's local
spikes measured RisingWave at a 768 MiB request, the NATS hub at 64 and a
Redpanda Connect process at 128; Grafana's 128 is not measured. With a hub,
RisingWave and Grafana per environment, M6 would have taken the total to
about 7,100 MiB. What makes it fit:

- **no stream processor** (ADR-0047): events go to Postgres and their windows
  are computed when read (§4), so RisingWave's 768 MiB per environment are
  not requested. M6 adds 320 MiB in prod (hub 64, Connect 128, Grafana 128)
  and 192 in staging (hub 64, and one Connect process, 128, running the
  synthetic producer and the consumer);
- **one Grafana** for both environments, 128;
- **`hello` removed**, 16 (done on 2026-11-16);
- **one run slot per environment for every batch job** (done on 2026-11-16):
  Dagster's run queue holds one run at a time (`maxConcurrentRuns: 1`; until
  then only runs tagged `lagom/pipeline: usgs` were limited, and the chart's
  default is no limit), so a new batch source adds no request; the budget
  check fails an Application that declares a run slot without that limit;
- **shared streaming components**: one hub and one Redpanda Connect process
  in streams mode per environment, for every streaming source;
- **platform requests trimmed to what was measured** (done on 2026-11-16):
  `argocd-server` at zero replicas (64; the operator uses the CLI with
  `--core`, not the UI), cert-manager's controller and cainjector at 48 each
  (32). The Postgres requests come down to 256 (44 each) only after a reading
  taken with events being written (ADR-0047, decision 11);
- **a staging without its Dagster UI and its backup** (done on 2026-11-16),
  224.

**The check counts staging as on, always** (ADR-0047, decision 10; to be
built with the state file). Staging can be hibernated (§9a), but the room
that frees is for real memory and for experiments, not for prod to grow
into: a resident component added to prod must fit beside a whole staging.
Requests in MiB:

| Point | Total | Room under 5,632 |
|---|---|---|
| Before ADR-0046's first trims (2026-11-16) | 5,070 | 562 |
| Today (`main` at `2a74ef2`, 2026-11-16) | 4,734 | 898 |
| Prod with M6, staging off: what runs, not what the check counts | 3,346 | 2,286 |
| … staging off, with RisingWave as an experiment (768; ADR-0047, decision 9) | 4,114 | 1,518 |
| Prod with M6, staging on (1,900): what the check counts | **5,246** | **386** |
| … with both Postgres at 256 | 5,158 | 474 |

**Prod's permanent growth after M6 is bounded by 386 MiB.** The next room is
the Postgres trim, and then replacing Argo CD with Flux (about 400 to 450 of
Argo CD's 544, not measured), whose spike is parked until a component does
not fit (ADR-0047, decision 12).

## 12. Accepted risks

- **No high availability.** A single node; maintenance takes everything down.
  Acceptable at this stage, but it is a choice, not an oversight.
- **The Pi's SD card is a worse bottleneck than its RAM.** No USB3, and constant
  writes kill cards in months. Local disk is never a hot layer. The one
  exception proposed is the NATS leaf stream of the streaming showcase, small
  and capped by size, its write volume measured before it is deployed
  ([ADR-0045](adr/0045-streaming-from-a-raspberry-pi-outside-the-cluster.md)).
- **Swap is a trap.** With swap on SD the system does not die, it becomes slow
  enough to look dead — harder to debug. Better to cap pod memory and let the
  OOM killer act quickly and explicitly.
- **etcd on shared vCPU is sensitive** (it depends on fast fsync). Relevant if
  HA or Talos is ever adopted.
- **Scaling.** Vertical is easy — Hetzner resizes, one reboot. Horizontal needs
  care with the datastore (SQLite to etcd, three nodes) and with storage
  (local-path pins the volume to a node; either node affinity or Longhorn).
- **Postgres backup: paid on 2026-10-28, with one open check.** The debt
  accepted in [ADR-0019](adr/0019-postgres-backup-deferred.md) is paid by
  [ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md): prod archives
  WAL and takes daily backups to R2 (staging did too until 2026-11-16,
  ADR-0046), and a restore was
  tested (runbook, *Backup and restore*). Retention deleted its first expired
  backup on 2026-11-05 (ADR-0025 notes). Still unproven: a rebuild of the node
  that restores from the bucket, instead of re-loading, has not been run.
- **Changes to the gates themselves are protected by convention only, for
  the operator.** Branch protection (since 2026-10-27) requires the checks to
  pass, but a pull request runs the workflow as it edits it. A change to the
  gates touches a guarded path, so it waits for the operator's approval of
  the `guardrail` environment, which no agent's token can give
  ([ADR-0035](adr/0035-agents-use-the-operators-scoped-token.md)), and the reviewer runs from `main`, out of a pull
  request's reach ([ADR-0036](adr/0036-the-reviewer-as-a-required-check.md)).
  The Mac session's token can, as an administrator, edit rulesets and
  environments; for it the gates hold by convention — see
  [CONTRIBUTING](../CONTRIBUTING.md#branch-protection).
- **Scanners find known defects, not intent.** Dependabot reports published
  vulnerabilities in dependencies and CodeQL blocks new high-severity code
  findings ([ADR-0048](adr/0048-dependency-and-code-scanning-for-the-public-repository.md)).
  Harmful code that looks ordinary — written by an agent steered by text it
  read, say — passes both. What stops it reaching the node is the reviewer
  and the operator's approval of each pin.

## 13. Runbook

The operational procedures — provisioning, deploys, backups, recovery — are
in [`runbook.md`](runbook.md).
