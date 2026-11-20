"""The platform's Dagster code location (ADR-0026).

Every source's assets plus the dbt project, whose models become assets
downstream of the sources they read. One job loads and builds, every 30
minutes; another keeps DuckLake compact, daily; a third renders the
dashboards and publishes them, with dbt's documentation, after each successful
pipeline run (lagom_orchestration/dashboards.py). Two sensors raise the alarm through
Healthchecks.io (lagom_orchestration/alarm.py).

Paths are relative to the repository layout, which the image keeps under /app.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    OpExecutionContext,
    ScheduleDefinition,
    asset,
    define_asset_job,
    in_process_executor,
    job,
    op,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets
from data.sources.usgs.assets import usgs_events

from lagom_orchestration.alarm import build_alarm_sensors
from lagom_orchestration.dashboards import (
    build_render_trigger,
    memory_peak_mib,
    run_docs,
    run_reports,
)
from lagom_orchestration.lake import attach_lake, grant_reader_access, run_maintenance

REPO = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO / "data" / "warehouse"
# The image's root filesystem is read-only: dbt writes only under /tmp.
DBT_TARGET_PATH = Path("/tmp/dbt-target")

warehouse_project = DbtProject(project_dir=WAREHOUSE, profiles_dir=WAREHOUSE, target="lake")


@dbt_assets(manifest=warehouse_project.manifest_path, project=warehouse_project)
def warehouse(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    """`dbt build`: silver and gold with their tests and reconciliations."""
    yield from dbt.cli(["build"], context=context, target_path=DBT_TARGET_PATH).stream()


# Two runs of this job must never overlap: both would read dlt's cursor and
# write it back. The tag is limited to one run at a time in the instance's
# run queue (infra/dagster-<env>/values.yaml).
PIPELINE_TAG = {"lagom/pipeline": "usgs"}

# The memory of each run's Pod. Without this, a run inherits the code server's
# resources, and a run needs more than twice what the code server does
# (docs/platform.md, Resource budget). Dagster applies a run's tags over the code
# location's configuration: a resource figure replaces the code server's, a
# list (the environment) is added to it. The request is also declared on
# each environment's Application (lagom.dev/ephemeral-memory-request) for the
# budget check; tests/test_definitions.py fails if they differ.
RUN_RESOURCES = {"requests": {"cpu": "50m", "memory": "768Mi"}, "limits": {"memory": "1Gi"}}


def _secret_env(name: str, secret: str, key: str) -> dict[str, Any]:
    return {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key}}}


# The credentials of the jobs that write the lake (ADR-0044): the catalog's
# owner, the R2 write token, and the variables dlt and dbt build from them.
# They are here, not in the code server's environment, so a job that needs
# fewer gets fewer. Secrets are per namespace: the same name resolves to
# staging's in staging and prod's in prod, which is how one image serves both.
# What is not secret and differs between environments (the bucket) stays in
# each environment's values.yaml. Kubernetes expands $(NAME) only from
# variables defined earlier in the same list, and dagster-k8s puts these after
# the code server's, so every reference below is to a variable above it.
WRITER_ENV = [
    # The catalog's owner, from the Secret CloudNativePG generates.
    _secret_env("PG_URI", "postgres-app", "uri"),
    _secret_env("LAGOM_R2_ACCESS_KEY_ID", "r2-lake", "ACCESS_KEY_ID"),
    _secret_env("LAGOM_R2_SECRET_ACCESS_KEY", "r2-lake", "ACCESS_SECRET_KEY"),
    _secret_env("LAGOM_R2_ENDPOINT", "r2-lake", "ENDPOINT"),
    # dlt's DuckLake destination.
    {"name": "DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG", "value": "$(PG_URI)"},
    {
        "name": "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__AWS_ACCESS_KEY_ID",
        "value": "$(LAGOM_R2_ACCESS_KEY_ID)",
    },
    {
        "name": "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__AWS_SECRET_ACCESS_KEY",
        "value": "$(LAGOM_R2_SECRET_ACCESS_KEY)",
    },
    {
        "name": "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__ENDPOINT_URL",
        "value": "$(LAGOM_R2_ENDPOINT)",
    },
    # The same lake for dbt's `lake` target and the maintenance job.
    {"name": "LAGOM_LAKE_CATALOG", "value": "ducklake:postgres:$(PG_URI)?sslmode=require"},
]
RUN_TAGS = {
    **PIPELINE_TAG,
    "dagster-k8s/config": {"container_config": {"resources": RUN_RESOURCES, "env": WRITER_ENV}},
}

usgs_job = define_asset_job(
    "usgs", selection=AssetSelection.groups("usgs").downstream(), tags=RUN_TAGS
)


# What the dashboards' render holds (ADR-0044, decisions 7 to 9): read-only
# credentials for the lake, and a write token for the dashboards bucket alone.
# No owner's connection string, no write token for the lake, nothing from
# `postgres-app` or `r2-lake`: tests/test_definitions.py reads the Pod's
# environment and fails if one appears. Same variable names as the writers',
# so `attach_lake` and the dbt `lake` target read them unchanged. The catalog
# string has no password: libpq reads PGPASSWORD, so a failed attach cannot
# repeat it. `postgres-rw` is CloudNativePG's read-write Service of the
# cluster `postgres`, in the Pod's own namespace (the Dagster values name it
# the same way). The dashboards bucket's name is not secret and differs by
# environment, so it is LAGOM_DASHBOARDS_BUCKET in each environment's values.
READER_ENV = [
    _secret_env("PGPASSWORD", "lake-reader-credentials", "password"),
    {
        "name": "LAGOM_LAKE_CATALOG",
        "value": (
            "ducklake:postgres:host=postgres-rw port=5432 dbname=lagom "
            "user=lake_reader sslmode=require"
        ),
    },
    _secret_env("LAGOM_R2_ACCESS_KEY_ID", "r2-lake-reader", "ACCESS_KEY_ID"),
    _secret_env("LAGOM_R2_SECRET_ACCESS_KEY", "r2-lake-reader", "ACCESS_SECRET_KEY"),
    _secret_env("LAGOM_R2_ENDPOINT", "r2-lake-reader", "ENDPOINT"),
    _secret_env("LAGOM_DASHBOARDS_ACCESS_KEY_ID", "r2-dashboards", "ACCESS_KEY_ID"),
    _secret_env("LAGOM_DASHBOARDS_SECRET_ACCESS_KEY", "r2-dashboards", "ACCESS_SECRET_KEY"),
    _secret_env("LAGOM_DASHBOARDS_ENDPOINT", "r2-dashboards", "ENDPOINT"),
]
# The same tag as the pipeline's jobs: the run queue then never runs the
# render beside `usgs`, so the node's budget (one run's memory) is unchanged.
DASHBOARDS_TAGS = {
    **PIPELINE_TAG,
    "dagster-k8s/config": {"container_config": {"resources": RUN_RESOURCES, "env": READER_ENV}},
}


def _log_memory_peak(context: AssetExecutionContext) -> None:
    """The container's peak so far: after each step, so each one's cost is visible."""
    peak = memory_peak_mib()
    context.log.info(
        "memory peak of the run's container: "
        + (f"{peak:.0f} MiB" if peak is not None else "not reported by the kernel")
    )


# No `deps` on gold: an asset downstream of `usgs/events` would be selected by
# the `usgs` job (`.downstream()` above) and run with the writers' credentials.
# The key is `dashboards/pages` because an asset and a job cannot share a name
# in one repository, and the job is `dashboards`.
@asset(key_prefix="dashboards", group_name="dashboards")
def pages(context: AssetExecutionContext) -> None:
    """The boards, rendered against the lake, checked, and published to the bucket."""
    published = run_reports(REPO)
    context.log.info(f"published {len(published)} page(s): {', '.join(published)}")
    _log_memory_peak(context)


# After `pages`, so a failure here leaves the reports current: they upload first.
# The same tags and credentials as the reports (ADR-0056).
@asset(key_prefix="dashboards", group_name="dashboards", deps=[pages])
def docs(context: AssetExecutionContext) -> None:
    """dbt's documentation, generated against the lake, checked, and published as one page."""
    context.log.info(f"published {run_docs(REPO)}")
    _log_memory_peak(context)


