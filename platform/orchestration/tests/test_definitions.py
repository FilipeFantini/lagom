"""The code location loads, and its graph is the one the ADR describes."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from dagster import AssetKey, DefaultSensorStatus

WAREHOUSE = Path(__file__).resolve().parents[2] / "warehouse"
# `dbt parse` reads the profile but opens no connection: placeholders suffice.
PARSE_ENV = {
    "LAGOM_LAKE_CATALOG": "ducklake:parse.ducklake",
    "LAGOM_LAKE_DATA_PATH": "parse/",
    "LAGOM_LAKE_METADATA_SCHEMA": "main",
    "LAGOM_R2_ACCESS_KEY_ID": "parse",
    "LAGOM_R2_SECRET_ACCESS_KEY": "parse",
    "LAGOM_R2_ENDPOINT": "https://parse.invalid",
}


@pytest.fixture(scope="module")
def defs(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    logs = tmp_path_factory.mktemp("dbt-logs")
    subprocess.run(
        [
            "dbt",
            "parse",
            "--project-dir",
            str(WAREHOUSE),
            "--profiles-dir",
            str(WAREHOUSE),
            "--target",
            "lake",
            "--log-path",
            str(logs),
            "--quiet",
        ],
        check=True,
        env={**os.environ, **PARSE_ENV},
    )
    # The code location refuses to load without the alarm's ping URL (ADR-0033);
    # the sensors capture it when the module is imported.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HEARTBEAT_URL", "https://hc-ping.invalid/test")
        from lagom_orchestration.definitions import defs

    return defs


def test_silver_reads_the_dlt_asset(defs) -> None:  # type: ignore[no-untyped-def]
    graph = defs.resolve_asset_graph()
    silver = AssetKey(["silver", "stg_usgs__events"])
    assert AssetKey(["usgs", "events"]) in graph.get(silver).parent_keys


def test_jobs_and_schedules(defs) -> None:  # type: ignore[no-untyped-def]
    assert {j.name for j in defs.resolve_all_job_defs()} >= {"usgs", "lake_maintenance"}
    crons = {s.job_name: s.cron_schedule for s in defs.schedules}  # type: ignore[union-attr]
    assert crons == {"usgs": "*/30 * * * *", "lake_maintenance": "15 4 * * *"}


def test_the_alarm_sensors_are_registered_and_running(defs) -> None:  # type: ignore[no-untyped-def]
    assert {s.name for s in defs.sensors} == {"usgs_success_heartbeat", "run_failure_alarm"}
    assert all(s.default_status == DefaultSensorStatus.RUNNING for s in defs.sensors)


def test_the_code_location_does_not_load_without_the_ping_url(defs) -> None:  # type: ignore[no-untyped-def]
    """The real load path: a code server started without the variable stops.

    `defs` is requested so the dbt manifest the module reads already exists.
    """
    env = {k: v for k, v in os.environ.items() if k != "HEARTBEAT_URL"}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    done = subprocess.run(
        [sys.executable, "-c", "import lagom_orchestration.definitions"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0
    assert "HEARTBEAT_URL is not set or is empty" in done.stderr


def test_steps_run_in_the_runs_process(defs) -> None:  # type: ignore[no-untyped-def]
    for job in defs.resolve_all_job_defs():
        if job.name in {"usgs", "lake_maintenance"}:
            assert job.executor_def.name == "in_process", job.name


def test_run_resources_override_the_code_servers(defs) -> None:  # type: ignore[no-untyped-def]
    """A run's tag wins over the code location's resources, key by key.

    The code location passes its own resources to every run; this is the
    merge dagster-k8s applies on top of them when it builds the run's Pod.
    """
    from dagster_k8s.container_context import K8sContainerContext
    from dagster_k8s.job import get_user_defined_k8s_config

    from lagom_orchestration.definitions import RUN_RESOURCES

    code_location = K8sContainerContext(
        resources={"requests": {"cpu": "50m", "memory": "320Mi"}, "limits": {"memory": "512Mi"}}
    )
    for job in defs.resolve_all_job_defs():
        if job.name not in {"usgs", "lake_maintenance"}:
            continue
        assert job.tags["lagom/pipeline"] == "usgs"
        run = code_location.merge(
            K8sContainerContext(run_k8s_config=get_user_defined_k8s_config(job.tags))
        )
        assert run.run_k8s_config.container_config["resources"] == RUN_RESOURCES, job.name


def test_budget_declares_the_runs_request(defs) -> None:  # type: ignore[no-untyped-def]
    """The budget check counts a run by its Application's annotation.

    Each environment's Application pins an image, and the request is in the
    image, so the annotation must match the request of the code it runs:
    this compares it with the code in this checkout.
    """
    import yaml

    from lagom_orchestration.definitions import RUN_RESOURCES

    apps = sorted((Path(__file__).resolve().parents[2] / "infra" / "apps").glob("dagster-*.yaml"))
    assert apps
    for app in apps:
        annotations = yaml.safe_load(app.read_text())["metadata"]["annotations"]
        declared = annotations["lagom.dev/ephemeral-memory-request"]
        assert declared == RUN_RESOURCES["requests"]["memory"], app.name
