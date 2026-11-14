# ADR-0044 — Credentials per job, and the path the dashboards take to be published

- **Status:** Proposed. The operator accepts it by approving the pull request
  that adds it. Decisions 1 to 6 are built in that pull request; decisions 7
  to 9 are the operator's, taken on 2026-11-14, and are built in the next one.
- **Date:** 2026-11-14
- **Decided by:** the operator, in conversation on 2026-11-14: the dashboards'
  render runs as a Dagster step in a job of its own, with credentials of its
  own; "no hack", so if per-job credentials do not exist they are built first;
  the catalog's password must never be exposed (rated high risk).
- **Amends:** [ADR-0033](0033-dagster-alerts-through-healthchecks.md),
  decision 5 (run Pods inherit every secret of the code server): a job now
  declares the Secrets its Pod gets, and the ping URL is the only one left
  shared. The "Credentials per job" row of the
  [roadmap's trigger table](../roadmap.md#trigger-table): entered early, by the
  operator's decision, and only in part.
  [ADR-0040](0040-dbt-charts-for-the-dashboards.md), *Open, for the publishing
  pull request* (the Cloudflare Pages project and the token) and decision 5
  (telemetry off).

## Context and problem

The code server's `env` in each environment's `values.yaml` held the catalog
owner's connection string, the R2 write token, the variables dlt builds from
them, and the ping URL. The chart's `includeConfigInLaunchedRuns` copies that
whole environment into every run Pod, with no way to leave a variable out
(ADR-0033, decision 5). That was accepted while every run was the pipeline's
own. The dashboards' render is not: it reads a public dataset, writes nothing,
and its output can leak. DuckDB repeats the catalog URI, password included,
when an attach fails (ADR-0040, *Negative*), and a render also runs a tool we
do not control (`dct`). Giving it the owner's password would let a leak write
the catalog.

ADR-0040 lists four defences for the password: keep it out of the connection
string, a read-only role, redaction, and rotation. The second needs two things
that did not exist: a role to give, and a way to give one job a credential
another job does not have.

## Requirements and constraints

- The render step holds no credential that writes the catalog or the data.
- The pipeline's jobs keep working, with no more than they use today.
- One image per environment pair, promoted by digest
  ([ADR-0015](0015-images-on-ghcr.md)): what differs between staging and prod
  is configuration, not code.
- A credential is never printed, logged or committed; the operator seals it
  without seeing it.
- No new resident process and no change to the memory budget.

## Alternatives considered

**A second code location for the dashboards,** with its own user-code
deployment and environment. Isolates by process, not only by Pod. Rejected: a
second resident code server is about 320 MiB of request per environment, and
there is no room for it
([`architecture.md` §11](../architecture.md#11-resource-budget)).

**The run launcher's `env_secrets`.** A tag can name a Secret whose every key
becomes a variable. Rejected: it hands a job every key of a Secret, including
ones it does not use, and the variables take the Secret's key names.
`valueFrom.secretKeyRef` names each variable and each key.

**Environment-specific values in the code.** Rejected: the image is promoted
from staging to prod by digest, so a bucket name in the code would make prod run
code staging never ran.

**Leaving the jobs as they are and giving the render the owner's credentials
with a good redaction.** Rejected by the operator: redaction is a safety net,
not a control, and the password is rated high risk.

## Decision

1. **Each job declares the Secrets its Pod gets,** in its own
   `dagster-k8s/config` tag (`container_config.env`, with
   `valueFrom.secretKeyRef`), defined in
   `platform/orchestration/lagom_orchestration/definitions.py` beside
   `RUN_RESOURCES`. `usgs` and `lake_maintenance` get what they used before and
   no more: the catalog owner's connection string (`postgres-app`), the R2
   write token (`r2-lake`), and the variables dlt and dbt build from them. A
   job that needs less is given less.

2. **The code server keeps what loading the definitions needs, and what is not
   secret.** `HEARTBEAT_URL` stays, because the sensors read it when the module
   is imported, and a run's process imports the same module. The code server's
   `env` also keeps the settings that are not secret and are written out in the
   environment's values: the bucket, the data path, the metadata schema, the
   region, `PGSSLMODE`. A test imports the definitions with only
   `HEARTBEAT_URL` set.

3. **How one image serves two environments.** A Secret is namespaced, so the
   same name resolves to staging's Secret in the `staging` namespace and to
   prod's in `prod`: the code names `postgres-app` and `r2-lake`, and the host
   of the catalog and the keys come from whichever namespace the Pod runs in.
   What differs between environments and is not secret, the bucket, stays in
   each environment's `values.yaml`, where it differs today. The rule to
   remember: *the values say which settings the environment has; the code says
   which Secrets each job gets.*

4. **The merge is proven offline.** dagster-k8s 0.29.27 merges a run's tag
   over the code location's context with the default `DEEP` behaviour: a list
   is the code location's followed by the tag's, with identical entries kept
   once, and a scalar is replaced. The de-duplication compares whole entries,
   not names: a variable defined in both places with different values is
   defined twice, and Kubernetes keeps the last one, the tag's. A test builds the Job for each job with the launcher's
   own `get_container_context_for_run` and `construct_dagster_k8s_job`, and
   asserts that the Pod of `usgs` and of `lake_maintenance` has the owner's and
   the write token's Secrets, and that a Pod built from the code location alone
   has none of them. The order matters and is tested: Kubernetes expands
   `$(NAME)` only from variables defined *above* it, and the tag's variables
   come after the code server's, so every composed variable sits in the tag,
   below the Secrets it reads.

5. **A run with no tag has no credentials.** The implicit asset job, which a
   run started from the asset catalog belongs to, has no tags: its Pod holds
   only the code server's variables and fails at its first asset. That is the
   safe failure; the runbook already says not to start a run there.

6. **The read-only role `lake_reader`.**
   - **The role** is declared in `spec.managed.roles` of both Postgres
     clusters: login, no superuser, no `CREATEDB`, no `CREATEROLE`,
     `connectionLimit: 3` (the clusters allow 25), password from the sealed
     basic-auth Secret `lake-reader-credentials` of the namespace. The operator
     seals it with a command that never prints the password (runbook: *The
     catalog's read-only role*).
   - **Its privileges** are `USAGE` on the schema `ducklake` of the database
     `lagom` and `SELECT` on its tables, including the tables DuckLake creates
     later (the ones that hold inlined rows). They are given by a step of the
     daily `lake_maintenance` job, `grant_reader_access` in `lake.py`, as the
     catalog's owner, directly against Postgres and not through DuckDB's
     attach: `GRANT USAGE ON SCHEMA`, `GRANT SELECT ON ALL TABLES IN SCHEMA`,
     and `ALTER DEFAULT PRIVILEGES IN SCHEMA … GRANT SELECT ON TABLES`, in one
     transaction. Each can run again. The default privileges are what covers a
     table created after the first run; the daily grant repairs one that
     slipped past, and shows if the role has gone.
   - **A role that does not exist fails the step,** loudly and by name. The
     alternative, skipping on "role does not exist", would let a deploy without
     the sealed Secret pass unseen for as long as nobody looks, and the reader
     would then fail on its first query. The cost: until the operator seals the
     Secret, the daily job fails, the failure alarm e-mails, and the
     environment's check goes down until the next `usgs` ping. Compaction does
     not depend on the grant, so it still runs. The runbook has the Secrets
     sealed before the change is merged.
   - **The grants are not repeated at the end of each `usgs` run.** The default
     privileges already cover what the pipeline creates, and a node in the
     asset graph for a grant would be structure for no gain.

7. **The render is a Dagster asset in a job of its own,** with read-only
   credentials: `lake_reader`'s password, and a read-only R2 token. Built in the
   next pull request. This one adds the two parts it needs:
   `attach_lake(read_only=True)` adds DuckLake's `READ_ONLY` attach option, and
   the catalog string may carry no password, which libpq then reads from
   `PGPASSWORD` (seen locally in ADR-0041's spike; *to be verified in staging*
   with the real role). No dashboards job exists yet.

8. **The pages are published to an R2 public bucket per environment, on a
   custom domain, with a token scoped to that bucket,** instead of Cloudflare
   Pages. The reason is the token: as far as the architect knows, a Cloudflare
   Pages API token is account-wide, so a leak from the render step could touch
   every Pages project and zone of the account, while an R2 token can be limited
   to one bucket. **To be confirmed in the Cloudflare dashboard** before the
   publishing pull request is written; if a Pages token can be scoped to one
   project, this decision is reconsidered. The hostnames and the bucket names
   are chosen in that pull request. This amends ADR-0040's Pages project and the
   token it lists as open.

9. **dbt Charts telemetry stays on** in the cluster step: the operator's choice
   of 2026-11-14, to help the dbt team, provided it costs nothing here. This
   amends ADR-0040, decision 5. It is two requests per command to dbt Labs, with
   no board content (ADR-0040). The condition is tested in staging before prod:
   a blocked or a slow telemetry endpoint must never fail or stall a render. If
   it does, `DO_NOT_TRACK=1` goes back into the step. CI keeps it off: it runs
   on a fixture, and a gate that waits for a third party is not a gate.

## Consequences

**Positive.**

- A job's Pod holds the credentials its job declares; the dashboards' render
  can run without the owner's password or the R2 write token.
- A job's credential set is in code that is reviewed and tested, not in a
  values file read by hand.
- The one image still serves both environments.

**Negative, and what it costs.**

- **Only half of the trigger is met.** `usgs` and `lake_maintenance` still hold
  the owner's connection string and the R2 write token: ingestion that writes
  only bronze, and dbt only the layers it builds, as the roadmap row describes,
  are not separated, and the credentials are still static. Short-lived
  credentials stay deferred.
- **The ping URL is still in every run Pod,** the render's included. A reader
  of it can silence the alarm, and it can send a body to the check. To remove
  it, the sensors would have to read the URL when they run and not when the
  module loads, which gives up ADR-0033's "does not load without it". Not done
  here.
- **The catalog password is still inside the writers' connection string,** so a
  failed attach in `usgs` or `lake_maintenance` still echoes it and redaction is
  still the only protection there. The password leaves the string for the
  readers only; the roadmap item for the writers stays open.
- **A change of a job's credentials is a change of code.** It reaches an
  environment with a new image, pinned by digest, staging first. The order of
  the rollout matters and is in the runbook: the new image first, and only then
  the removal of the credentials from the values.
- **A run started from the asset catalog now fails** instead of taking the code
  server's memory (decision 5).
- **`lake_reader` can reach other databases of the cluster.** Postgres grants
  `CONNECT` on every database to every role, so the role can open a connection
  to `dagster`; it holds no privilege on its tables. Revoking `CONNECT` from
  `PUBLIC` is a change to the clusters that this ADR does not make.
- **The default privileges cover tables the owner creates.** Another role
  creating a table in the schema would not be covered. DuckLake creates its
  tables as the user that attaches, which is the owner.
- **Not proven offline:** the grants against a real Postgres, and a read-only
  attach by the restricted role against a real DuckLake catalog. The unit
  tests check the statements, the failure modes, the option, and a read-only
  attach on a local DuckLake that cannot write. Verification in staging is in
  the runbook.

## Risks, cost, and reversal

Cost: none on the node. No new Pod and no new request; the maintenance job opens
one more Postgres connection for a second.

Risk: a job's tag names a Secret that does not exist in the namespace; its Pod
stays in `CreateContainerConfigError` and the run fails. The Secrets the tags
name are the ones the code server used before, so the pipeline needs nothing
new.

Risk: a rollout in the wrong order. Removing the credentials from the values
before an image with the tags is running leaves every run without them. The
order is in the runbook.

Reversal: put the removed variables back in the environment's `values.yaml` and
the pipeline reads them from the code server again (the tags' identical
copies are kept once; a copy that differs would win, as decision 4 says). The role goes with `ensure: absent` in the clusters'
`managed.roles`; removing it from the list alone would leave it in Postgres.

**Reconsider when** the render needs a second credential, a second reader
arrives (its own role, as ADR-0005 and `architecture.md` §10 describe for
`gold_reader`), or the Cloudflare check in decision 8 says a Pages token can be
scoped to one project.

Sources: dagster-k8s 0.29.27 (`container_context.py`, `launcher.py`, `job.py`),
read in the installed package on 2026-11-14; the CloudNativePG 0.29.1 chart's
`Cluster` schema for `connectionLimit`.