dashboards_job = define_asset_job(
    "dashboards", selection=AssetSelection.groups("dashboards"), tags=DASHBOARDS_TAGS
)


@op
def compact_lake(context: OpExecutionContext) -> None:
    connection = attach_lake()
    try:
        run_maintenance(connection)
    finally:
        connection.close()
    context.log.info("DuckLake merged, expired and cleaned")


@op
def grant_lake_reader(context: OpExecutionContext) -> None:
    """The read-only role can read every table DuckLake has made so far."""
    grant_reader_access()
    context.log.info("catalog read access granted to the read-only role")


# The two ops do not depend on each other: a role that is missing fails the
# grant, not the compaction.
@job(tags=RUN_TAGS)
def lake_maintenance() -> None:
    compact_lake()
    grant_lake_reader()


defs = Definitions(
    assets=[usgs_events, warehouse, pages, docs],
    # Every step in the run's own process. The default executor starts each
    # step in a new Python process beside the run's, which holds Dagster, dlt
    # and dbt's imports a second time. The steps run one after the other
    # anyway, so nothing is lost. dbt still runs as its own process.
    executor=in_process_executor,
    jobs=[
        usgs_job,
        lake_maintenance,
        dashboards_job,
    ],
    schedules=[
        # Every 30 minutes, as the CronJob: USGS publishes ~9 events an hour.
        ScheduleDefinition(
            job=usgs_job,
            cron_schedule="*/30 * * * *",
            execution_timezone="UTC",
            default_status=DefaultScheduleStatus.RUNNING,
        ),
        # Once a day, away from the half hours.
        ScheduleDefinition(
            job=lake_maintenance,
            cron_schedule="15 4 * * *",
            execution_timezone="UTC",
            default_status=DefaultScheduleStatus.RUNNING,
        ),
    ],
    # Reads HEARTBEAT_URL now: without it the code location does not load
    # (ADR-0033). It is the one variable the code location needs to load, so
    # it stays in the code server's environment, which every run inherits:
    # run Pods import this module too.
    sensors=[*build_alarm_sensors(usgs_job), build_render_trigger(usgs_job, dashboards_job)],
    resources={"dbt": DbtCliResource(project_dir=warehouse_project)},
)
