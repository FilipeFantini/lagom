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

## Notes

The text above is kept as accepted. These notes record what was built and
measured, without rewriting it.

- **2026-11-17 — the showcase runs in staging (decisions 2 to 7).** The
  views, dbt's `serving` target and the `streaming` job (#113), then the hub,
  Redpanda Connect 4.116.1, the database `serving` and its two roles (#114).
  Argo CD synced them at 09:04 UTC; the first `streaming` run, at 09:45,
  built and tested the views (`PASS=27`). The JetStream input, the SQL insert
  and the other components used are under Apache-2.0 or MIT, read when the
  version was pinned (the first bullet of *Risks*).
- **2026-11-17 — decision 8, criteria 1 to 4, pass in staging.** The
  operator ran each with the runbook's commands (*The streaming showcase in
  staging*), while the producer published five events a second. Each reading
  takes the hub first, then the table.
  - *Connect restarted:* hub 20,670, table 20,675, nothing pending or
    unacknowledged. The table was read a moment after the hub, while events
    arrived; the primary key keeps any event from being stored twice.
  - *Postgres's Pod deleted:* once it was back, hub 20,225 and table 20,225,
    nothing pending; five messages unacknowledged at that reading, most
    likely a batch in flight. Every later reading had none.
  - *Table emptied and rebuilt from the hub:* over the rows collected before
    a cut-off, the count and an MD5 of every column, `received_at` included,
    were the same before and after: 21,015 rows. Then hub 22,675 and table
    22,675. Of the two claims in the second bullet of *Risks*, this shows
    one: a durable consumer deleted and created again reads the stream from
    its first message. The other, that dbt enforces a contract on a view, is
    not exercised by these checks.
  - *An hour held back and released:* the producer stopped at about
    11:29:14 UTC (the start of the hold was not printed; it is read back from
    the counts below, within a second) and came back at 12:29:24, with the
    backfill. The held hour holds 18,000 rows, 3,600 seconds of five
    streams; each of its 59 whole minutes has 60 events per stream (295 rows of `pipeline_minute`, none other than
    60); the outages at 11:37 and 12:07 are in `link_outages`, 20 lost pings
    on each of the three targets that drop. Per stream, 3,508 of the window's
    3,590 events are late: the others were collected less than a minute
    before the release, or published after it. Hub 79,455, table 79,455,
    nothing pending. The 12:45 `streaming` run passed (`PASS=27`); its
    `live_window_is_full` reads 11:43 to 12:43, where the live producer
    alone gave about 840 of the 1,800 events per stream needed.
  - A first attempt, at 10:23, was void: the producer kept running, up
    since the Connect Pod started at 10:18, and its restart was refused as
    `Stream already exists`. Most likely the `DELETE` went through a
    port-forward still tied to the Pod that the rebuild had replaced. The
    producer kept publishing, and the backfill added a second set of
    events, with their own keys, beside it. From 10:23:08 to 11:23:08 staging's table holds two events a
    second per stream (36,000 rows), so `link_minute` would count 120 pings
    a minute there. It is synthetic data in staging and is left as it is: the
    hub would bring it back on a rebuild until it ages out, on 2026-11-24.
    The runbook now checks the answer and the stream list before the wait.
