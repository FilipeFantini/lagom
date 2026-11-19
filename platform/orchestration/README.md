# platform/orchestration/

The platform's Dagster code location ([ADR-0026](../../docs/adr/0026-dagster-one-instance-per-environment.md)).
One image, `lagom-orchestration`, runs both the code server and every run Pod.

| Path | What it is |
|---|---|
| `lagom_orchestration/definitions.py` | The `Definitions`: each service's assets, the dbt project as assets, the jobs and their schedules |
| `lagom_orchestration/alarm.py` | The two sensors that raise the alarm through Healthchecks.io ([ADR-0033](../../docs/adr/0033-dagster-alerts-through-healthchecks.md)) |
| `lagom_orchestration/dashboards.py` | The `dashboards` job's step (render, check, watermark, upload to R2) and the sensor that starts it after each `usgs` run ([ADR-0044](../../docs/adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)) |
| `lagom_orchestration/lake.py` | Attaching the environment's DuckLake (read-only on request), its daily maintenance, and the grants of the catalog's read-only role |
| `Dockerfile` | Built from the repository root: it bundles `services/*/ingestion`, `services/*/assets`, `platform/warehouse` and the dashboards' boards (`products/dashboards`), and installs dbt Charts |
| `tests/` | The code location loads, and its graph, schedules and alarm sensors are the intended ones |

**Jobs.**

- `usgs`, every 30 minutes: `usgs/events` (dlt into DuckLake), then `dbt build`
  on the warehouse's `lake` target.
- `lake_maintenance`, daily at 04:15 UTC: merge small files, expire snapshots
  older than 7 days, and delete the files nothing references; and, as a step of
  its own, grant the catalog's read-only role `lake_reader` `SELECT` on the
  DuckLake schema. The step fails if the role does not exist
  ([ADR-0044](../../docs/adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)).

- `dashboards`, after each successful `usgs` run (the sensor
  `render_dashboards_after_usgs`): the asset `dashboards/pages` renders the
  boards of `products/dashboards` against the lake **read-only**, checks them,
  stamps each page `Page built at <UTC time>`, and uploads the pages to the
  environment's dashboards bucket (R2, public, on a custom domain). It holds
  `lake_reader`'s password, a read-only R2 token and the bucket's token
  (`READER_ENV`), and none of the owner's or the lake's write token. In order:
  `dct validate --strict --warehouse`, `dct render`, `check_boards.py`, the
  watermark check, the upload, then the removal of pages no longer boards.
  Every command has a timeout, and `dct` runs with the lake's variables only.
  A failed run is covered by `run_failure_alarm`.

The three jobs carry the tag `lagom/pipeline: usgs`, which the run queue limits to one
run at a time (so the render waits for the pipeline, and the budget does not change), and a `dagster-k8s/config` tag with the memory of the run's Pod
(`RUN_RESOURCES`) and the Secrets it gets (`WRITER_ENV`, below). Without it a run would inherit the code server's, which is
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

**Where it is set:** each environment's values give the code server
`HEARTBEAT_URL` from the Secret `pipeline-heartbeat`, in the change that
retired that environment's CronJob (both on 2026-11-07). An image with `alarm.py` is never pinned in an environment whose
values do not set it.
Run Pods import this same module, so they too refuse to load without the
variable. They receive it with the rest of the code server's environment, as
ADR-0033 decision 5 records.

**Configuration** comes from the environment. The code server's, which every
run Pod inherits, holds what is not secret and differs by environment, plus the
ping URL. The credentials are not there: each job names the Secrets it needs in
its own tag, and a run's Pod gets the two lists together
([ADR-0044](../../docs/adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)).

| Variable | Set by | For |
|---|---|---|
| `DESTINATION__DUCKLAKE__CREDENTIALS__*` (`DUCKLAKE_NAME`, `METADATA_SCHEMA`, `STORAGE__BUCKET_URL`, `REGION_NAME`) | the environment's values | dlt's DuckLake destination: what is not secret |
| `LAGOM_LAKE_DATA_PATH`, `LAGOM_LAKE_METADATA_SCHEMA`, `PGSSLMODE` | the environment's values | the lake's bucket and schema, for dbt and maintenance |
| `HEARTBEAT_URL` | the environment's values, from the Secret `pipeline-heartbeat` | the check's ping URL (a secret): required by the code location, so every run Pod holds it (ADR-0033 decision 5, still true) |
| `LAGOM_DASHBOARDS_BUCKET` | the environment's values | the bucket the pages are published to (`lagom-<env>-dashboards`) |
| `PG_URI` | each job's tag (`WRITER_ENV`), from `postgres-app` | the catalog owner's connection string |
| `LAGOM_R2_ACCESS_KEY_ID`, `LAGOM_R2_SECRET_ACCESS_KEY`, `LAGOM_R2_ENDPOINT` | each job's tag, from `r2-lake` | the environment's R2 write token |
| `DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG` and `…__STORAGE__CREDENTIALS__*`, `LAGOM_LAKE_CATALOG` | each job's tag | composed from the Secrets above with `$(NAME)`, so they sit below them in the tag |

| `PGPASSWORD` | the `dashboards` job's tag (`READER_ENV`), from `lake-reader-credentials` | the read-only role's password, read by libpq: the catalog string has none |
| `LAGOM_LAKE_CATALOG` | the `dashboards` job's tag | the catalog as `lake_reader`, host `postgres-rw`, no password |
| `LAGOM_R2_*` | the `dashboards` job's tag, from `r2-lake-reader` | a read-only R2 token on the lake (same names as the writers') |
| `LAGOM_DASHBOARDS_ACCESS_KEY_ID`, `…_SECRET_ACCESS_KEY`, `…_ENDPOINT` | the `dashboards` job's tag, from `r2-dashboards` | a token for the dashboards bucket only; `dct` never sees it |

A run that belongs to no job of ours (the implicit asset job, from the asset
catalog) has no tag and so no credentials. `attach_lake(read_only=True)` is for
a reader: its catalog string may leave the password out and let libpq read
`PGPASSWORD`.

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

**The `dashboards` dependency group** holds `dbt-charts` (`dct`), the renderer
of [`products/dashboards/`](../../products/dashboards/README.md)
([ADR-0040](../../docs/adr/0040-dbt-charts-for-the-dashboards.md)). It is not in
`dependencies`, so a test run does not install it; the image asks for it
(`uv sync --locked --no-dev --group dashboards`) because the `dashboards` job
renders in it, and CI and a local render ask with `--group dashboards`. It
added 18 packages to the lock and changed none; DuckDB stays at 1.5.6. The
image grew by about 237 MiB unpacked (1,376 to 1,612 MiB, measured locally on
2026-11-14). `botocore`, for the upload, is declared in `dependencies`: dlt's
s3 extra already brought it, so it adds nothing.
