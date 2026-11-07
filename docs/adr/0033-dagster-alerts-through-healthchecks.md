# ADR-0033 — Dagster raises the alarm: a failure sensor and a heartbeat, both through Healthchecks.io

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: retire the CronJobs now, with
  the alarm, and accept the ping URL in run Pods (decision 5).
- **Amends:** in [ADR-0026](0026-dagster-one-instance-per-environment.md),
  *Heartbeat*: "native failure alerting or a ping after each successful run"
  becomes both, through one check; and *DuckLake*: the CronJobs on Postgres
  are retired in the change that turns these sensors on, without waiting for
  a week in prod (2026-11-12) or for
  [ADR-0023](0023-sme-stack-duckdb-on-ducklake.md) to be accepted or revised.

## Context and problem

ADR-0026 keeps the CronJobs on Postgres running until two conditions hold:
Dagster has run a week in prod (from 2026-11-12), and ADR-0023 is accepted or
revised with the spike's numbers. Dagster must also have taken over the alarm. Today the only alarm is the
CronJob's heartbeat to Healthchecks.io ([ADR-0018](0018-pipeline-heartbeat.md)).
Dagster's runs are watched in its UI, which tells nobody.

On 2026-11-07 the operator decided to waive both conditions. Dagster has run
USGS on DuckLake in staging since 2026-10-28 (372 of 373 runs succeeded in the
first week) and in prod since 2026-11-05. The CronJobs feed a Postgres
warehouse nothing reads. The spike's open items — Evidence reading the lake,
compaction, the memory and time comparison — do not need the CronJobs: the
Postgres side of the comparison is measured once, by hand, from a re-load, if
ADR-0023 still asks for it. What must not happen is a window with no alarm. So the
CronJobs go in the same change that turns Dagster's alarm on.

Two kinds of failure have to reach the operator:

- **A run fails.** It should be reported at once, with the error.
- **Nothing runs.** The daemon, the code server, the node or the cluster is
  down. Nothing inside the cluster can report this, because it is down too.

Native alerting answers the first and is silent on the second: it runs on the
node it watches. A heartbeat answers the second, but only after the schedule
plus the grace period has passed, and it says nothing about why.

## Requirements and constraints

- Both kinds of failure reach a person, without opening a port.
- No change to the resident memory budget
  ([`architecture.md`](../architecture.md#11-resource-budget) §11).
- No monetary cost; no SMTP or chat credentials in the cluster.
- Each environment alerts on its own.
- No window in which an environment has no alarm.

## Alternatives considered

**Dagster's native alerting alone** (a run-failure sensor sending e-mail or a
chat message). Rejected: it needs an SMTP or chat credential in the cluster
([ADR-0018](0018-pipeline-heartbeat.md) rejected the same for the Job), and it
says nothing when the daemon or the node is down.

**A success ping alone,** as the CronJob does. Rejected: a failed run is
reported only when its ping is late, up to about 50 minutes later, and without
the error.

**A new check per environment, beside the CronJob's.** Needed only while both
pipelines run, so that the CronJob's success could not clear a Dagster
failure. Rejected: the CronJobs go in the same change, so nothing else pings
the check, and a new check would add a sealed secret per environment for no
gain.

**Retire the CronJobs first, alarm later.** Rejected: between the two changes
a failed or missing Dagster run would tell nobody, and the CronJob's check
would e-mail a false "down" as soon as its pings stopped.

## Decision

1. **Dagster takes over the existing check per environment,**
   `lagom-usgs-staging` and `lagom-usgs-prod`, and the Secret
   `pipeline-heartbeat` (key `url`) that already exists in the `staging` and
   `prod` namespaces, where Dagster runs. Schedule stays `*/30 * * * *` (UTC),
   the USGS schedule's. Its grace period is set from the longest run measured
   in staging plus the queue delay, before the change reaches prod.
2. **A run-status sensor pings the check after each successful run** of the
   USGS job. A missing ping is the alarm for "nothing runs", as in ADR-0018.
3. **A run-failure sensor calls the check's `/fail` endpoint** for any failed
   run in the code location, with the job name, the run ID and the error in the
   request body, cut to 10,000 bytes, keeping its start and its end.
   Healthchecks.io stores the first 100 kB of a body; the smaller cap is for
   the person reading the e-mail. Healthchecks e-mails at once. The next successful run clears it.
4. **Both sensors run in the code location,** which already runs all the time.
   No new process, no new image.
5. **The ping URL is a secret,** as in ADR-0018: whoever has it can silence the
   alarm. It reaches the code server as `HEARTBEAT_URL`, and **run Pods get it
   too.** The chart copies the code server's whole environment into every run
   Pod (`includeConfigInLaunchedRuns`), with no way to leave one variable out;
   and run Pods import the same module, which refuses to load without it.
   Keeping it out would mean declaring every run's environment apart from the
   code server's. That is not done: run Pods already hold the catalog owner's
   password and the R2 write token, so a reader of the URL in a run Pod
   already holds far more, and the URL's worst use, silencing the alarm, adds
   nothing to that. The CronJob's Pod holds it today, beside dlt and dbt.
   Decided by the operator on 2026-11-07. The sensors never print the URL. A
   missing variable fails the code location at load, loudly.
6. **A ping that cannot be sent fails the sensor tick** after three attempts,
   and Dagster shows the failed tick. The sensor's cursor still moves past
   that run, so the call is not retried later. For a failed success ping the
   missing ping raises the alarm anyway; for a failed `/fail` the failed tick
   is the only trace, and for a failed `lake_maintenance` run no missing ping
   covers it.
7. **The CronJob `usgs-pipeline` is deleted in the same change, per
   environment,** staging first. Prod's change lands only after staging has
   shown both a ping from the success sensor and a `/fail` from a run made to
   fail on purpose. The Postgres `Cluster`s stay: they hold Dagster's
   metastore and DuckLake's catalog. The old warehouse schemas in them are
   dropped by a separate, recorded step.

## Consequences

**Positive.** A failed run is reported within a minute, with its error. A
silent cluster is still reported by the missing ping. No new secret and no new
check; one pipeline per environment instead of two.

**Negative.**

- **The Postgres warehouse stops updating** the moment the CronJobs go.
  Nothing reads it. If ADR-0023 is revised instead of accepted, its reversal
  applies: the dbt models are re-targeted to Postgres and the data loaded
  back from the Parquet files, or re-loaded from USGS.
- **The roadmap's warehouse rule changes earlier than planned:** gold lives in
  DuckLake from this change, while ADR-0023 is still Proposed. The spike
  decides whether it stays there.
- **A failure in any job,** compaction included, marks the environment's check
  down until the next successful USGS run. That is deliberate: the check means
  "this environment's pipeline is healthy".

## Risks, cost, and reversal

Cost: none. The code server's memory does not change measurably; the sensors
are a few HTTP calls every 30 minutes.

Risk: a sensor that is turned off sends nothing. The success sensor's silence
is caught by the missing ping; the failure sensor's is not. Both are declared
with `default_status=RUNNING` and a test asserts it. The same gap opens for one
run when its `/fail` cannot be sent (decision 6).

Risk: every Pod of a run holds the ping URL (decision 5). It is covered by the
same defences as the database and R2 credentials beside it: images built by CI
from reviewed code, pinned by digest, and no inbound port on the node.

Reversal: restore the CronJob manifests from Git history and remove the two
sensors; the check and the Secret are the CronJob's again.
