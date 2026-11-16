# ADR-0046 — Memory for streaming: staging on demand, one Grafana, one run slot per environment

- **Status:** Proposed. Decisions 1 to 5 were taken by the operator in
  conversation on 2026-11-16; how each is carried out, the CI checks and the
  open items are the architect's proposal, accepted when this pull request is
  approved. Nothing is built yet: the implementation pull requests follow.
- **Date:** 2026-11-16
- **Decided by:** the operator, on 2026-11-16: staging on demand, with its
  Postgres hibernated while off; one Grafana for both environments; the
  `hello` Application removed; one run slot per environment for every batch
  job; streaming sources sharing one hub, one stream processor and one
  Redpanda Connect process. A bigger node is not an option. A separate local
  spike measures Flux in place of Argo CD; it is not decided here.
- **Amends:** [ADR-0007](0007-three-environments.md): staging's namespace,
  secrets, quota and NetworkPolicy stay permanent, but its workloads run on
  demand; [ADR-0026](0026-dagster-one-instance-per-environment.md): staging no
  longer "exercises everything prod will run" continuously, and the run queue
  limits every run, not only those tagged with the pipeline;
  [ADR-0014](0014-develop-against-staging.md): developing against
  `lagom_dev` needs staging's Postgres awake;
  [ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md)
  (proposed): one Grafana reads both environments (decision 9), and the
  streaming components are shared by every streaming source.

## Context and problem

The node, a Hetzner CAX21 with 8 GB, runs prod and staging on one k3s. The CI
check of [`architecture.md` §11](../architecture.md#11-resource-budget) holds
the sum of declared memory requests under a ceiling of 5,632 MiB
([ADR-0024](0024-ci-ceiling-on-the-8-gb-node.md)). On 2026-11-16 the total is
**5,070 MiB**: 562 MiB of room.

M6, the streaming showcase
([ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md),
proposed), adds resident components to both environments. Its local spikes of
2026-11-15 and 16 measured them. Their notes, a section *Notes from the
spikes* in ADR-0045, are on the branch `claude/lagom-m6-streaming-spikes-29a59a`
and not yet in `main`; their pull request is to land before this one. The
figures used here are restated:

- **RisingWave** fits a 1 GiB limit only with lean memory budgets, and its
  working set (p95 718 MiB over one hour) includes about 450 MiB of active file
  cache. The request it needs is about **768 MiB**, not the 512 ADR-0045
  planned.
- **The NATS hub's** working set reached 58 MiB: a request of **64 MiB**.
- **Redpanda Connect's** working set reached 121 MiB (61 MiB of its own
  memory): **128 MiB**.
- **Grafana** is not measured; ADR-0045 and §9 estimate 150 MB. The request
  proposed is **128 MiB**, set from measurement when it is deployed.

With ADR-0045 as designed, a hub, RisingWave and Grafana in each
environment and staging's synthetic producer, M6 adds about 2,050 MiB and the
total reaches about 7,100 MiB, far over the ceiling. Later sources would add more, and
one cost the check does not see today: a batch source with its own run tag.

## Measurement

`cd tools && uv run python resource_budget.py --repo ..`, on `main` at
`e01e579`, 2026-11-16:

| Application | Requests (MiB) |
|---|---|
| Argo CD (controller 384, repo-server 128, server 64, redis 32) | 608 |
| `postgres-prod`, `postgres-staging` (Postgres 300 + backup sidecar 64, each) | 364 + 364 |
| `dagster-prod`, `dagster-staging` (code server 320, daemon 320, webserver 160, each) | 800 + 800 |
| metrics-server 80, local-path 64, cert-manager 160, CloudNativePG 64, Barman Cloud plugin 32, Sealed Secrets 32 | 432 |
| `cloudflared` | 48 |
| `hello` | 16 |
| CoreDNS, outside Git | 70 |
| Ephemeral jobs, all at once: one Dagster run per environment (768 each) and the Argo CD hook (32) | 1,568 |
| **Total** | **5,070** of 5,632 |

`kubectl top pods -A`, the same day, read-only: staging's Dagster uses
143 + 275 + 235 MiB (webserver, daemon, code server) and its Postgres 213 MiB,
between runs; `hello` uses 4 MiB.

**Staging requests 1,932 MiB**: Postgres 364, Dagster 800, its run 768.

## Requirements and constraints

- M6 must fit the ceiling with prod complete, and staging must still be able
  to validate what reaches prod.
- The ceiling stays a check. A rule that depends on the operator remembering
  what is switched on is not one.
- Staging stays isolated from prod ([ADR-0007](0007-three-environments.md)):
  its own Postgres, quota, NetworkPolicy and secrets. Nothing joins the two
  pipelines (ADR-0045, *Environments*).
- No data is lost by switching staging off.
- A new source, batch or streaming, should cost configuration, not a new
  resident process.