- **2026-11-18 — the fifth criterion, read in staging: memory and the hub
  pass, two panels' queries fail.** Read at 09:24 UTC, 24 hours 20 minutes
  after the first event, with the synthetic producer at about five events a
  second. The hub and Connect had restarted at 21:22 UTC on 2026-11-17, when
  #124 turned on NATS authentication, so their figures cover 12 hours; the
  operator accepted 12 hours as enough to decide.
  - *Memory,* from each container's cgroup on the node (`memory.current`,
    `memory.peak` and `memory.stat`; working set is current less inactive
    file cache, the figure `kubectl top` shows). The peak counts page cache
    as well, so it is an upper bound on what the process held.

    | Container | Request / limit | Working set | Peak since start |
    |---|---|---|---|
    | NATS hub | 64 / 128 MiB | 32 MiB | 110 MiB (12 h) |
    | Redpanda Connect | 128 / 256 MiB | 70 MiB | 166 MiB (12 h) |
    | Postgres (staging) | 300 / 512 MiB | 266 MiB | 511 MiB (23 h) |

    Each working set is inside its request, so the requests stay. Postgres's
    266 MiB, read with events being written, is above 256: the reading does
    not confirm a 256 MiB request, and both Postgres stay at 300 (decision 8
    of ADR-0046, as amended here). Its peak at the limit is page cache, which
    the kernel reclaims under the limit; its anonymous memory was 33 MiB.
  - *The hub on disk:* `EVENTS` held 456,145 messages in 167,226,509 bytes,
    367 bytes an event (366 in the local spike), with nothing pending or
    unacknowledged on `serving-writer`. At this rate seven days take about
    1.2 GB, inside the stream's 3 GiB cap and the 4 GiB volume.
  - *Query times,* the ten queries of the dashboard's nine panels and its
    `source` variable, as `grafana_reader`, with Grafana's macros replaced
    for a 7-day range (`$__timeGroupAlias` as 10-minute bins, the target
    `node`), over the 24 hours the table held. Server-side time from
    `EXPLAIN (ANALYZE, TIMING OFF)`; the round trip over a port-forward added
    about 0.2 s more.

    | Panel | Execution |
    |---|---|
    | Is it the home router or the provider | 1,776 ms |
    | How long was the link down | 1,827 ms |
    | Is it good enough for a video call | 891 ms |
    | How late is an event, end to end | 446 ms |
    | How much arrived, and how much arrived late (two queries) | 449 and 360 ms |
    | At which hours is the link worse | 232 ms |
    | The three health panels | 140 to 148 ms |

    The criterion is over seven days, and one day of events cannot show it
    directly; what it shows is that every view reads the whole table, so a
    query's cost grows with the table, not with the range shown. Seven days
    hold about seven times these rows. `link_minute` and `link_outages` run
    window functions over every ping, so a filter on time cannot reach the
    scan: at 1.8 s for one day, seven would pass Grafana's 10-second
    statement timeout. Scaled the same way, the video-call panel (on
    `link_minute`, 0.9 s) and the three `pipeline_minute` queries (0.36 to
    0.45 s) would also pass two seconds, and `link_hour_profile` (0.23 s)
    would come close. An index cannot help a query that reads every row, so
    decision 8's second remedy applies: a table built by a dbt incremental
    model, for every view the seven-day projection puts over two seconds.
    It is built next, before the Pi or prod, and every panel is measured
    again over seven days of events.
- **2026-11-17 — Grafana, decision 7, built for staging.** One Grafana in
  the namespace `grafana`, at `ops.ofantini.com/grafana`, reading staging's
  `serving` as `grafana_reader` (`platform/infra/grafana/`; runbook: *The
  streaming showcase in staging (M6), Grafana*). How decision 7 was carried
  out, where the text left it open:
  - *The role.* SELECT on the views of `metrics` only, granted by dbt on
    every build and checked by a test of the `streaming` job; a connection
    limit of 3 (Grafana's pool is 2); `pg_hba` refuses it every database
    but `serving`. A view runs with its owner's rights, so the role reads
    `live.events` through the views and is refused the table itself.
  - *The statement timeout, 10 seconds,* is set by the operator with one
    `ALTER ROLE`, since CloudNativePG's role spec (chart 0.29.1, operator
    1.30.1) has no field for it and Grafana's PostgreSQL data source sets
    none. The test fails every hour it is missing. It is a session default,
    which a session may change with `SET`: it stops a slow dashboard query,
    not SQL that a Viewer sends through Grafana by hand. The privileges,
    `pg_hba` and the connection limit hold either way.
  - *The NetworkPolicy.* Staging's Postgres had none, so every namespace
    reached it; it now admits `staging`, the CloudNativePG operator, and
    Grafana's Pod on port 5432.
  - *The risk "the 386 MiB assume Grafana at 128"* is resolved against
    this ADR's figure. Measured locally in Grafana 13.3.1's image under its
    configuration: 185 MiB of working set at rest, 220 MiB after the
    dashboard read 26 hours of events (356 MiB before the unused data
    source processes and alerting were turned off). It is requested at
    256, so the table in *What fits* becomes: today 5,182; prod with M6 and
    staging on, **5,374, with 258 MiB to spare**; with both Postgres at
    256, 5,286 and 346. The figure is read again on the node after a day.
