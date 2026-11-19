# ADR-0052 — The streaming showcase leaves the platform

- **Status:** Accepted, taken by the operator in conversation on 2026-11-19.
  Decision 1 is final; how it is carried out, the budget figures and the list
  of what is left in the cluster are the architect's.
- **Date:** 2026-11-19
- **Decided by:** the operator, on 2026-11-19.
- **Supersedes:**
  - [ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md):
    the streaming showcase, its Raspberry Pi source and its NATS log.
  - [ADR-0047](0047-streaming-without-a-stream-processor-and-staging-on-or-off.md):
    events in Postgres and the staging that is on or off.
  - [ADR-0050](0050-the-pi-streaming-runtime-and-private-hub-link.md): the
    Pi's runtime and its private link to the hub.
- **Amends:**
  - [ADR-0046](0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md):
    what it made for streaming goes (decision 4's Grafana, decision 7's shared
    streaming components, decision 9's archive job, and the staging modes of
    decisions 1 and 2, already replaced by ADR-0047's on or off, which is not
    built either). What stays is decision 5, decision 6, decision 8 and the
    lean staging of decision 1; see *Decision* 4.
  - [ADR-0042](0042-the-dashboards-return-to-dbt-charts.md), decision 6
    ("Streaming gets a tool of its own"): there is no streaming, so no tool.
  - [ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md),
    decision 4 (where the events land): there are none. The `serving` database
    is back to what ADR-0042's decision 5 says: built when a resident reader is.
- **Restores:** [ADR-0003](0003-ingestion-split-dlt-benthos.md)'s deferral of
  NATS, which ADR-0045 had amended, and
  [ADR-0002](0002-single-node-k3s-hetzner.md)'s single node: no Raspberry Pi
  is part of the platform.

## Context and problem

M6, the streaming showcase, was to prove that the platform grows by
composition with a source of continuous events: a Raspberry Pi at the
operator's home sending its own health and the quality of its internet link
through a NATS hub on the node to Postgres, with Grafana for the operator
(ADR-0045, ADR-0047, ADR-0050).