- No bigger node. Replacing Argo CD with Flux is measured apart and not
  assumed here.

## Alternatives considered

**Staging always on, M6 in prod only.** Rejected: M6 would reach prod
untested, and even so prod with M6 plus today's staging is about 6,000 MiB.

**Staging destroyed and recreated per pull request.** Rejected by ADR-0007 for
reasons that still hold: a failure takes its state with it, and there is no
standing URL. Hibernation keeps the state, and the namespace keeps its
addresses (decision 1); the UIs answer only while staging is on.

**A Grafana per environment.** 128 MiB more, and two places for the operator
to look. Rejected by the operator.

**Keep the tag limit and give each new batch source its own tag and run slot
in the budget.** Correct, but every source would add 768 MiB to the ceiling
for runs that last seconds to minutes every 30 minutes. Rejected for a global
limit (decision 4).

**One hub, stream processor and Redpanda Connect per streaming source.**
Isolation between sources, at about 960 MiB a source. Rejected (decision 5).

**Lower RisingWave's request to 512 MiB and accept eviction risk.** It would
save 256 MiB per environment. Not taken here: the request is set to what was
measured; the 24-hour run in staging may revise it.

**Replace Argo CD with Flux.** Expected to save about 450 MiB, unmeasured. It
is the subject of its own spike and, if it passes, its own ADR.

## Decision

1. **Staging runs on demand, one mode at a time.** Staging has three states,
   each declared in Git and switched by a pull request:

   | State | What runs | Requests (MiB) |
   |---|---|---|
   | **Off** (the default) | nothing; staging's Postgres hibernated | 0 |
   | **Streaming** | NATS hub 64, RisingWave 768, synthetic producer 128 | 960 |
   | **Batch** | Postgres 364, Dagster 800, its run 768 | 1,932 |

   - **Dagster** is off in Git by default: the webserver and the code server
     at `replicas: 0`, and the daemon removed (`dagsterDaemon.enabled:
     false`), since the pinned chart (1.13.27) hard-codes the daemon's
     `replicas: 1`. With no daemon nothing is dequeued, so no run starts.
   - **Postgres is hibernated declaratively**, by the annotation
     `cnpg.io/hibernation: "on"` on staging's `Cluster`. In CloudNativePG 1.30
     (the operator running on the node is 1.30.1, from chart 0.29.1), the
     operator then deletes the instance's Pod and keeps its
     PersistentVolumeClaim, so the data stays on the volume; the `Cluster`
     shows the condition `cnpg.io/hibernation` with reason `Hibernated`.
     Setting it to `"off"` recreates the Pod on the same volume. (CloudNativePG
     documentation, *Declarative hibernation*, versions 1.28 and 1.30, read on
     2026-11-16; it says nothing of backups, so the scheduled backup is
     suspended with the `Cluster`, below.) Argo CD syncs the annotation like
     any field; it does not own the Pods the operator deletes.
   - **The streaming stack** (hub, RisingWave, producer) is at zero by
     default too.
   - **Postgres may be awake alone**, for development against `lagom_dev`
     ([ADR-0014](0014-develop-against-staging.md)): 364 MiB, beside either
     state.
   - **The CI check counts the state declared in Git**, so the ceiling stays a
     check. The implementation teaches
     [`tools/resource_budget.py`](../../tools/resource_budget.py) that a
     hibernated `Cluster` requests nothing, and that a Dagster Application
     whose daemon Deployment is absent has no run slot. A pull request that
     switches on more than fits fails the check before it merges.

   **The trade-off, stated plainly: staging no longer exercises the pipeline
   continuously.** While it is off, a change in the USGS source, the lake or
   R2 is caught by prod and its Healthchecks.io alarm
   ([ADR-0033](0033-dagster-alerts-through-healthchecks.md)), not by staging
   first. Staging catches what a change brings, when it is switched on to
   validate that change.

2. **One Grafana for both environments**, in a namespace of its own,
   `grafana`, as `cloudflared` has. It reads each environment's live store
   (RisingWave) through that environment's own read-only role, with a
   connection limit and a statement timeout (ADR-0045, decision 9). Its own
   state (users, preferences) is SQLite on its own volume, and its data
   sources and dashboards are files from Git, so nothing of Grafana lives in
   either environment's Postgres. It is served behind Cloudflare Access at
   `ops.ofantini.com/grafana`, like every operator UI
   ([ADR-0030](0030-operator-uis-behind-cloudflare-access.md)).

   **Why this is not the link between environments ADR-0045 forbids.** That
   rule is about the pipelines: no staging component reads, writes or
   triggers anything of prod, and the reverse. It holds. Data does not move
   between the environments; each environment's NetworkPolicy admits Grafana's
   namespace to its RisingWave port only, and still denies staging to prod.
   Grafana is the operator's viewer: it holds two read-only credentials and
   writes nothing to either store. Two risks are accepted with it:
   - **one compromised Grafana reads both live stores,** and prod's holds the
     Pi's real events, which ADR-0045 keeps private (decision 11), while
     staging's holds synthetic ones. The viewer is behind a login and its
     roles are read-only;
   - **a change to Grafana itself** (an upgrade, a plugin, a dashboard file)
     reaches the viewer that holds prod's credential with no staging copy to
     try it on first. So Grafana is first deployed with staging's data source
     only, and prod's is added once it works; later changes are reviewed
     like any change to an operator UI, and reverted by Git.