- **2026-11-17 — decision 6, the archive job, built.** Dagster's
  `streaming_archive`, hourly at minute 20, where `LAGOM_STREAMING` is on
  (`platform/orchestration/lagom_orchestration/archive.py`; runbook: *The
  archive and the live table's retention*). How it was carried out, where
  the text left it open:
  - *Windows of `received_at`, one hour, UTC.* `received_at` is the hub's
    timestamp and a replay writes the same value, so a window of it closes; a
    window of `collected_at` never does. Hourly keeps each read small
    (18,000 rows) and the lake an hour behind.
  - *Idempotent by `event_id`.* A row is inserted only if its identifier is
    not in the lake, looked up among the lake rows of the same
    `collected_at`, which the identifier is made of. A window counts as
    archived when every live row of it is found in the lake by identifier
    and no identifier is there twice; otherwise the run fails before any
    delete.
  - *Retention, 7 days live.* An hour leaves `live.events` once all of it
    is older than 7 days, the hub's retention (a test fails if the hub keeps
    less), by the identifiers just confirmed, in one transaction that rolls
    back unless it deletes exactly those. A failed archive deletes nothing.
  - *The lake's table,* `bronze.streaming_events`: the live table's six
    columns, the payload as JSON, sorted by source, signal and
    `collected_at`, zstd, partitioned by the UTC day of `received_at`, with
    no rows inlined into the catalog. A day then leaves the lake as whole
    files, which `lake_maintenance` deletes from R2 once their snapshots
    expire (seen locally: the file of a deleted day goes).
  - *No index on `received_at`.* Measured locally on 2026-11-17 against a
    Postgres 18 holding eight days (3.9 million rows, 1,958 MB): a normal
    hourly run took 0.6 s without one and 0.3 s with one. Decision 8's rule
    stands: none is built before a measurement asks for it. The run's time in
    staging is read too.
  - *ADR-0045's 90 days of raw events in the lake become 30,* fixed from the
    measurement as its decision 8 says, and confirmed by the operator on
    2026-11-17. Locally, the synthetic producer's
    events (432,000 a day) took 8.86 MB a day in Parquet, 20.5 bytes an
    event, in hourly files, and 8.38 MB, 19.4 bytes, once merged into one
    file a day. The spike's 15.6 bytes measured typed columns, not the JSON
    payload. In R2 a deleted day stays until its snapshot expires (7 days),
    and a day's hourly files stay 7 days after the merge replaces them: per
    environment about 38 × 8.4 + 8 × 8.9 ≈ 390 MB, both about 780 MB, inside
    the showcase's 1 GB. 90 days would be about 1.8 GB for both. The
    showcase adds nothing to the Postgres backup (decision 5) beyond the
    catalog's few rows per run. The Pi's real events carry more fields and
    less regular values; their size is read in prod's first days, and the
    retention, one constant, is lowered if the two environments pass 1 GB.
