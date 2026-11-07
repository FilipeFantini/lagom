# platform/orchestration/

The platform's Dagster code location ([ADR-0026](../../docs/adr/0026-dagster-one-instance-per-environment.md)).
One image, `lagom-orchestration`, runs both the code server and every run Pod.

| Path | What it is |
|---|---|
| `lagom_orchestration/definitions.py` | The `Definitions`: each service's assets, the dbt project as assets, the jobs and their schedules |
| `lagom_orchestration/alarm.py` | The two sensors that raise the alarm through Healthchecks.io ([ADR-0033](../../docs/adr/0033-dagster-alerts-through-healthchecks.md)) |
| `lagom_orchestration/lake.py` | Attaching the environment's DuckLake, and its daily maintenance |
| `Dockerfile` | Built from the repository root: it bundles `services/*/ingestion`, `services/*/assets` and `platform/warehouse` |
| `tests/` | The code location loads, and its graph, schedules and alarm sensors are the intended ones |

**Jobs.**

- `usgs`, every 30 minutes: `usgs/events` (dlt into DuckLake), then `dbt build`
  on the warehouse's `lake` target.
- `lake_maintenance`, daily at 04:15 UTC: merge small files, expire snapshots
  older than 7 days, and delete the files nothing references.

Both carry the tag `lagom/pipeline: usgs`, which the run queue limits to one
run at a time, and a `dagster-k8s/config` tag with the memory of the run's Pod
(`RUN_RESOURCES`). Without it a run would inherit the code server's, which is
about half of what a run needs. Each environment's Application declares
the same request for the budget check, and the tests fail if they differ.
Steps run in the run's own process (`in_process_executor`); dbt is still a
process of its own.

**The alarm** ([ADR-0033](../../docs/adr/0033-dagster-alerts-through-healthchecks.md)).
Two sensors run in the code server, both `RUNNING` from the start (a test
asserts it: a sensor that is off sends nothing):

- `usgs_success_heartbeat` pings the environment's Healthchecks.io check after
  each successful `usgs` run. A missing ping is the alarm for "nothing runs".
- `run_failure_alarm` calls the check's `/fail` after any failed run in this
  code location, with the job, the run ID and the error as a plain-text body,
  cut to 10,000 bytes (Healthchecks.io stores 100 kB). It e-mails at once.

Each call is tried three times, with a 10-second timeout, and then raises, so
the sensor's tick fails and shows in the UI. Dagster still moves past the run
that tick was for, so a `/fail` that could not be sent is not retried later:
the failed tick is the only trace, and for a failed `lake_maintenance` run
nothing else raises the alarm.

The ping URL is a secret: whoever has it can silence the alarm. It is read
from `HEARTBEAT_URL` when the code location loads, and **the code location
does not load without it**. Nothing prints it: a failed call reports only the
error's class or the HTTP status. The Pod's log level stays at INFO or above,
because `urllib3`'s debug lines carry the URL's path.

**The variable is not set anywhere yet.** The staging values that give
`HEARTBEAT_URL` to the code server, and the deletion of the CronJob that the
alarm replaces, arrive in a later change that pins the image built from this
one. Until then an image with `alarm.py` must not be pinned in any environment.
Run Pods import this same module, so they too refuse to load without the
variable; ADR-0033 keeps it out of them, and how to reconcile the two is
settled in that change.

**Configuration** comes from the environment, set on the code server and on
every run Pod:

| Variable | For |
|---|---|
| `DESTINATION__DUCKLAKE__CREDENTIALS__*` | dlt's DuckLake destination: catalog, storage, R2 keys, metadata schema |
| `LAGOM_LAKE_CATALOG`, `LAGOM_LAKE_DATA_PATH`, `LAGOM_LAKE_METADATA_SCHEMA` | the same lake, for dbt and maintenance |
| `LAGOM_R2_ACCESS_KEY_ID`, `LAGOM_R2_SECRET_ACCESS_KEY`, `LAGOM_R2_ENDPOINT` | the environment's R2 token |
| `HEARTBEAT_URL` | the check's ping URL (a secret): required by the code location; ADR-0033 wants it on the code server only |

**Run it locally** against a DuckLake on disk, with no R2 and no secret. dlt
honours a metadata schema only for a SQL catalog, so a local file uses
`main`:

```bash
cd platform/orchestration && T=$(mktemp -d) && export PYTHONPATH=$PWD/../..:. HOME=$T DAGSTER_HOME=$T DLT_DATA_DIR=$T/dlt DBT_LOG_PATH=$T/dbt-logs \
  DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG="duckdb:///$T/catalog.duckdb" DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__BUCKET_URL="$T/data" \
  DESTINATION__DUCKLAKE__CREDENTIALS__DUCKLAKE_NAME=lagom DESTINATION__DUCKLAKE__CREDENTIALS__METADATA_SCHEMA=main \
  LAGOM_LAKE_CATALOG="ducklake:$T/catalog.duckdb" LAGOM_LAKE_DATA_PATH="$T/data/" LAGOM_LAKE_METADATA_SCHEMA=main \
  LAGOM_R2_ACCESS_KEY_ID=unused LAGOM_R2_SECRET_ACCESS_KEY=unused LAGOM_R2_ENDPOINT=https://unused.invalid
uv run dbt parse --project-dir ../warehouse --profiles-dir ../warehouse --target lake --quiet
uv run dagster job execute -m lagom_orchestration.definitions -j usgs
rm -rf "$T"
```

The command above needs `HEARTBEAT_URL` too, because the code location does
not load without it: add `HEARTBEAT_URL=https://hc-ping.invalid/local` to the
`export` line. The `.invalid` host never resolves, so nothing is pinged; and
the sensors only run under the daemon, which this command does not start.

On the operator's Mac, 2026-10-28: 9,878 events loaded, dbt `PASS=33`, in 35 s,
with a peak of 356 MiB for the whole process.

**DuckDB is pinned below 2.0.** DuckDB 2.0 removes `Relation.fetchone()`,
which dlt 1.31 calls.