3. **The `hello` Application is removed.** It is the M1 smoke test, a trivial
   nginx that proved the GitOps loop; Argo CD's own Applications and Dagster
   prove it every day since. 16 MiB of requests and one Pod. The runbook steps
   that use it (*Deploy (M1)*, whose measurements were made on it, and the
   staging-to-prod network test under *Postgres (M2)*, which reuses its nginx
   image because the node already holds it) are rewritten in the same pull
   request, and its line in `platform/infra/README.md` goes.

4. **One run slot per environment for every batch job.** Today the
   `QueuedRunCoordinator` limits only runs tagged `lagom/pipeline: usgs`
   (`tagConcurrencyLimits` in `platform/infra/dagster-<env>/values.yaml`,
   `PIPELINE_TAG` in
   [`definitions.py`](../../platform/orchestration/lagom_orchestration/definitions.py)),
   while the budget check counts one 768 MiB run per environment. A new
   source with a tag of its own, or a run launched from the UI that carries no
   tag, would run beside it and the check would not see it. The chart's
   default for `maxConcurrentRuns` is no limit.
   - **The queue holds every run:** `maxConcurrentRuns: 1` in each
     environment's `queuedRunCoordinator`. A run waits until the one before it
     ends. Runs last seconds to minutes, every 30 minutes, so the wait is
     short.
   - **The tag limit stays.** It states a correctness rule, two `usgs` runs
     must not share dlt's cursor, which must hold even if the global limit is
     ever raised. The global limit is the budget's rule.
   - **A CI check holds it:** `tools/resource_budget.py` reads the run queue
     from the rendered Dagster instance configuration and fails if an
     Application that declares a run slot
     (`lagom.dev/ephemeral-memory-request`) has no `max_concurrent_runs`, or
     one other than 1. The existing test that every job declares the same run
     resources, equal to that annotation
     (`platform/orchestration/tests/test_definitions.py`), stays.

   A new batch source then costs no resident memory and no budget: its jobs
   take turns in the slot that exists.

5. **Streaming sources share their components**, in each environment:
   - **one NATS hub**, with a subject per source and signal;
   - **one stream processor**, RisingWave, holding every streaming source's
     tables and views inside its fixed memory budgets;
   - **one Redpanda Connect process in streams mode** for the event sources
     that run on the node (in staging, the synthetic producer; in prod, none
     yet, since the Pi runs its own). Streams mode runs several pipeline
     configurations in one process, one file per source.

   A new streaming source is then a subject, a set of views and, if it runs
   on the node, a stream file: no new process and no new request. What it
   does add is load inside budgets that are fixed, so RisingWave's and
   Connect's memory is measured again when a source is added.

### What fits

Requests in MiB, with the CI check's accounting. Prod with M6 is today's
total, less staging (1,932) and `hello` (16), plus RisingWave 768, the hub 64
and one Grafana 128.

| Point | Total | Room under 5,632 |
|---|---|---|
| Today | 5,070 | 562 |
| Prod with M6, staging off | 4,082 | 1,550 |
| … staging in streaming mode | 5,042 | 590 |
| … staging's Postgres awake alone, for development | 4,446 | 1,186 |
| … staging in **batch** mode | **6,014** | **−382** |

Pods that do not run also have no `containerd-shim`, about 20 MiB each outside
Kubernetes ([ADR-0024](0024-ci-ceiling-on-the-8-gb-node.md)), which the check
does not count; staging off removes four.

**Batch-mode staging does not fit once M6 is in prod.** It needs 382 MiB more
than the ceiling leaves: either the Argo CD → Flux replacement, being measured
in its own spike (about 450 MiB expected, unmeasured), or other savings. This
ADR does not decide Flux. Until one of them lands, a change to the batch
pipeline can be validated in staging only while prod's streaming stack is not
yet deployed (prod without M6, staging in batch mode: 5,054, room 578).

## Consequences

**Positive.**

- M6 fits in prod with staging's streaming mode beside it.
- A new batch source costs no budget; a new streaming source costs no new
  process. The platform grows by configuration, as ADR-0003 meant.