- **2026-11-17 — the archive runs in staging.** The image built from #121
  was pinned by #122. Each figure below is from the run's own log; the
  table's counts are the archive's own, read in the same run as the lake's.
  - *First run,* `1cde87fe-ce07-4f7d-ba1d-589804628462`, at 17:20 UTC,
    37 seconds. It archived the 8 closed hours since the table began, from
    09:00 to 16:00: 160,480 rows, and in each hour the lake held exactly
    the live table's count. The hour from 09:00 held 16,555 rows, because
    events started at 09:04. The hours from 11:00 and 12:00 held 26,800 and
    27,180 rows, because they include the doubled backfill recorded above
    for 10:23 to 11:23. The others held 18,000 each. The lake then held
    3,576,317 bytes of Parquet, 22.3 bytes an event. The container's peak
    was 520 MiB.
  - *Second run,* `090c0145-b475-44b3-88fb-86bc6fd28d50`, at 18:20 UTC,
    19 seconds. It read the per-hour counts of all nine closed hours and
    archived only the new one, from 17:00: 18,000 rows. The eight hours
    archived before were left as they were, with live and lake equal. The
    lake held 178,480 rows, 160,480 plus 18,000, so no row was copied
    twice. The new hour took 405,192 bytes, 22.5 bytes an event, which is
    about 9.7 MB a day in hourly files. The peak was 490 MiB.
  - *R2 against the 1 GB,* with staging's 22.5 bytes in place of the local
    20.5: about 430 MB per environment and about 860 MB for both, still
    inside the showcase's 1 GB, with less margin. Two figures are still to
    read. The first is a merged day, after `lake_maintenance` at 04:15
    merges the hourly files. The second is the first delete from the live
    table, which comes once the hour from 09:00 on 2026-11-17 is 7 days
    old: the 10:20 UTC run on 2026-11-24.
  - *No index on `received_at`:* the two runs took 37 and 19 seconds, Pod
    start included, so none is asked for.
