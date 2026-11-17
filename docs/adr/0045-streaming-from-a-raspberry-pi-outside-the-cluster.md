# ADR-0045 — The streaming showcase: a Raspberry Pi outside the cluster, NATS JetStream as the log

- **Status:** Proposed. Taken by the operator in conversation on 2026-11-15:
  decisions 1 and 3, RisingWave measured first with the two-path design as the
  fallback (decision 6), no throughput test (decision 4), the storage limit
  of decision 8 and the privacy rule of decision 11. The rest is the
  architect's proposal, accepted when this pull request is approved.
- **Date:** 2026-11-15
- **Supersedes:** [ADR-0002](0002-single-node-k3s-hetzner.md), for the
  Raspberry Pi only: it does not join the cluster as an agent node.
- **Amends:** [ADR-0003](0003-ingestion-split-dlt-benthos.md): NATS is no
  longer deferred, its trigger is met by this showcase;
  [ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md),
  decision 4: events land in `serving` only under the fallback of
  decision 6;
  [ADR-0042](0042-the-dashboards-return-to-dbt-charts.md), decision 6: Grafana
  is the streaming tool.
- **Amended by:** [ADR-0046](0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md): RisingWave requests 768 MiB; one Grafana
  reads both environments (decision 9); staging's streaming stack runs on
  demand; every streaming source shares one hub, one RisingWave and one
  Redpanda Connect process; the archive job is validated locally.