By 2026-11-19 the cluster half ran in staging on synthetic events and had passed
the criteria ADR-0047 set for it. The edge half, the Pi, was written and tested
in a lab but never deployed, and the project around it was not going well: the
source was a 1 GB single-board computer on a home link, the work to keep it
honest (clock gating, a private path to the hub, a restricted credential, an
SD card's wear) was growing faster than what it showed, and the showcase was
taking the operator's time and the node's memory away from the batch data
platform that is the point of Lagom.

The operator decided to put streaming aside and focus on batch data and on
better dashboards (dbt Charts, ADR-0042).

## Decision

1. **The streaming showcase leaves the platform entirely, and Lagom is
   batch-only.** What goes, in one change:
   - the Pi edge (`platform/edge/pi/`) and its CI lab;
   - the NATS hub, Redpanda Connect and their sealed credential
     (`platform/infra/streaming-staging/`, its Application);
   - the database `serving` and its roles `serving`, `events_writer` and
     `grafana_reader`, with their sealed passwords, in staging's Postgres;
   - the dbt models, macros and tests of the streaming showcase, the `serving`
     dbt target and its CI job;
   - Dagster's `streaming` and `streaming_archive` jobs, schedules and
     resources, the `LAGOM_STREAMING` switch, and the dependency on
     `dbt-postgres` that only the `serving` target needed;
   - Grafana (`platform/infra/grafana/`, its Application, the data source and
     the dashboard), and its route in the Cloudflare tunnel.
2. **Grafana leaves with it.** It served only the showcase. If the operator
   later wants a viewer for the platform's own metrics, that is the
   infrastructure-observability trigger in the roadmap, with its own ADR.
3. **Staging stays always on.** ADR-0047's plan of a staging that is on or
   off existed to make room for the showcase; it was never built and is
   dropped. The budget check counts staging as running, as it always did.
4. **What stays of ADR-0046**, because none of it is about streaming:
   `hello` removed (decision 5); one run slot per environment, `maxConcurrentRuns:
   1`, and the budget check that fails an Application without it (decision 6);
   `argocd-server` at zero replicas and cert-manager's requests at 48 MiB
   (decision 8); staging without its Dagster UI and without its backup
   (decision 1's lean staging, with `/dagster-staging` gone from the tunnel).
   The open item of decision 8, both Postgres at 256 MiB after a reading taken
   during a run, stays open. Decision 3, staging's alarm on failures only, was
   made for a staging that can be off; it was never built and, with staging
   always on, is not needed: staging keeps its success heartbeat.
5. **Nothing is built in its place.** No placeholder for a future event
   source: ADR-0003's split between batch and event sources stands as a
   decision, with NATS deferred as it was, until a source needs it.

## Consequences

**Memory.** The budget check's total falls from **5,182 MiB to 4,734 MiB**, a
fall of 448 MiB: Grafana 256, Redpanda Connect 128, the NATS hub 64. The
ceiling is 5,632 MiB, so the room is **898 MiB**, the figure of
2026-11-16 after ADR-0046's trims. Staging's quota (2,432 MiB of requests)
is unchanged and now holds 1,708. The Postgres of staging loses three roles
and one database in its configuration, and a NetworkPolicy rule for Grafana.

**What is simpler.** One Postgres database fewer to reason about, no
`LAGOM_STREAMING` switch to get wrong, two CI jobs fewer (neither was a
required check), the tunnel serves one tool, two Argo CD Applications fewer,
and the orchestration image drops `dbt-postgres`. `AGENTS.md` §10 no longer
names the Pi among what the handoff agent must not open.

**What is lost.** The streaming showcase, and with it the demonstration of the
platform ingesting events. What was measured and decided stays in the three
superseded ADRs and their notes: the criteria ADR-0047 passed in staging, the
memory of the hub and of Connect over 12 hours, the query times of the views.

**Left in the cluster, outside Git.** Removing files from Git does not remove
every object they created, because of how Argo CD is set up here. The
operator or an agent deletes each one by hand after the change has synced,
and a note below records it when done:

- The Applications `streaming-staging` and `grafana` disappear when `root`
  prunes them, but they had no `resources-finalizer`, so Argo CD deletes only the
  Application objects: the NATS hub and its 4 Gi volume, Redpanda Connect,
  Grafana and its volume, and the `grafana` namespace keep running until
  deleted.
- `postgres-staging` never prunes (runbook: *Retire a manifest from
  `postgres-<env>`*): the `Database` `serving` and the three sealed secrets
  stay as objects. The `Database` has CloudNativePG's default reclaim policy,
  `retain`, so deleting it does not drop the database; the roles are
  not dropped by removing them from the `Cluster`'s `managed.roles`. They
  are dropped by hand, as the superuser, through the instance's local socket.
- The Parquet files of `lake.bronze.streaming_events` in staging's lake (R2
  bucket `lagom-staging-lake`, catalog in staging's `lagom` database), at
  most 30 days of synthetic events: the table is dropped by hand and
  `lake_maintenance` then removes its files.
- In Tailscale, the tag `tag:lagom-pi` and its ACL grants (the Pi's access to
  the node's port 7422 and ICMP, and the operator's SSH to the Pi).
- In Cloudflare, nothing: the Access application covers the hostname
  `ops.ofantini.com`, every path, and the route was a line in `config.yaml`.
- Staging's Dagster is pinned to an image built when the streaming jobs still
  existed. Without `LAGOM_STREAMING` they do not load; the next pin comes from
  an image built after this change, and prod follows by the promotion check.

## Risks, cost and reversal

**Risk.** The leftovers above run, and the NATS hub keeps accepting events
and Connect keeps writing to `serving`, until they are deleted: the
cost is the 448 MiB they hold, which the budget check, which reads Git, no
longer counts. They are deleted soon after the merge.

**Reversal.** The files are in Git history: the last commit with the whole
showcase is `accaf8c`, and `platform/edge/pi/` and the manifests can be read
there. To bring streaming back, write a new ADR that supersedes this one; the
superseded ADRs hold the decisions and the measurements it would start from.
The roadmap's M6 would be reopened and the budget re-counted against the
ceiling.

## Notes after acceptance

- **2026-11-19 — removed from Git.** The change that carries this ADR removes
  the files above; the budget check reads 4,734 of 5,632 MiB. What is left in
  the cluster is not yet deleted.
