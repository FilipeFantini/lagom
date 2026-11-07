"""The platform's Dagster code location (ADR-0026).

Every service's assets plus the dbt project, whose models become assets
downstream of the sources they read. One job loads and builds, every 30
minutes; another keeps DuckLake compact, daily. Two sensors raise the alarm
through Healthchecks.io (lagom_orchestration/alarm.py).

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
    define_asset_job,
    in_process_executor,
    job,
    op,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets
from services.usgs.assets import usgs_events

from lagom_orchestration.alarm import build_alarm_sensors
from lagom_orchestration.lake import attach_lake, run_maintenance

REPO = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO / "platform" / "warehouse"
# The image's root filesystem is read-only: dbt writes only under /tmp.
DBT_TARGET_PATH = Path("/tmp/dbt-target")

warehouse_project = DbtProject(project_dir=WAREHOUSE, profiles_dir=WAREHOUSE, target="lake")


@dbt_assets(manifest=warehouse_project.manifest_path, project=warehouse_project)
def warehouse(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    """`dbt build`: silver and gold with their tests and reconciliations."""
    yield from dbt.cli(["build"], context=context, target_path=DBT_TARGET_PATH).stream()


# Two runs of this job must never overlap: both would read dlt's cursor and
# write it back. The tag is limited to one run at a time in the instance's
# run queue (platform/infra/dagster-<env>/values.yaml).
PIPELINE_TAG = {"lagom/pipeline": "usgs"}

# The memory of each run's Pod. Without this, a run inherits the code server's
# resources, and a run needs more than twice what the code server does
# (architecture.md section 11). Dagster applies a run's tags over the code
# location's configuration, so this wins. The request is also declared on
# each environment's Application (lagom.dev/ephemeral-memory-request) for the
# budget check; tests/test_definitions.py fails if they differ.
RUN_RESOURCES = {"requests": {"cpu": "50m", "memory": "768Mi"}, "limits": {"memory": "1Gi"}}
RUN_TAGS = {
    **PIPELINE_TAG,
    "dagster-k8s/config": {"container_config": {"resources": RUN_RESOURCES}},
}

usgs_job = define_asset_job(
    "usgs", selection=AssetSelection.groups("usgs").downstream(), tags=RUN_TAGS
)


@op
def compact_lake(context: OpExecutionContext) -> None:
    connection = attach_lake()
    try:
        run_maintenance(connection)
    finally:
        connection.close()
    context.log.info("DuckLake merged, expired and cleaned")


@job(tags=RUN_TAGS)
def lake_maintenance() -> None:
    compact_lake()


defs = Definitions(
    assets=[usgs_events, warehouse],
    # Every step in the run's own process. The default executor starts each
    # step in a new Python process beside the run's, which holds Dagster, dlt
    # and dbt's imports a second time. The steps run one after the other
    # anyway, so nothing is lost. dbt still runs as its own process.
    executor=in_process_executor,
    jobs=[usgs_job, lake_maintenance],
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
    # (ADR-0033).
    sensors=build_alarm_sensors(usgs_job),
    resources={"dbt": DbtCliResource(project_dir=warehouse_project)},
)