- **2026-11-18 — decision 8's second remedy, built: tables of minutes
  behind the views.** In staging, over one day of events, two panels' queries
  took 1.8 s (the fifth criterion's reading, #125). Every view read every
  event of the live table, so each query's cost grew with the week. Measured
  locally over 7 days (below), four of the dashboard's eleven queries took
  4.3 to 13 s, and two more about 1.9 s. An index cannot help a query that
  reads every row, so the second remedy is built, for all five views of
  `metrics`, not only the two that failed. How it was carried out, where the
  text left it open:
  - *What holds state.* Two `UNLOGGED` tables in `streaming`, `ping_minute`
    and `health_minute`: one row per minute of `collected_at`, per source and
    target, or per source, built by dbt incremental models in the hourly
    `streaming` job. A minute of pings keeps what every link view needs:
    counts, percentiles and jitter, but also its round trips, so the
    percentiles of `link_hour_profile` stay exact, and its runs of lost pings,
    each marked as starting an outage or carrying one on, so `link_outages`
    joins an outage across minutes and hours. The five views keep their
    names, columns and contracts, and the dashboard is unchanged.
  - *What triggers recomputation.* The table's newest `received_at` records
    how far the last build read. A build, and every read of a view, computes
    two stretches of event time again from `live.events`:
    - the recent stretch: from the minute of the earliest event the hub
      stored less than an hour before that newest one, or after it, so a late
      event moves the stretch back to its own minute;
    - the trimmed stretch: up to an hour and two minutes after the live
      table's oldest `received_at`, where the archive deletes.

    Between the two stretches, the table's rows are read as they are. The
    events are filtered by an index on `collected_at` before the window
    functions run; a second index, on `received_at`, finds the new ones.
    `platform/warehouse/macros/streaming/changed_minutes.sql` holds the
    mechanism and its assumptions.
  - *Freshness.* At the moment a panel is read, it shows what a computation
    over the whole live table would show. The views compute the recent
    stretch themselves, so nothing waits for the next build. The table
    decides only how much of the week a query reads.
  - *Assumptions, and what makes a broken one loud:*
    - the writer stores an event less than an hour after a newer one;
    - the source's clock runs less than an hour ahead of the hub's;
    - only the archive deletes rows of `live.events`;
    - a rebuild from the hub is followed by emptying the two tables
      (runbook).

    Every hour, the job's test `minutes_equal_a_full_computation` compares
    what the views read with a computation over the whole live table, and
    fails on any difference. It runs after the views, so a failure leaves
    them in place.
  - *Retention.* The tables hold the minutes the live table holds, and no
    more: the next build deletes a minute whose events the archive deleted.
    A full refresh from `live.events` gives the same rows as incremental
    builds (shown below).
  - *The trade-off accepted.* More code in the warehouse: two incremental
    models and the macros that keep them. Where the views needed no
    assumption to be right, they now rest on the four above, which the test
    guards. The test's computation over the whole table costs Postgres 11 to
    23 s an hour at 7 days, in the job and not in a panel; before, each of
    the views' own tests read the whole table. Right after a long backlog
    arrives, the views compute all of it until the next build: with 6 hours
    held back and then delivered, the slowest panel took 0.9 s. In exchange,
    a panel reads about two hours of events plus the tables, so its cost no
    longer grows with the week.
  - *Budget.* No new component and no new request. At 7 days the tables
    take 29 MB (`ping_minute`, 42,804 rows) and 1.8 MB (`health_minute`), and
    the indexes take 37 and 69 MB, beside the live table's 1,723 MB. All are
    `UNLOGGED`, so the backup gains nothing. During the final local review
    run, the live table held 3,210,005 events after a trim and subsequent
    backfill. The two tables occupied 28 MB and 1,736 kB at that point.
  - *Measured locally on 2026-11-18* against Postgres 18.1 in Docker, with
    staging's settings (`shared_buffers` 96MB, `work_mem` 4MB, a 512 MiB
    memory limit), on an Apple M5. The data: 7 days and an hour of synthetic
    events at staging's rate, 3,041,980 rows. Four targets are pinged at
    1 Hz, with staging's half-hourly outages and 0.3 % random loss, plus
    health readings. Each of the dashboard's queries ran as `grafana_reader`,
    under `EXPLAIN (ANALYZE, TIMING OFF)`, with Grafana's macros replaced for
    a 7-day range: `$__timeGroupAlias(col, $__interval)` as
    `date_bin('10 minutes', col, timestamptz '2000-01-01') as time`,
    `$__timeFilter(col)` as the last 7 days, and the target `node`. Each
    figure is the median of three runs before and five after. *After* has an
    hour of events stored since the last build, as just before the :45 run.

    | Query | Before | After |
    |---|---|---|
    | Is it the home router or the provider | 7,479 ms | 160 ms |
    | Is it good enough for a video call | 4,333 ms | 128 ms |
    | How long was the link down | 13,016 ms | 475 ms |
    | At which hours is the link worse | 1,311 ms | 343 ms |
    | The three health panels | 938 to 1,198 ms | 24 ms |
    | How late is an event, end to end | 5,752 ms | 301 ms |
    | How much arrived, and how much arrived late (two queries) | 1,856 and 1,985 ms | 304 and 300 ms |
    | The `source` variable | 647 ms | 25 ms |

    The Mac is faster than the node: per day of events, staging's one-day
    reading of the first query took 1.6 to 1.7 times the local one. So the
    node may take about twice these times, which are still under two
    seconds. Staging must first pin the orchestration image published from
    this merge. After the first hourly `streaming` build on that image, run
    the same queries in staging. The criterion needs seven days of live
    events there.
  - *Equal to the views it replaces.* Over the same 7 days, each of the five
    views returned the same rows as the SQL on `main`: 0 rows differed, of
    40,564 minutes, 6,585 outages, 672 cells, 10,141 health minutes and
    50,705 pipeline rows, with averages compared to nine decimals. Then a
    series of steps compared the views with that SQL before and after each
    build:
    - an outage carried across a build, and across the start of the recent
      stretch;
    - two minutes held back, then delivered two hours late, which joined two
      outages into one before any build;
    - the oldest hour trimmed, as the archive trims it.

    Each step left 0 rows different. A hand-made delete of ten minutes failed
    the test (58 rows), and `--full-refresh` repaired it. A second build
    changed no row, and after several incremental builds a full refresh gave
    the same tables, with 0 rows different.
