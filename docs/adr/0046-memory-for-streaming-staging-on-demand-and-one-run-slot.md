# ADR-0046 — Memory for streaming: a lean staging on demand, one Grafana, one run slot per environment

- **Status:** Accepted, by the operator approving its pull request (#101) on
  2026-11-16. The decisions below were taken by the operator in conversation
  that day; how each is carried out, the CI checks and the figures were the
  architect's proposal. Being built in the pull requests that follow; what
  each one built is in the notes at the end.
- **Date:** 2026-11-16
- **Decided by:** the operator, on 2026-11-16, in two rounds.
  - First: staging on demand, with its Postgres hibernated while off; one
    Grafana for both environments; the `hello` Application removed; one run
    slot per environment for every batch job; streaming sources sharing one
    hub, one stream processor and one Redpanda Connect process. A bigger node
    is not an option.
  - Then, after a first draft of this ADR left batch-mode staging 382 MiB
    over the ceiling and needed the operator's approval for every switch:
    staging as lean as possible, with no backup (prod's backup is enough) and
    no Dagster UI; the switch operated by agents, not by the operator; the
    streaming design of ADR-0045 kept, RisingWave included, after a design
    with no stream processor was proposed and rejected (*Alternatives*); Argo
    CD's UI dropped, since it is not used; Flux measured as Argo CD's
    replacement in a spike of its own, kept only if it gives the same
    guarantees.
- **Amends:**
  - [ADR-0007](0007-three-environments.md): staging's namespace, secrets
    and quota stay permanent, and prod's NetworkPolicy keeps denying it; its
    workloads run on demand, one mode at a time.
  - [ADR-0009](0009-argocd-bootstrap-and-self-management.md): its rejection
    of a headless Argo CD (no UI or API server) is reversed; the operator
    does not use the UI.
  - [ADR-0014](0014-develop-against-staging.md): developing against
    `lagom_dev` needs staging's Postgres awake.
  - [ADR-0025](0025-postgres-backup-barman-cloud-to-r2.md): staging is no
    longer backed up; a change to the backup is validated in prod.
  - [ADR-0026](0026-dagster-one-instance-per-environment.md): staging runs no
    Dagster UI and no Dagster at all while off; the run queue limits every
    run, not only those tagged with the pipeline.
  - [ADR-0030](0030-operator-uis-behind-cloudflare-access.md): the path
    `ops.ofantini.com/dagster-staging` goes; `ops.ofantini.com/grafana`
    comes.
  - [ADR-0033](0033-dagster-alerts-through-healthchecks.md): staging's alarm
    reports failures only.
  - [ADR-0035](0035-agents-use-the-operators-scoped-token.md) and
    [ADR-0038](0038-leaner-agent-gates.md): one input to what Argo CD applies
    in staging, the mode file, sits outside the guarded list (decision 2).
  - [ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md)
    (proposed): RisingWave's request, one Grafana for both environments
    (decision 9), staging's streaming stack on demand, every streaming source
    sharing the components, and its archive job validated locally.

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
and were not yet in `main` when this ADR merged; they arrive with the
spikes' own pull request. The figures used here are restated:

- **RisingWave** fits a 1 GiB limit only with lean memory budgets, and its
  working set (p95 718 MiB over one hour) includes about 450 MiB of active file
  cache. The request it needs is about **768 MiB**, not the 512 ADR-0045
  planned. It refuses a compute budget under 512 MiB, so it cannot be made
  much smaller.
- **The NATS hub's** working set reached 58 MiB: a request of **64 MiB**.
- **Redpanda Connect's** working set reached 121 MiB (61 MiB of its own
  memory): **128 MiB**.
- **Grafana** is not measured; ADR-0045 and §9 estimate 150 MB. The request
  proposed is **128 MiB**, set from measurement when it is deployed.

With ADR-0045 as designed, a hub, RisingWave and Grafana in each environment
and staging's synthetic producer, M6 adds about 2,050 MiB and the total
reaches about 7,100 MiB, far over the ceiling. Later sources would add more,
and one cost the check does not see today: a batch source with its own run
tag.

## Measurement

`cd tools && uv run python resource_budget.py --repo ..`, on `main` at
`e01e579`, 2026-11-16:

| Application | Requests (MiB) |
|---|---|
| Argo CD (controller 384, repo-server 128, server 64, redis 32) | 608 |
| `postgres-prod`, `postgres-staging` (Postgres 300 + backup sidecar 64, each) | 364 + 364 |
| `dagster-prod`, `dagster-staging` (code server 320, daemon 320, webserver 160, each) | 800 + 800 |
| metrics-server 80, local-path 64, cert-manager 160 (controller 64, cainjector 64, webhook 32), CloudNativePG 64, Barman Cloud plugin 32, Sealed Secrets 32 | 432 |
| `cloudflared` | 48 |
| `hello` | 16 |
| CoreDNS, outside Git | 70 |
| Ephemeral jobs, all at once: one Dagster run per environment (768 each) and the Argo CD hook (32) | 1,568 |
| **Total** | **5,070** of 5,632 |

`kubectl top pods -A`, the same day, read-only, between runs: staging's
Dagster 143 + 275 + 235 MiB (webserver, daemon, code server) and Postgres
213 MiB; prod's Postgres 210 MiB; cert-manager 39, cainjector 35, webhook
22 MiB; `hello` 4 MiB. Earlier measurements, in
[`architecture.md` §11](../architecture.md#11-resource-budget): the daemon
uses 104 MiB at start, 206 after four hours and 263 after seven days; the
code server peaked at 291 MiB; a run peaked at 804 MiB.

## Requirements and constraints

- M6 must fit the ceiling with prod complete, and staging must still be able
  to validate what reaches prod, batch and streaming.
- The ceiling stays a check. A rule that depends on someone remembering what
  is switched on is not one.
- **The operator does not operate the switch.** Agents do.
- Staging stays isolated from prod ([ADR-0007](0007-three-environments.md)):
  its own Postgres, quota and secrets, and prod's NetworkPolicy, which admits
  nothing from staging. Nothing joins the two
  pipelines (ADR-0045, *Environments*).
- No prod data and no prod guarantee is lost.
- A new source, batch or streaming, costs configuration, not a new resident
  process.
- No bigger node.

## Alternatives considered

**Staging always on, M6 in prod only.** Rejected: M6 would reach prod
untested, and prod with M6 plus today's staging is about 6,000 MiB.

**Staging destroyed and recreated per pull request.** Rejected by ADR-0007 for
reasons that still hold: a failure takes its state with it. Hibernation keeps
the state.

**The first draft of this ADR: staging on demand at its present size,
switched by the operator.** Batch mode (1,932 MiB) did not fit once M6 was in
prod, so batch changes could not be validated at all, and every switch was a
pull request on a guarded path that waited for the operator. Rejected by the
operator.

**No stream processor: events in Postgres, windows computed when read.**
Redpanda Connect writes each event to the environment's Postgres, and dbt
views compute the event-time windows at query time, so late events are
always counted and RisingWave's 768 MiB per environment go. It would have let
staging stay always on, 66 MiB under the ceiling. Rejected by the operator:
it is not a design built for streaming. The showcase is a stream processor
keeping its views current as events arrive.

**A lighter stream processor**, searched on 2026-11-16. None fits better than
RisingWave:

- **Arroyo** (Rust, SQL, Apache-2.0, a NATS source): its documentation calls
  the single-node mode unsuitable for production, and since Cloudflare
  acquired it in April 2025 the work centres on Cloudflare's platform. No
  memory figures found.
- **Timeplus Proton** (C++ on ClickHouse, Apache-2.0): NATS is listed for its
  enterprise edition only; memory is reported between 200 MB and several GB,
  with no official figure.
- **Feldera** (incremental views, MIT, a dbt adapter): it compiles SQL to Rust
  inside its own container, beside a Postgres; one report is of a 32 GiB
  container stalling.
- **Bytewax** (Python): its repository's last push found was in March 2025.

**A Grafana per environment.** 128 MiB more, and two places to look. Rejected
by the operator.

**Keep the tag limit, and a run slot per batch source.** Correct, but each
source would add 768 MiB of budget for runs of seconds to minutes. Rejected
for one global slot (decision 6).

**One hub, stream processor and Connect process per streaming source.**
Isolation between sources at about 960 MiB a source. Rejected (decision 7).

**Requests trimmed below what was measured.** It would make room on paper
and evict Pods under pressure. Not done: every trim below stays above the
working set measured, except staging's daemon, whose request holds only for a
mode that runs for hours (decision 1).

## Decision

1. **Staging runs on demand, lean, one mode at a time.** Its states:

   | Mode | What runs | Requests (MiB) |
   |---|---|---|
   | `off` (the default) | nothing; Postgres hibernated | 0 |
   | `dev` | Postgres alone, for development against `lagom_dev` ([ADR-0014](0014-develop-against-staging.md)) | 256 |
   | `streaming` | NATS hub 64, RisingWave 768, synthetic producer 128 | 960 |
   | `batch` | Postgres 256, Dagster daemon 256, code server 320, its run 768 | 1,600 |

   - **No Dagster UI in staging.** The webserver goes, and with it
     `ops.ofantini.com/dagster-staging` from `cloudflared`. A run in staging is
     followed by its status in the daemon's and the run Pod's logs and by the
     failure alarm (decision 3). The operator rarely validates there.
   - **No backup in staging.** The Barman Cloud sidecar, the `ObjectStore`
     and the `ScheduledBackup` of `postgres-staging` go: staging's data is
     rebuilt by loading the source again. Prod's backup, with its restore
     test (runbook, *Backup and restore*), is the protection. The files
     already in `lagom-staging-backups` stay until deleted by hand, since
     the sidecar that applied their retention goes with it.
   - **Postgres is hibernated declaratively**, by the annotation
     `cnpg.io/hibernation: "on"` on staging's `Cluster`. In CloudNativePG 1.30
     (the operator on the node is 1.30.1, from chart 0.29.1), the operator
     then deletes the instance's Pod and keeps its PersistentVolumeClaim, so
     the data stays on the volume; the `Cluster` shows the condition
     `cnpg.io/hibernation` with reason `Hibernated`. Setting it to `"off"`
     recreates the Pod on the same volume. (CloudNativePG documentation,
     *Declarative hibernation*, versions 1.28 and 1.30, read on 2026-11-16.)
     Argo CD syncs the annotation like any field; it does not own the Pods the
     operator deletes.
   - **Dagster is off outside `batch`.** The pinned chart (1.13.27) accepts
     zero replicas for neither the daemon (its template fixes `replicas: 1`)
     nor a code server (its schema requires more than 0), so both are
     removed rather than scaled: `dagsterDaemon.enabled: false`, and
     `dagster-user-deployments.enabled: false` with `enableSubchart: false`.
     The webserver, gone in every mode, renders at `replicaCount: 0`. What is
     left requests nothing, and with no daemon nothing is dequeued, so no run
     starts.
   - **Lean requests:** Postgres 256 (213 measured for the Pod, 179 for its
     `postgres` container); the daemon 256, which holds for a mode that runs for hours
     (104 MiB at start, 206 after four hours) and not for days (263 after
     seven); the code server stays at 320 (291 measured).
   - **The quota caps what staging can hold.** Staging's ResourceQuota is
     lowered to the batch mode plus a small margin, so whatever is declared,
     the API server refuses Pods past that total. It does not refuse every
     pair of modes (`dev` and `streaming` together are 1,216 MiB); one mode
     at a time is held by the mode file's single value.
   - **The CI check counts the mode declared in Git**, so the ceiling stays a
     check. [`tools/resource_budget.py`](../../tools/resource_budget.py) learns
     that a hibernated `Cluster` requests nothing and that a Dagster
     Application with no daemon Deployment has no run slot.
   - **Runs in staging follow the schedules.** With no UI, and `kubectl exec`
     denied to agents, a change is validated by the next scheduled `usgs` run
     (every 30 minutes), which the dashboards' render follows. A job off
     that schedule, such as `lake_maintenance` at 04:15, is either waited for
     or launched by the operator (`dagster job launch` through `kubectl
     exec` on the code server).
   - **Argo CD shows `postgres-staging` as Suspended** while it is hibernated:
     its health check for CloudNativePG maps the hibernation condition to
     that state. It is expected, not a fault.

   **The trade-off, stated plainly: staging no longer exercises the pipeline
   continuously.** While it is off, a change in the USGS source, the lake or
   R2 is caught by prod and its Healthchecks.io alarm, not by staging first.
   Staging catches what a change brings, when it is switched on to validate
   that change.

2. **Agents operate the switch, not the operator.** The mode lives in one
   small file outside the guarded list (outside `platform/infra/`,
   [`.github/CODEOWNERS`](../../.github/CODEOWNERS)), holding one field,
   `mode`, with one of the four values above.
   - The file only selects among states that are defined in guarded paths;
     it carries no manifest and no value of its own. A CI check, in
     `tools/` (guarded), fails any content other than that one field and its
     four values. Only staging's Applications read it; prod's never do. How
     the file reaches each staging Application is settled in the
     implementation pull request.
   - A pull request that touches the file alone may set any of the four
     values, `batch` included. That is the point: an agent can switch staging
     on to validate without the operator, within what the modes allow.
   - A pull request that brings a change to validate in staging (a digest, a
     manifest) already touches guarded paths and is approved by the operator
     as today; it sets the mode in the same change. The pull request that
     switches staging off touches the mode file alone, so an agent merges it
     once the required checks pass and a reviewer approves (AGENTS.md §6).
   - Staging is switched off when its validation ends: the lean daemon's
     request assumes hours.
   - The mechanism is a guardrail change: the operator approves it once, in
     its implementation pull request.

3. **Staging's alarm reports failures only.** With staging off, nothing pings
   its Healthchecks.io check and it would alarm. Its success heartbeat is
   dropped; the failure sensor stays, so a run that fails in staging still
   reaches the operator. The check's period is set once to the longest
   Healthchecks.io allows, so silence while off raises nothing; the limit is
   checked when it is set.

4. **One Grafana for both environments**, in a namespace of its own,
   `grafana`, as `cloudflared` has. It reads each environment's live store
   (RisingWave) through that environment's own read-only role, with a
   connection limit and a statement timeout (ADR-0045, decision 9). Its own
   state is SQLite on its own volume, and its data sources and dashboards are
   files from Git, so nothing of Grafana lives in either environment's
   Postgres. It is served behind Cloudflare Access at
   `ops.ofantini.com/grafana`, like every operator UI
   ([ADR-0030](0030-operator-uis-behind-cloudflare-access.md)).

   **Why this is not the link between environments ADR-0045 forbids.** That
   rule is about the pipelines: no staging component reads, writes or
   triggers anything of prod, and the reverse. It holds. Data does not move
   between the environments. Prod's NetworkPolicy, which admits only `prod`
   and `cnpg-system` today, is amended to admit Grafana's namespace to
   RisingWave's port only, and still admits nothing from staging; staging has
   no NetworkPolicy, so Grafana reaches it as anything in the cluster can.
   Grafana is the operator's viewer: it holds two read-only credentials and
   writes nothing to either store. Two risks are accepted with it:
   - **one compromised Grafana reads both live stores,** and prod's holds the
     Pi's real events, which ADR-0045 keeps private (decision 11), while
     staging's holds synthetic ones. The viewer is behind a login and its
     roles are read-only;
   - **a change to Grafana itself** (an upgrade, a plugin, a dashboard file)
     reaches the viewer that holds prod's credential with no staging copy to
     try it on first. So Grafana is first deployed with staging's data source
     only, and prod's is added once it works; later changes are reviewed like
     any change to an operator UI, and reverted by Git.

5. **The `hello` Application is removed.** It is the M1 smoke test, a trivial
   nginx that proved the GitOps loop; Argo CD's own Applications and Dagster
   prove it every day since. 16 MiB of requests and one Pod. The runbook steps
   that use it (*Deploy (M1)*, whose measurements were made on it, and the
   staging-to-prod network test under *Postgres (M2)*, which reuses its nginx
   image because the node already holds it) are rewritten in the same pull
   request, and its line in `platform/infra/README.md` goes.

6. **One run slot per environment for every batch job.** Today the
   `QueuedRunCoordinator` limits only runs tagged `lagom/pipeline: usgs`
   (`tagConcurrencyLimits` in `platform/infra/dagster-<env>/values.yaml`,
   `PIPELINE_TAG` in
   [`definitions.py`](../../platform/orchestration/lagom_orchestration/definitions.py)),
   while the budget check counts one 768 MiB run per environment. A new
   source with a tag of its own, or a run launched with no tag, would run
   beside it and the check would not see it. The chart's default for
   `maxConcurrentRuns` is no limit.
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

7. **Streaming sources share their components**, in each environment:
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

8. **Platform requests trimmed to what was measured**, with no function
   lost:
   - **Argo CD without its UI and API server** (`argocd-server` removed,
     64 MiB). The operator reaches Argo CD through the kubeconfig already
     and the `argocd` CLI already talks to the cluster that way with `--core`
     (runbook, *Hand over to Argo CD (M1)*). Reconciliation, self-heal and pruning live in
     the application controller and do not change. This reverses ADR-0009's
     rejection of a headless install, made so the operator could watch the
     loop in the UI; the operator does not use it.
   - **cert-manager's controller and cainjector at 48 MiB each**, from 64
     (39 and 35 measured): 32 MiB.
   - **Prod's Postgres at 256 MiB**, from 300 (210 measured for the Pod and
     184 for its `postgres` container, between runs; its limit stays 512):
     44 MiB. §11 kept 300 "for dbt load", when dbt built every layer inside
     Postgres; since 2026-11-07 dbt runs in the run Pod on DuckLake, and
     Postgres holds the catalog and Dagster's history. Prod's Postgres is
     read during a run before the trim lands, and the trim waits if that
     reading is above 256.

9. **ADR-0045's archive job is validated locally.** It reads RisingWave and
   writes DuckLake through Dagster (ADR-0045, decision 7), so it needs both
   of staging's modes at once (2,560 MiB), which never fit. It is validated
   on the operator's machine in a compose like the spikes' (`platform/streaming-spike/`,
   on the spikes' branch), and in prod a live row is deleted only after its
   archive succeeded, so a failing archive leaves the data in place and
   fails its run.

### What fits

Requests in MiB, with the CI check's accounting. Prod with M6 is today's
total, less staging (1,932), `hello` (16) and the trims of decision 8 (140),
plus RisingWave 768, the hub 64 and one Grafana 128.

| Point | Total | Room under 5,632 |
|---|---|---|
| Today | 5,070 | 562 |
| Prod with M6, staging `off` | 3,942 | 1,690 |
| … staging `dev` | 4,198 | 1,434 |
| … staging `streaming` | 4,902 | 730 |
| … staging `batch` | **5,542** | **90** |
| … staging `batch`, if Flux replaces Argo CD (saving about 400 to 450 of Argo CD's 544, not measured) | ~5,100–5,150 | ~480–530 |

Pods that do not run also have no `containerd-shim`, about 20 MiB each outside
Kubernetes ([ADR-0024](0024-ci-ceiling-on-the-8-gb-node.md)), which the check
does not count; staging `off` removes four.

**Batch-mode staging fits with M6 in prod, by 90 MiB.** That is enough to
validate, and not enough to grow: until more room is found, a new resident
component in prod makes batch-mode staging stop fitting, and the check says
so on that component's pull request. The next room is expected from
replacing Argo CD with Flux, measured in a spike of its own and kept only if
it gives the same guarantees: reconciliation from Git, self-heal, pruning,
health of what it applies, and the CI checks rendering what it renders. That
is decided in its own ADR, not here.

## Consequences

**Positive.**

- M6 fits in prod, and staging can still validate both batch and streaming.
- The operator approves only the pull requests that already need it; staging
  is switched off by agents.
- A new batch source costs no budget; a new streaming source costs no new
  process. The platform grows by configuration, as ADR-0003 meant.
- Staging's state survives being switched off: its Postgres volume, its
  secrets and its quota stay.
- The budget check closes a gap it has today: a run outside the pipeline's
  tag.

**Negative.**

- **Staging no longer runs the pipeline continuously** (decision 1). A source
  or infrastructure change between validations surfaces first in prod, behind
  its alarm.
- **A change to the backup is validated in prod** (decision 1). A broken
  backup configuration reaches prod untried; it shows as a failed `Backup`
  object or a WAL archiving condition on the `Cluster`, which the runbook's
  checks read after any such change.
- **No Dagster UI in staging.** A failed staging run is read in its logs and
  in the alarm's e-mail, which carries the error.
- **Promotion leans more on the operator.**
  [`tools/promotion_check.py`](../../tools/promotion_check.py) checks that
  staging pinned a digest first, not that staging ran it. With staging off by
  default, a digest can be pinned there and never run; the operator's
  confirmation that staging's run succeeded, before the prod pull request
  ([`architecture.md` §9a](../architecture.md#9a-environments)), is the guard.
- **Agents can switch staging's mode without the operator** (decision 2).
  The modes are fixed in guarded paths, the budget check and the quota bound
  what any mode costs, and staging cannot reach prod, so the worst an agent
  can do is waste staging's memory or leave it off.
- **Lean requests sit close to what was measured.** A staging left in batch
  mode for days lets its daemon grow past its request, which makes it the
  first Pod the kubelet evicts when the node runs short; its limit is what
  stops it.
- **Runbook steps that rely on what goes** are rewritten in the pull request
  that removes it: *Check it in staging* of the dashboards' publishing path
  and *Start a run on demand* (both use staging's UI); the staging rows of
  *Backup and restore*, its restore test sized to staging's quota, and its
  "staging first"; the expected Pods of *Install Argo CD (M1)*, which list
  `argocd-server`.
- **Staging's public dashboards go stale.** The pages at
  `lagom-staging.ofantini.com` stay in R2 while staging is off, with their
  `Page built at` stamp showing their age, and are rebuilt by a run in batch
  mode.
- **One run at a time.** A slow run delays the next job of any kind. A hung
  run holds the slot until Kubernetes stops it (`activeDeadlineSeconds:
  1500`) and Dagster's run monitoring, on by default in the chart, marks it
  failed; meanwhile no `usgs` run succeeds, so the heartbeat stops and prod's
  alarm fires. It fails loudly.
- **Shared streaming components share fate.** A crash of the hub, RisingWave
  or the Connect process stops every streaming source at once. Nothing is
  lost: the Pi's leaf keeps events (ADR-0045, decision 2) and the hub replays
  to RisingWave. Whether one invalid stream file stops a Connect process in
  streams mode is checked in its implementation.
- **No Argo CD UI** (decision 8). The state of the loop is read with `kubectl`
  and `argocd --core`.
- **One Grafana reads both environments** (decision 4, two accepted risks).

## Risks, cost, and reversal

- **The streaming figures are short runs on the operator's machine,** one
  hour for RisingWave and 18 minutes on the Pi. The 24-hour run is in staging,
  in streaming mode, before prod, and the requests are corrected from it.
- **Grafana's 128 MiB is a guess,** set from `kubectl top` after it runs.
- **CloudNativePG's hibernation** is read in its documentation, not run. The
  first switch-off in staging is the test: the Pod goes, the PVC and its data
  stay, and the Pod comes back on the same volume with the databases intact.
- **90 MiB of room in batch mode** is thin. If a measurement raises any
  request, batch-mode staging stops fitting before Flux lands; the check says
  so, and staging validates in streaming mode or `dev` until then.
- **Cost:** none in money.
- **Reversal:** each decision is undone by its own revert. Staging returns to
  always on by setting its mode file's value permanently, if the ceiling
  allows; the backup sidecar, the webserver and `argocd-server` return by
  reverting their removal; Grafana splits into two by duplicating its
  Application; the global run limit is raised by changing one number and the
  check with it, in a new ADR.

**Reconsider when** the Flux spike reports, a client makes a second node the
premise (ADR-0007), or the queue's wait for the run slot makes a scheduled run
start late by more than a few minutes.

Sources checked on 2026-11-16: CloudNativePG's documentation, *Declarative
hibernation* (1.28 and 1.30); the `dagster/dagster` chart's values and
templates, version 1.13.27 (`helm show values`, `helm template`):
`queuedRunCoordinator.maxConcurrentRuns` "defaults to no limit",
`runMonitoring.enabled: true`, the daemon's `replicas: 1` fixed in its
template. For the stream processors: Arroyo's announcement of joining
Cloudflare and its deployment documentation; Timeplus's Proton documentation
and its comparison of editions; Feldera's pricing page, its NATS connector,
its memory documentation and issue 7450; Bytewax's repository activity as
indexed by ecosyste.ms. None of those processors was run. The longest period
a Healthchecks.io check allows is from memory of its documentation and
checked when set.

## Notes

The text above is kept as accepted. These notes record what was built,
without rewriting it.

- **2026-11-16 — `hello` removed, and the first trims (decisions 5 and 8).**
  The `hello` Application and its manifests are gone; `argocd-server` runs at
  zero replicas; cert-manager's controller and cainjector request 48 MiB.
  The budget check's total falls from 5,070 to 4,958 MiB. Prod's Postgres
  trim waits for a reading during a run, as decision 8 says.
