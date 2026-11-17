# ADR-0047 — Streaming without a stream processor: events in Postgres, and a staging that is on or off

- **Status:** Accepted (#106, merged 2026-11-16). Taken by the operator in conversation on 2026-11-16:
  the essential pipeline first, with no stream processor in it; a staging
  that can be hibernated, in two states, so that the node has room to try an
  idea; an events table that a crash may empty, which a personal project
  tolerates; and RisingWave tried afterwards, as a second reader of the same
  log.
  How each is carried out, the checks and the figures are the architect's
  proposal, accepted when this pull request is approved.
- **Date:** 2026-11-16
- **Supersedes:**
  - [ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md),
    decision 6: neither RisingWave nor the two-path fallback is built.
  - [ADR-0046](0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md),
    decisions 1 and 2 where they define four modes (two states replace
    them), and decision 9 (the archive job is validated in staging).
- **Amends:**
  - ADR-0045, decision 3 (Redpanda Connect consumes the hub on the node, in
    both environments), decision 7 (events are stored in `serving`),
    decision 8 (the events are kept out of the backup from the start) and
    decision 9 (Grafana reads `serving`).
  - ADR-0046, decision 4 (the live store Grafana reads is Postgres),
    decision 7 (no stream processor among the shared components) and
    decision 8 (when the Postgres requests are trimmed). The Flux spike it
    announced is no longer on M6's path.
  - [ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md): the
    `serving` database is created in M6, by this showcase.
- **Stands, from ADR-0046:** what is already built (`hello` removed, Argo CD
  without its server, cert-manager trimmed, one run slot per environment,
  staging without its Dagster UI and its backup), decision 3 (staging's alarm
  reports failures only), one Grafana for both environments, and streaming
  sources sharing one hub and one Redpanda Connect process.

## Context and problem

ADR-0045 chose RisingWave, a streaming database, to process the events of the
streaming showcase, and ADR-0046 made room for it. RisingWave's request,
768 MiB in each environment, is the single cause of most of what ADR-0046
then had to build: a staging that runs one of four modes at a time because
batch and streaming do not fit together, a mode file outside the guarded
list, a budget check that reads the mode, an archive job that staging can
never validate, a spike to replace Argo CD, and 90 MiB of room at the end.

The load is about five events a second. ADR-0046 recorded, among its
alternatives, a design with no stream processor that is correct at that
load, and rejected it as "not a design built for streaming".

On 2026-11-16 the operator restated the aim: learn by building, starting
with the essential that works; prove that batch and streaming run with
quality on a very small node; pull in no tool for the look of it. And a
second one: whenever an idea comes, the node is already full, so free room
has a value of its own, and since agents, not the operator, are the ones who
use staging, hibernating it is acceptable.

## Measurement

`cd tools && uv run python resource_budget.py --repo ..`, on `main` at
`2a74ef2`, 2026-11-16: **4,734 MiB** of 5,632. Of these, staging is 1,708
(Postgres 300, Dagster's daemon 320 and code server 320, its run 768), and
prod with the platform is 3,026.

From M6's local spikes, restated in ADR-0046: the NATS hub's working set
reached 58 MiB (request 64) and Redpanda Connect's 121 MiB (request 128).
RisingWave's p95 working set was 718 MiB over one hour (request 768).
Grafana is not measured; its request of 128 MiB is a guess.

## Requirements and constraints

- Those of ADR-0045 that do not name a processor: a durable log that can be
  replayed, no event lost when the home link is down, an event delivered
  twice stored once, windows over *event time* (when the event happened, not
  when it arrived), the privacy rule and the limit on R2.
- Each metric is defined once.
- Staging validates what reaches prod, batch and streaming, on the cluster.
- The ceiling stays a check, and no value an agent can set may break it.
- No bigger node.

## Alternatives considered

**ADR-0046 as accepted: RisingWave, and a staging in four modes.** It fits
on paper, with 90 MiB in batch mode. Rejected by the operator: a stream
processor and a second database for five events a second, paid for with the
mode machinery, a job never validated on the cluster, and no room left.

**The two-path fallback of ADR-0045** (a live, approximate path and a batch,
correct one). Rejected: the same metric is defined twice and the two can
disagree. At this load one path in Postgres is both live and correct.

**Windows computed inside Redpanda Connect**, which is already in the
budget, accepting that events arriving after a window closed are dropped
from it. Rejected: the late events are the outage. While the home link is
down the Pi keeps measuring and the leaf keeps the events; they arrive
together when it returns, and a window that had closed would show a gap
where the outage was, the first question of ADR-0045's decision 5. Computing
on read counts them at no cost.

**Staging always on.** With this ADR's streaming design it fits, with
386 MiB to spare, and it is the simplest. Rejected by the operator: the
room hibernation frees is where an idea can be tried, and the operator
rarely uses staging.

**The four modes kept, without RisingWave.** Not needed: staging's batch and
streaming workloads now fit together, so there is nothing to choose between.

## Decision

1. **The log stays; the stream processor comes second.** The edge
   and the log are ADR-0045's, unchanged: Redpanda Connect and a NATS leaf
   node on the Pi, a NATS JetStream hub on the node whose stream sources the
   Pi's. What changes is what reads the hub:

   ```
   Pi:    sensors, ping -> Redpanda Connect -> NATS leaf node (stream on disk)
                                                    |  tailnet, outbound only
   node:  NATS hub -> Redpanda Connect -> Postgres, serving.events
                                               |-> views (dbt) -> Grafana
                                               |-> Dagster -> lake (history)
   ```

2. **Redpanda Connect writes each event to Postgres, idempotently.** It
   reads the hub through a *durable consumer*, a position the hub keeps for
   it, and acknowledges a message only after the row is written; so a
   restart resumes where it stopped and a failed write is delivered again.
   The table `events`, in the database `serving`
   ([ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md),
   decision 4), is append-only with a primary key on the event identifier,
   and the insert ignores a row whose key exists. Delivery is at-least-once;
   the key makes storing it exactly once. The writer adds `received_at`.
   Connect holds a credential that can insert into that table and nothing
   else.

3. **Windows are computed when read, by views defined in dbt.** A view
   groups events by `collected_at`. A late event is a row with an old
   `collected_at`: the next query counts it, and no window ever closes. Each
   metric of ADR-0045's decision 5 is one dbt model, with its tests; dbt
   creates the views and tests them from a Dagster job, in the run slot that
   exists. Each view has an enforced dbt contract, the names and types of
   its columns, and Grafana's panels read those views and nothing else: what
   computes a metric can then change behind its view without a panel being
   rewritten (decision 9). How the Postgres target sits beside the DuckLake one in
   [`platform/warehouse/`](../../platform/warehouse/) is settled in the
   implementation pull request.

   **What this is not.** It is a streaming pipeline with aggregation on
   read, not stream processing: nothing keeps a result up to date as events
   arrive, and there are no watermarks. That part of the subject is
   decision 9's.

4. **The hub can always rebuild the table.** The hub's stream keeps events
   at least as long as the live table does (7 days to start, ADR-0045
   decision 7), so `serving.events` is a projection of the log: deleting the
   durable consumer makes Connect read the stream from its start, and the
   primary key makes that safe over a table that is partly full. The runbook
   gets this as a procedure. The hub's size on disk is measured.

5. **The table is `UNLOGGED`: the log is the record, the table a copy.**
   Postgres does not write such a table to its write-ahead log, so the
   events do not reach R2 through prod's backup, and this showcase adds
   nothing to it (ADR-0045, decision 8, taken from the start instead of
   after a measurement). The price: Postgres empties an unlogged table after
   a crash, and a restore brings it back empty. Decision 4 repairs both, by
   hand, from the runbook. Until then the live window is empty or partial;
   the operator accepts that. What makes it loud: the Dagster job of
   decision 3 runs on a schedule, and one of its dbt tests fails when the
   live window holds far fewer events than one a second per signal, which
   raises the environment's alarm.

6. **The archive job is an ordinary batch job, validated in staging.** A
   Dagster job reads `serving.events`, writes DuckLake, and deletes a range
   from the live table only after its archive succeeded (ADR-0045,
   decision 7). It runs in the run slot, on the cluster, in staging first.

7. **Grafana reads `serving`.** One Grafana, as ADR-0046's decision 4 sets,
   with one read-only role per environment that can connect to `serving`
   only, with a connection limit and a statement timeout. Prod's
   NetworkPolicy is amended to admit Grafana's namespace to Postgres's port
   and nothing else. Postgres allows 25 connections; Connect's pool and
   Grafana's limit are sized inside it in the implementation.

8. **The design passes if all of these hold, in staging, before prod:**
   - Connect restarted while events flow: none missing, none doubled;
   - the synthetic producer's link cut for an hour and restored: the backlog
     arrives and the windows of that hour are complete;
   - Postgres's Pod deleted while events flow: none missing after it returns;
   - the table emptied and rebuilt from the hub (decision 4): the same rows;
   - over 24 hours: the memory of the hub, Connect and Postgres inside their
     requests, the hub's size on disk, and every panel's query under two
     seconds over the 7-day window.

   If a query is slow, the first remedy is an index, and the second a
   per-minute table built by a dbt incremental model that recomputes the
   minutes touched by rows whose `received_at` is newer than its last run.
   Neither is built before a measurement asks for it.

9. **RisingWave is tried next, as a second consumer of the same hub.** A
   second phase, after decision 10 is built, with an ADR of its own. Each
   consumer of a JetStream stream has its own position, so adding one
   changes nothing that exists: the Pi, the hub, the Connect process, the
   table and the archive job stay as they are.
   - It runs in a namespace of its own, reads prod's hub through a NATS
     account that can only read, and keeps its own materialized views of the
     same metrics, as dbt models for its adapter. Grafana gets a second data
     source.
   - It is an experiment in the room decision 10 frees: prod with M6 and
     RisingWave's 768 MiB is 4,114 MiB with staging off, and does not fit
     beside a staging that is on (6,014). So staging is off while it runs.
   - While it runs, each metric is defined twice, once per engine, against
     this ADR's own requirement. That is the experiment: the same events,
     the same question, a view computed on read and a view kept up to date,
     compared on latency, memory and what each does with a late event.
   - It stays only if that comparison shows a gain worth its memory and it
     fits beside a whole staging, which today needs about 380 MiB found
     elsewhere (decision 12). If it stays, the views of decision 3 keep
     their contracts and read from it; the raw events and the archive may
     stay in Postgres. If it does not, its Application is removed and
     nothing else changes.

   The spikes' measurements of RisingWave stay in ADR-0045's notes.

10. **Staging is on or off.** Two states replace ADR-0046's four modes:

    | State | What runs | Requests (MiB) |
    |---|---|---|
    | `on` | Postgres 300, Dagster's daemon 320 and code server 320, its run 768, the hub 64, Connect 128 (the synthetic producer and the consumer in one process) | 1,900 |
    | `off` | nothing | 0 |

    - **Off** is carried out as ADR-0046's decision 1 describes: Postgres
      hibernated by the annotation `cnpg.io/hibernation`, which removes the
      Pod and keeps the volume; Dagster's daemon and code server removed,
      since the chart cannot scale them to zero; the hub and Connect at zero
      replicas. The namespace, volumes, secrets and quota stay.
    - **The state is one field in a small file outside the guarded list,**
      `state`, `on` or `off`, checked in CI to hold nothing else, read by
      staging's Applications only. An agent switches staging on to validate
      and off when done, with no approval from the operator. The mechanism
      is a guardrail change, approved once, in its pull request.
    - **The budget check counts staging as on, whatever the file says.** So
      prod and a whole staging always fit, a resident component added to
      prod can never take the room staging needs, and neither value of the
      file can break the ceiling: the worst an agent can do with it is leave
      staging on, which wastes memory and breaks nothing.
    - **What hibernation frees is real memory, and room for experiments.**
      An experiment larger than the room left beside a running staging
      needs the check to count the declared state for it; that exemption is
      written with the first experiment that needs one, in its own pull
      request, on a guarded path. It is not built now.
    - **It is built last,** after M6 is in prod. Until then staging stays on,
      which gives the streaming pipeline its only run of days.
    - Dagster's daemon keeps its request of 320 MiB, and the `dev` mode goes:
      developing against `lagom_dev`
      ([ADR-0014](0014-develop-against-staging.md)) needs staging on.
    - Staging's ResourceQuota is set to the `on` total plus a small margin.

11. **The Postgres requests are trimmed from a reading taken with events
    being written,** in staging first. ADR-0046's decision 8 read prod
    between and during batch runs; Postgres is now the live store too. The
    plan fits without the trim.

12. **Flux is not on M6's path.** The spike ADR-0046 announced is parked.
    It is reconsidered when a resident component for prod does not fit
    beside a whole staging.

### What fits

Requests in MiB, with the CI check's accounting. The sums are from the
requests above.

| Point | Total | Room under 5,632 |
|---|---|---|
| Today (`main` at `2a74ef2`) | 4,734 | 898 |
| Prod with M6 (hub 64, Connect 128, one Grafana 128), staging off | 3,346 | 2,286 |
| … staging off, with decision 9's RisingWave (768) | 4,114 | 1,518 |
| Prod with M6, staging on: what the check counts | **5,246** | **386** |
| … with both Postgres at 256 | 5,158 | 474 |

## Consequences

**Positive.**

- M6 adds 512 MiB of requests across both environments, where ADR-0045's
  design, as built by ADR-0046, added RisingWave's 768 in each.
- Staging validates batch, streaming and the archive job together, on the
  cluster.
- No second database to operate; the events' store is the Postgres already
  backed up, monitored and understood.
- Each metric is defined once, in dbt.
- The state file is safe by construction, so it sits outside the guarded
  list for a simpler reason than ADR-0046's mode file did.
- The showcase's events add nothing to the Postgres backup or to R2 beyond
  their files in the lake.
- With staging off there are about 2,300 MiB of requests free, for trying
  what would otherwise not fit. RisingWave is the first to use it, read
  beside the design it would replace instead of in place of it.

**Negative.**

- **Stream processing comes second** (decisions 3 and 9). M6 reaches prod
  without it, and RisingWave may end as an experiment that is removed.
- **Postgres carries a third kind of work,** beside the lake's catalog and
  Dagster's history: a table that takes five inserts a second and the
  queries of Grafana. A slow query or a full connection pool there now
  touches batch.
- **Aggregation on read has a ceiling.** Every panel scans the window it
  shows. Decision 8 measures it and decision 9 says when it stops being
  enough.
- **A crash of Postgres empties the live window** until the hub is replayed
  by hand (decisions 4 and 5). The archived history in the lake is not
  touched, and events not yet archived are still in the hub.
- **While staging is off nothing runs there:** a change in the source is
  caught by prod and its alarm first, and a digest pinned in staging may
  never have run there (ADR-0046, *Negative*, unchanged).
- **Permanent growth in prod is bounded by the room beside a whole
  staging,** 386 MiB, not by what hibernation frees.
- **Two agents share one switch.** One may find staging switched off by the
  other in the middle of a validation; it fails visibly and costs a retry.
- **The article on kappa against lambda** that ADR-0045 planned changes
  subject: what streaming needs at this load, and, from decision 9, a view
  computed on read against one kept up to date, on the same events.

## Risks, cost, and reversal

- **The component names are from memory of Redpanda Connect's
  documentation:** a JetStream input with a durable consumer, and an SQL
  insert output whose statement can ignore a conflict. Both, and their
  licence tier, are checked when the version is pinned.
- **That dbt enforces a contract on a view** in its Postgres adapter, and
  that a durable consumer deleted and recreated reads the stream from its
  start, are from memory of the two tools' documentation, and are shown by
  decision 8's fourth criterion.
- **A crash between an archive run and the next** leaves the table empty
  with events only in the hub; the hub's retention (decision 4) is what
  covers it, and is why it may not be set below the live table's.
- **CloudNativePG's hibernation** is read in its documentation, not run
  (ADR-0046, *Risks*). The first switch-off is its test.
- **The 386 MiB assume Grafana at 128,** not measured.
- **Cost:** none in money.
- **Reversal:** the table becomes logged again with one statement, if the
  empty window after a crash turns out to matter. RisingWave replaces the
  views' source by decision 9, with nothing of the edge or the log to
  change. Staging returns to always on by leaving the file at `on`.

**Reconsider when** a criterion of decision 8 fails, decision 9's comparison
reports, or a resident component for prod does not fit beside a whole
staging.

Sources: ADR-0045 and ADR-0046, read on 2026-11-16; the budget check's output
on `main` at `2a74ef2`. Nothing in this ADR was run beyond that check.
