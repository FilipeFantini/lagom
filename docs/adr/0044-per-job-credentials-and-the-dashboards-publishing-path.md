# ADR-0044 — Credentials per job, and the path the dashboards take to be published

- **Status:** Accepted, by the operator approving its pull request (#80) on
  2026-11-14. Decisions 1 to 6 were built in that pull request; decisions 7 to
  9 are the operator's, taken on 2026-11-14, and are built in the second one,
  the dashboards' render (*What decisions 7 to 9 became*, below).
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

**2026-11-20 amendment.** [ADR-0056](0056-the-data-console.md), decision 3:
other producers will share the dashboards bucket, so the step that removes
stale pages will delete only under its own prefix. Until that change is
built, the step still deletes every `.html` key it did not write.

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
   credentials: `lake_reader`'s password, and a read-only R2 token. The first
   pull request added the two parts it needs:
   `attach_lake(read_only=True)` adds DuckLake's `READ_ONLY` attach option, and
   the catalog string may carry no password, which libpq then reads from
   `PGPASSWORD`. The second pull request adds the job (below). `PGPASSWORD`
   with the role's grants was run against a real Postgres on 2026-11-14 (below);
   *the real cluster's role and R2 are verified in staging*.

8. **The pages are published to an R2 public bucket per environment, on a
   custom domain, with a token scoped to that bucket,** instead of Cloudflare
   Pages. The reason is the token: as far as the architect knows, a Cloudflare
   Pages API token is account-wide, so a leak from the render step could touch
   every Pages project and zone of the account, while an R2 token can be limited
   to one bucket. **To be confirmed in the Cloudflare dashboard** before the
   publishing pull request is written; if a Pages token can be scoped to one
   project, this decision is reconsidered. The hostnames and the bucket names
   are chosen in that pull request (below). This amends ADR-0040's Pages project
   and the token it lists as open.

9. **dbt Charts telemetry stays on** in the cluster step: the operator's choice
   of 2026-11-14, to help the dbt team, provided it costs nothing here. This
   amends ADR-0040, decision 5. It is two requests per command to dbt Labs, with
   no board content (ADR-0040). The condition is tested in staging before prod:
   a blocked or a slow telemetry endpoint must never fail or stall a render. If
   it does, `DO_NOT_TRACK=1` goes back into the step. CI keeps it off: it runs
   on a fixture, and a gate that waits for a third party is not a gate.

## What decisions 7 to 9 became

Built in the second pull request. The names, so that the code, the Secrets and
the Cloudflare setup can be checked against each other:

| What | Staging | Prod |
|---|---|---|
| Bucket (public, in `LAGOM_DASHBOARDS_BUCKET` of the Dagster values) | `lagom-staging-dashboards` | `lagom-prod-dashboards` |
| Hostname (a custom domain on the bucket) | `lagom-staging.ofantini.com` | `lagom.ofantini.com` |
| Read-only token on the lake bucket: Secret `r2-lake-reader` | keys `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY`, `ENDPOINT` | the same, in `prod` |
| Token scoped to the dashboards bucket: Secret `r2-dashboards` | the same three keys | the same, in `prod` |
| The role's password: Secret `lake-reader-credentials` (decision 6) | key `password` | the same, in `prod` |

The Secrets are sealed by the operator in `platform/infra/postgres-<env>/`
(runbook: *The dashboards' publishing path*); no placeholder is in the
repository. The Cloudflare side, as the operator set it up: a public bucket per
environment with a custom domain, minimum TLS 1.2, the `r2.dev` address off,
and tokens limited to one bucket. Decision 8's check of the Pages token was not
needed for the build: the bucket-scoped token is what is built.

- **The job.** `dashboards`, with the one asset `dashboards/pages` in the group
  `dashboards` (an asset and a job cannot share the name `dashboards`), defined
  in `definitions.py` beside the writers' jobs. Its tag is the pipeline's
  (`lagom/pipeline: usgs`), so the run queue never runs it beside `usgs` and the
  memory budget does not change, and its `dagster-k8s/config` carries `READER_ENV`:
  `PGPASSWORD`; the catalog string `ducklake:postgres:host=postgres-rw port=5432
  dbname=lagom user=lake_reader sslmode=require`, with no password;
  `LAGOM_R2_*` from `r2-lake-reader` (the writers' variable names, so
  `attach_lake` and the dbt `lake` target read them unchanged); and
  `LAGOM_DASHBOARDS_*` from `r2-dashboards`. `postgres-rw` is the Service
  CloudNativePG makes for the cluster `postgres` in the Pod's own namespace.
  Nothing from `postgres-app` or `r2-lake`: a test builds the Pod from each
  environment's real values and fails if one appears. The asset has no
  dependency on gold: an asset downstream of `usgs/events` would be selected by
  the `usgs` job, which selects `.downstream()`, and would run with the writers'
  credentials.
- **The trigger.** A run-status sensor, `render_dashboards_after_usgs`, requests
  one `dashboards` run after each successful `usgs` run, with the `usgs` run's
  ID as the run key. The alarm needed no change: `run_failure_alarm` has no job
  filter and already e-mails a failed run of any job in the location (a test
  fails a job named `dashboards` and reads the call), and the heartbeat is
  declared for `usgs` alone.
- **The step**, in `lagom_orchestration/dashboards.py`, in this order and
  failing at the first thing wrong: `dct validate --strict --warehouse`;
  `dct render` to HTML and to the rows; `check_boards.py`; the watermark; the
  upload. The attach is read-only twice over: the dct profile
  (`products/dashboards/profiles.yml`, `read_only: true`) and `check_boards.py`
  use DuckLake's `READ_ONLY` option, and the role could not write anyway. The
  dbt manifest `dct` resolves `ref()` against is the one the image's build wrote
  with `dbt parse`: the project is read-only at run time and the manifest is of
  the same code, so the step does not parse again.
- **The tool does not see what it does not need.** `dct` and the check run with
  the lake's variables, `PGPASSWORD` and what a process needs, taken from a
  list; the dashboards bucket's token, the ping URL and everything else of the
  Pod's environment are left out. `DO_NOT_TRACK` is left out too, which is
  decision 9: the image sets it for dlt and dbt, and the step's tool does not
  inherit it. Every command has a timeout (180 s to validate, 360 s per render,
  120 s to check; the Pod's deadline is 1,500 s), so a telemetry request that
  never answers is a killed command with its name in the error and not a run
  that hangs. Errors go through `redact_secrets`, are not chained to the
  original exception, and never carry the environment.
- **The watermark is a dbt Charts feature,** not post-processing:
  `style.timestamp` with `position: top` and the format
  `Page built at %Y-%m-%d %H:%M %Z`, in the board. It is the render's clock in the
  board's zone, UTC, and sits above the title, over the line that gives the
  data's own times. The step does not trust it: each page must contain the
  marker text exactly once, and the whole line exactly once, and the time must be
  within 15 minutes of now (2 minutes ahead), or the page is not uploaded. A test
  keeps the board's format and the step's marker together.
- **The upload** uses botocore (already in the image through dlt's s3 extra; now
  declared) against R2's S3 endpoint, path style, with
  `request_checksum_calculation: when_required` (newer botocore adds a checksum
  header R2 does not accept on uploads), timeouts and three retries. Each page is
  one file that loads nothing from outside (CI checks it), so there are no
  assets to hash, cache for longer, or upload before the HTML; the pages get
  `Content-Type: text/html; charset=utf-8` and `Cache-Control: public,
  max-age=300`. Stale pages are deleted only after the last upload, and only
  `.html` keys, so a failure part-way leaves the old set and never fewer pages
  than before, and anything else in the bucket is left alone.
- **Verified here,** against a real Postgres 17 in a container with the
  role's real grants (`grant_reader_access`): the catalog string with no
  password and `PGPASSWORD` attaches; the read-only session reads and refuses an
  `INSERT` and a `CREATE`; a failed attach (wrong password) prints neither
  password. Against the fixture lake in the built image, as the Pod runs
  (user 10001, read-only root, 1 GiB limit): `dct validate`, both renders and
  the check pass and the page carries its watermark. **Not verified:** real R2
  (the upload, the checksum setting, the bucket-scoped token, the public
  domain), `lake_reader` against the cluster's catalog, telemetry behaviour on
  the node's network, and memory and time on real data. Staging's checks are in
  the runbook.

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
- **The render's Pod still holds two credentials it does not use.** The ping
  URL (above), and `DAGSTER_PG_PASSWORD`: the run launcher puts the password of
  Dagster's own metadata database in every run Pod, because the run's process
  writes its events there. A leak from the render could therefore write Dagster's
  history. It does not reach the lake, the catalog or the buckets. Not changed
  here; the run's process needs it.
- **The pages are public and the step publishes whatever gold holds.** The
  check proves the page equals gold, not that gold is fit to publish; the
  boards read public data only, as `architecture.md` §9 says.
- **A run started from the asset catalog now fails** instead of taking the code
  server's memory (decision 5).
- **`lake_reader` can reach other databases of the cluster.** Postgres grants
  `CONNECT` on every database to every role, so the role can open a connection
  to `dagster`; it holds no privilege on its tables. Revoking `CONNECT` from
  `PUBLIC` is a change to the clusters that this ADR does not make.
- **The default privileges cover tables the owner creates.** Another role
  creating a table in the schema would not be covered. DuckLake creates its
  tables as the user that attaches, which is the owner.
- **Not proven offline:** the grants and the role's read-only attach against
  the cluster's Postgres and R2. The unit tests check the statements, the
  failure modes, the option, and a read-only attach on a local DuckLake that
  cannot write; the second pull request ran the grants and the attach once against
  a throwaway Postgres (above). Verification in staging is in the runbook.

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