- **Superseded in part by:** [ADR-0047](0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)
  (proposed): decision 6 is superseded, neither RisingWave nor the two-path
  fallback is built, and events are written to Postgres with their windows
  computed when read; decisions 3, 7, 8 and 9 are amended to match. Decision
  8's starting 90 days of raw events in the lake were fixed at 30 from
  measurement, as that decision says (ADR-0047's notes, 2026-11-17).

## Context and problem

The streaming showcase ([M6](../roadmap.md#m6--streaming)) needs a source of
continuous events. The operator has a Raspberry Pi 3B+ (1 GB of memory) at
home and wants it to produce them: its own health (CPU temperature, frequency,
throttling, memory, disk, load, network) and the quality of its internet link
(latency and packet loss). Sensors may be attached to it later.

Two things were settled in conversation on 2026-11-15:

- **It must be real streaming.** One reading a minute written straight to a
  table is polling. Streaming here means: events at the rate the source emits
  them, a durable log that consumers read by offset and can replay, more than
  one consumer, and aggregation over windows of *event time* (when the event
  happened, not when it arrived).
- **The Pi is an external source.** It produces data; the platform turns the
  data into insight. It is not part of the cluster.

The roadmap had the Pi joining k3s as an agent node. That needs flannel, the
network that lets Pods on different nodes talk, moved from the node's public
interface to its Tailscale interface, because the Pi sits behind a home router
with no public address. On a single node that carries prod and staging this
means restarting k3s with a new node address, the network of every Pod rebuilt
at once, and k3s depending on Tailscale being up at boot.

## Requirements and constraints

- Open-source components only, and inside both memory budgets: 1 GB on the
  Pi, and the ceiling of
  [`architecture.md` §11](../architecture.md#11-resource-budget) on the node.
- **No event lost when the home link is down.** The link is one of the things
  being measured; the minutes around an outage are the data that matters most.
- Idempotent: an event delivered twice is stored once.
- The node keeps zero inbound ports open to the internet
  ([`architecture.md` §10](../architecture.md#10-access-channels)).
- The Pi sits in a home, the least protected machine in the design. What it
  holds must not reach the warehouse.
- "A new source is configuration" ([ADR-0003](0003-ingestion-split-dlt-benthos.md))
  holds for a sensor added later.
- **It must be impossible to locate the operator or learn about the home
  network from what is collected or published.** The repository is public.
- **R2's free tier is 10 GB for the whole account,** shared with the Postgres
  backups and the existing lake
  ([ADR-0027](0027-hourly-wal-archiving-and-14-day-prod-retention.md)). This
  showcase must not be what exceeds it.

## Alternatives considered

**The Pi as a k3s agent node** (ADR-0002, the roadmap as written). Keeps the
collector under Argo CD. Rejected: the flannel change above is a risk to prod
taken for a machine that only produces data, the agent would take an estimated
quarter to a third of the Pi's memory (not measured), and with a broker in the
design the Pi needs one outbound connection, not Pod networking.

**Polling with a local buffer, written straight to Postgres.** The smallest
design: Redpanda Connect on the Pi with its `sqlite` buffer and a `sql_insert`
output. Rejected: it is not streaming, which is the purpose of the showcase,
and it puts a database credential on the Pi.

**Apache Kafka.** The market standard. Rejected on memory: a JVM broker is
expected at about 1 GB resident, a fifth of the ceiling, for tens of events a
second.

**Redpanda, the broker.** Kafka-compatible and lighter. Rejected: it is under
the Business Source License, which is not an open-source licence. Not to be
confused with Redpanda Connect, which is a different program (decision 3).

**Mosquitto (MQTT) alone.** The standard broker for sensors, and small.
Rejected as the log: it delivers messages but does not keep a stream that a
consumer can replay by offset. It stays the fallback at the edge (see
*Risks*).

**Telegraf on the Pi.** A metrics collector with inputs for everything listed
above. Rejected: a second event tool beside the one ADR-0003 chose, for no
capability Redpanda Connect lacks here.

**Our own scripts with a NATS client.** Rejected by the operator: code where
configuration does the job.

**Apache Flink.** The market's stream processor. Rejected on memory: a JVM
cluster of a job manager and task managers.

**Arroyo.** A single-binary stream processor in SQL. Not chosen as the first
candidate: it has no dbt adapter known to us, so it would not give the one
definition per metric that RisingWave promises (decision 6). Not measured.

**A throughput test every hour.** Rejected by the operator: it is polling,
not a stream, and every signal kept is sampled once a second.

**TimescaleDB.** Rejected: an extension to operate, part of it under a
non-open licence, for a volume that needs no time-series engine.

## Decision

1. **The Pi is an external source, outside the cluster.** It runs two
   services under systemd, the Linux service manager: Redpanda Connect and a
   NATS server. Their configuration is versioned in this repository and
   applied by hand from the runbook; Argo CD does not reach the Pi. The Pi is
   a member of the tailnet with a tag whose ACL allows one thing: the NATS
   leaf port on the node.

2. **NATS JetStream is the log.** NATS is a message broker; JetStream is its
   persistence layer, which keeps messages in a *stream* on disk and lets each
   *consumer* read from its own position and replay. ADR-0003 deferred it
   until "a second consumer of the same stream, or a replay requirement".
   The replay requirement holds here: whatever processes the events is
   rebuilt by reading the stream again (decision 6). So does a third need:
   store and forward.
   - **On the Pi, a leaf node:** a NATS server that connects outward to
     another and bridges subjects to it. It has its own JetStream domain and
     a local stream on disk, capped by size, so events are kept while the
     link is down.
   - **On the node, the hub:** one NATS server with JetStream, whose stream
     *sources* the Pi's stream: it copies what it has not yet seen whenever
     the link is up. Retention is bounded by age and size.

3. **Redpanda Connect does the reading and writing.** It is the
   former Benthos: a program that reads from an input, transforms, and writes
   to an output, declared in YAML. It stores nothing; NATS stores. On the Pi
   it turns sensor readings and the output of a continuous `ping` into events
   and publishes them. On the node it is used only under the fallback of
   decision 6, where it consumes the hub's stream and writes to Postgres;
   RisingWave reads the hub by itself. Only its Apache-2.0 components are used; the tier of each one is
   checked in the vendor's catalog when the version is pinned.

4. **What is collected.** One JSON document per reading, once a second, on
   a subject named by source and signal:

   | Signal | Fields |
   |---|---|
   | Health of the Pi | CPU temperature and frequency, throttling and under-voltage flags, load, memory, disk use, network byte counters |
   | Latency and loss | one event per `ping` reply, or per missing reply, for each of four targets: the home router, two public resolvers, and the node over the tailnet |

   The router is there to tell a fault of the home network from a fault of
   the provider. Every event carries `collected_at`, the Pi's clock, and an
   identifier derived from source, signal and `collected_at`; whatever stores
   it adds `received_at`, its own clock. The collector starts only after the
   Pi's clock is synchronised: the 3B+ has no battery clock and boots with a
   wrong time. There is no throughput test.

5. **The questions the showcase answers.** A signal is collected because one
   of these needs it.

   | About | Question |
   |---|---|
   | The link | How long was it down this month: outages, their durations, availability |
   | The link | At which hours is it worse: median and 95th-percentile latency by hour of day and day of week |
   | The link | Is it the home router or the provider: loss to the router against loss beyond it |
   | The link | Is it good enough for a video call: variation of latency (jitter) and loss |
   | The Pi | Does it get hot enough to slow down: temperature against load, minutes throttled |
   | The Pi | Is its power supply adequate: under-voltage events |
   | The Pi | When does its disk fill: the trend of disk use |
   | The pipeline | How late is an event end to end: `received_at` minus `collected_at` |
   | The pipeline | How much arrived, and how much arrived late: events received against one a second, and the backlog delivered after each outage |

6. **Processing: RisingWave is measured first; two paths are the fallback.**
   The hard case is the late event: after an outage the Pi delivers hours of
   events at once, long after any window of time has closed.

   - **First candidate, one path** (a *kappa* architecture). RisingWave is a
     streaming database: it reads the hub's stream and keeps *materialized
     views*, stored query results, up to date as each event arrives, a late
     one included. It answers on the Postgres protocol, so Grafana and dbt
     connect to it as to a Postgres, and the views are dbt models through
     the `dbt-risingwave` adapter. Each metric is then defined once, in dbt,
     and is both live and correct. A primary key on the event identifier
     removes duplicates.
   - **Fallback, two paths** (a *lambda* architecture). A live path:
     Redpanda Connect writes every event to an append-only table in
     `serving` and aggregates per minute with its `system_window`; these
     numbers are approximate, since they miss late events. A batch path:
     dbt on the lake rebuilds the correct aggregates on a schedule. The same
     metric is defined twice, and the batch one is the truth. dbt takes no
     part in the live path: it runs SQL over tables at rest when called.

   **RisingWave passes if all of these hold**, measured in a spike on the
   operator's machine and then in staging:
   - its arm64 image runs as a single process with its state on local disk,
     not in R2;
   - over 24 hours at the showcase's rate its memory fits a request of
     512 MiB and a limit of 1 GiB in each environment, leaving the job slot
     of [`architecture.md` §11](../architecture.md#11-resource-budget)
     intact. Its documentation sizes a compute node at 8 GiB for a cluster,
     so this is the criterion most likely to fail;
   - after it is restarted, and after the Pi's link is cut and restored, no
     event is missing or doubled, and the views include the late events;
   - every feature used is under Apache-2.0, with no licence key.

   If any fails, the fallback is built and the result is written in this
   ADR's notes. Delivery is at-least-once in both designs: JetStream drops a
   message whose `Nats-Msg-Id` it has seen inside its deduplication window,
   and the store has a unique key on the event identifier.

7. **Where events are stored.** Under the first candidate, in RisingWave, on
   the node's disk; they do not enter Postgres and `serving` is not created
   for this showcase. Under the fallback, in `serving`, as ADR-0041's
   decision 4 set. In both, a Dagster job archives the events to DuckLake on
   a schedule, and a row leaves the live store only after the archive of its
   range succeeded. Live retention starts at 7 days.

8. **R2 stays inside its free tier.** This showcase may use at most 1 GB of
   the account's 10 GB, counting its files in the lake and whatever it adds
   to the Postgres backups.
   - Raw events in the lake are kept for a limited time, starting at
     90 days; the per-minute aggregates are small and kept.
   - Under the fallback, events written to Postgres also reach R2 through
     the backup's write-ahead log. That growth is measured in staging before
     prod; if it does not fit, the events' tables become `UNLOGGED` (not
     written to that log), which is safe because the hub can replay them.
   - The size is measured in the spike and the retention fixed from it. An
     estimate, not a measurement: five events a second are about 430,000 a
     day, a few megabytes of Parquet.

9. **Grafana shows the events to the operator.** It reads the live store
   through its own read-only role with a connection limit and a statement
   timeout (ADR-0041, decision 2), behind Cloudflare Access like every
   operator UI ([ADR-0030](0030-operator-uis-behind-cloudflare-access.md)).
   One panel per question of decision 5. It is not the public page.

10. **Sensors, later, speak MQTT to the Pi's NATS.** MQTT is the standard
    protocol of small sensors, and the NATS server can accept it directly.
    The listener is switched on with the first sensor, not before. A new
    sensor is then a new subject and its configuration.

11. **Nothing collected or published can locate the operator or describe the
    home network.**
    - **Never collected:** the public IP address, the provider's name, the
      Wi-Fi network's name, hardware (MAC) addresses, the Pi's hostname or
      its own addresses, a location, or the route to a target (its hops name
      the provider's routers).
    - **Labels, not addresses.** An event names its target `gateway`,
      `resolver-a`, `resolver-b` or `node`, and its source by a generic
      label. The addresses live in a file on the Pi that is not in this
      repository.
    - **Raw events stay private:** in the live store, the lake and Grafana,
      all behind login or inside the cluster.
    - **A public page, if one is made, shows aggregates only:** by hour of
      day and by month, in UTC. No dated outage, no raw series, and no
      absolute latency to the node, whose location is public. What the site
      shows is decided in its own pull request under this rule.

**To be measured in the first pull request, before anything is deployed:**
resident memory of NATS and Redpanda Connect on the Pi and on the node, of
RisingWave and Grafana on the node, the events' size per day in the live
store and in the lake, what the showcase adds to R2, and the bytes written
per day to the Pi's SD card.

**Environments.** The Pi feeds prod, and only prod. Staging runs the same
pipeline fed by a synthetic producer inside the cluster: Redpanda Connect
generating events in the format of decision 4, labelled `synthetic`, which
can also produce an outage and its late events on demand. No link joins the
two environments. During the first build the Pi points at staging until the
pipeline is validated there, then moves to prod for good.

**Open, for the implementation pull requests:** how the hub's leaf port is published on the node's
Tailscale interface only; where the Pi's configuration lives in the
repository.

## Consequences

**Positive.**

- The cluster's network is not touched; prod takes no risk for the Pi.
- The Pi holds one credential, a NATS account that can publish to its own
  subjects. It holds no database password and cannot read the stream back.
- An outage of the home link loses nothing up to the leaf stream's size cap,
  and produces real out-of-order data to show.
- A later source or sensor is a subject and a pipeline in YAML.
- If RisingWave passes, each metric is written once, in dbt, and the
  showcase adds nothing to Postgres or to its backups.
- The two designs, measured side by side, are material for an article on
  kappa against lambda on a small node, written in the site's repository
  ([ADR-0039](0039-the-site-in-its-own-repository.md)).

**Negative, and what it costs.**

- **The Pi is outside GitOps.** Its configuration is in Git, but a change is
  applied by hand and drift is not detected.
- **Three or four resident components on the node** where the roadmap had
  none yet: NATS, Redpanda Connect, Grafana, and RisingWave if it passes.
  `architecture.md` §11 estimated the first three at 270 MB together; none
  is measured, and RisingWave is in no budget yet.
- **If RisingWave passes, a second database to operate,** young compared
  with Postgres, with its own upgrades and failure modes.
- **If it fails, two paths compute the same aggregate,** live and batch, and
  can disagree for a window that received late events.
- **A measurement comes before any showcase work,** and may end in the
  fallback.
- **The SD card becomes a written layer,** against
  [`architecture.md` §12](../architecture.md#12-accepted-risks). The leaf
  stream is small and capped; the write volume is measured, not assumed.
- **One more system to learn and operate,** with its own accounts, limits and
  failure modes.

## Risks, cost, and reversal

- **NATS depends on one company.** In April 2025 Synadia, which maintains it,
  moved to take it out of the CNCF and relicense the server; on 2025-05-01
  the two announced that NATS stays in the CNCF under Apache-2.0 and the
  trademark went to the Linux Foundation. No later change was found on
  2026-11-15. If it happens again, the edge can move to Mosquitto, since
  sensors and collector can speak MQTT, and the hub is the piece to replace.
- **The cross-domain source between leaf and hub is the least familiar
  piece.** It is tried first, on the operator's machine, with the link cut
  and restored, before any manifest is written.
- **A stolen or failed Pi.** Its NATS account is revoked on the hub and its
  tailnet key expired; nothing else trusted it. Events not yet forwarded are
  lost with the card.
- **"Impossible to locate" cannot be proven, only narrowed.** Dated outages
  could be matched against a provider's public incident reports, and an
  hour-of-day pattern hints at a time zone. Decision 11 removes the first
  and accepts the second.
- **R2 past 10 GB is billed, not blocked.** The limit of decision 8 is
  checked by measurement, not enforced by a tool.
- **Cost:** none in money. Memory as estimated above, to be measured.
- **Reversal:** the three components are removed by reverting their
  manifests; the archived events stay in the lake. Joining the Pi to the
  cluster later takes a new ADR that supersedes decision 1.

**Reconsider when** RisingWave fails its criteria (the fallback is built), a
second edge device appears (hand-applied configuration
stops scaling), or the events' rate grows past what one Postgres table and
one Redpanda Connect process hold.

Sources checked on 2026-11-15: the CNCF's announcement of 2025-05-01 on NATS;
`nats.io/about`; the NATS documentation on leaf nodes and JetStream domains;
Redpanda's licensing page for Connect; Grafana's documentation of its MQTT
data source; RisingWave's documentation of its NATS JetStream source, its
standalone mode (one process, state on the local filesystem) and its hardware
requirements. RisingWave's licence, its arm64 image and the `dbt-risingwave`
adapter are from memory and are checked in the spike. The JetStream source across domains and NATS's MQTT support were
read in documentation, not run. Memory figures for Kafka, Flink and the
k3s agent are from memory of their documentation, not measured.