- Staging's state survives being switched off: its Postgres volume, its
  secrets and its quota stay. ADR-0007's objections to an ephemeral staging do
  not apply.
- The budget check closes a gap it has today: a run outside the pipeline's tag.

**Negative.**

- **Staging no longer runs the pipeline continuously** (decision 1). A source
  or infrastructure change between validations surfaces first in prod, behind
  its alarm.
- **Switching staging on or off is a pull request on a guarded path**
  (`platform/infra/`, [`.github/CODEOWNERS`](../../.github/CODEOWNERS)), so
  each one waits for the operator's approval of the `guardrail` environment.
  That stays until staging's folders leave the guarded list
  ([roadmap trigger table](../roadmap.md#trigger-table)).
- **Promotion leans more on the operator.**
  [`tools/promotion_check.py`](../../tools/promotion_check.py) checks that
  staging pinned a digest first, not that staging ran it. With staging off by
  default, a digest can be pinned there and never run; the operator's
  confirmation that staging's run succeeded, before the prod pull request
  ([`architecture.md` §9a](../architecture.md#9a-environments)), is the only
  guard.
- **Staging's alarm goes quiet.** With Dagster off, staging's Healthchecks.io
  check stops receiving pings and would alarm. It is paused when staging is
  switched off. Healthchecks.io resumes a paused check on its next ping, so
  switching staging on needs no step; this behaviour is checked when the
  first switch is made.
- **Staging's daily backup has nothing to back up while hibernated.** The
  `ScheduledBackup` is suspended (`spec.suspend: true`) with the `Cluster`,
  and resumed with it, so no failing Backup objects pile up. That this is
  enough is verified in the implementation pull request.
- **Staging's public dashboards go stale.** The pages at
  `lagom-staging.ofantini.com` stay in R2 while staging is off, with their
  `Page built at` stamp showing their age, and are rebuilt only by a run in
  batch mode, which does not fit once M6 is in prod. The runbook's staging
  checks of the dashboards' publishing path need batch mode too.
- **One run at a time.** A slow run delays the next job of any kind. A hung
  run holds the slot until Kubernetes stops it (`activeDeadlineSeconds:
  1500`) and Dagster's run monitoring, on by default in the chart, marks it
  failed; meanwhile no `usgs` run succeeds, so the heartbeat stops and the
  alarm fires. It fails loudly.
- **Shared streaming components share fate.** A crash of the hub, RisingWave
  or the Connect process stops every streaming source at once. Nothing is
  lost: the Pi's leaf keeps events (ADR-0045, decision 2) and the hub replays
  to RisingWave. Whether one invalid stream file stops a Connect process in
  streams mode is checked in its implementation.
- **One Grafana reads both environments** (decision 2, accepted risk).

**Open, for the operator.**

- **ADR-0045's archive job needs both modes at once.** It reads RisingWave
  and writes DuckLake through Dagster (ADR-0045, decision 7). Staging's two
  modes together request 2,892 MiB, which does not fit even before M6 is in
  prod (prod without M6, 3,122, plus 2,892 is 6,014). Options: validate the
  archive in prod first, where a live row is deleted only after its archive
  succeeded, so a failing archive leaves data in place and fails the run; or
  wait for the savings above. Decided when that job is built.
- **When M6 reaches prod.** Once it does, batch-mode staging stops fitting
  (above). The order of M6 in prod and the Flux spike is the operator's.

## Risks, cost, and reversal

- **The streaming figures are short runs on the operator's machine,** one
  hour for RisingWave and 18 minutes on the Pi. The 24-hour run is in staging,
  in streaming mode, before prod, and the requests are corrected from it.
- **Grafana's 128 MiB is a guess,** set from `kubectl top` after it runs.
- **CloudNativePG's hibernation** is read in its documentation, not run. The
  first switch-off in staging is the test: the Pod goes, the PVC and its data
  stay, and the Pod comes back on the same volume with the databases intact.
- **Cost:** none in money.
- **Reversal:** each decision is undone by its own revert. Staging comes back
  on permanently by setting replicas to 1 and the annotation to `"off"`, if
  the ceiling allows; Grafana splits into two by duplicating its Application;
  the global run limit is raised by changing one number and the check with
  it, in a new ADR.

**Reconsider when** the Flux spike reports, a client makes a second node the
premise (ADR-0007), or the queue's wait for the run slot makes a scheduled run
start late by more than a few minutes.

Sources checked on 2026-11-16: CloudNativePG's documentation, *Declarative
hibernation* (1.28 and 1.30); the `dagster/dagster` chart's values, version
1.13.27 (`helm show values`): `queuedRunCoordinator.maxConcurrentRuns`
"defaults to no limit", `runMonitoring.enabled: true`. Healthchecks.io
resuming a paused check on its next ping is from memory of its documentation
and checked at the first switch.
