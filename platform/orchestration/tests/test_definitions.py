"""The code location loads, and its graph is the one the ADR describes."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from dagster import AssetKey, DagsterRun, DefaultSensorStatus

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
    assert {j.name for j in defs.resolve_all_job_defs()} >= {
        "usgs",
        "lake_maintenance",
        "dashboards",
    }
    crons = {s.job_name: s.cron_schedule for s in defs.schedules}  # type: ignore[union-attr]
    assert crons == {"usgs": "*/30 * * * *", "lake_maintenance": "15 4 * * *"}


def test_the_alarm_sensors_are_registered_and_running(defs) -> None:  # type: ignore[no-untyped-def]
    assert {s.name for s in defs.sensors} == {
        "usgs_success_heartbeat",
        "run_failure_alarm",
        "render_dashboards_after_usgs",
    }
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


def test_the_code_location_loads_with_only_the_ping_url(defs, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """What the code server and an untagged run hold is enough to import the module.

    The credentials are not in the code server's environment (ADR-0044), so the
    module must not read one when it loads. The process gets the ping URL and
    nothing else of the platform's: no catalog, no R2 key, no dlt variable.
    """
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(sys.path),
        "HEARTBEAT_URL": "https://hc-ping.invalid/test",
    }
    done = subprocess.run(
        [sys.executable, "-c", "import lagom_orchestration.definitions"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_steps_run_in_the_runs_process(defs) -> None:  # type: ignore[no-untyped-def]
    for job in defs.resolve_all_job_defs():
        if job.name in {"usgs", "lake_maintenance", "dashboards"}:
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
        if job.name not in {"usgs", "lake_maintenance", "dashboards"}:
            continue
        assert job.tags["lagom/pipeline"] == "usgs"
        run = code_location.merge(
            K8sContainerContext(run_k8s_config=get_user_defined_k8s_config(job.tags))
        )
        assert run.run_k8s_config.container_config["resources"] == RUN_RESOURCES, job.name


def _run_pod_env(job, code_location_env):  # type: ignore[no-untyped-def]
    """The environment of the container a run of `job` gets, as the daemon builds it.

    The code location's environment travels as the run's container context
    (the chart's `includeConfigInLaunchedRuns`); the job's tags are merged over
    it by the launcher's own `get_container_context_for_run`, and the Job
    comes out of `construct_dagster_k8s_job`, the function the launcher calls.
    Only the launcher's start-up is replaced: it would open a kubeconfig.
    """
    from unittest.mock import MagicMock

    from dagster._core.code_pointer import ModuleCodePointer
    from dagster._core.origin import JobPythonOrigin, RepositoryPythonOrigin
    from dagster_k8s import K8sRunLauncher
    from dagster_k8s.job import construct_dagster_k8s_job

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("dagster_k8s.launcher.load_kubernetes_config", MagicMock())
        launcher = K8sRunLauncher(
            dagster_home="/opt/dagster/dagster_home",
            instance_config_map="dagster-instance",
            postgres_password_secret="dagster-db",
            service_account_name="dagster",
            job_image="example.invalid/lagom-orchestration:test",
            job_namespace="staging",
            k8s_client_batch_api=MagicMock(),
            k8s_client_core_api=MagicMock(),
        )
    repository = RepositoryPythonOrigin(
        executable_path="python",
        code_pointer=ModuleCodePointer("lagom_orchestration.definitions", "defs"),
        container_image="example.invalid/lagom-orchestration:test",
        container_context={"k8s": {"env": code_location_env, "namespace": "staging"}},
    )
    run = DagsterRun(
        job_name=job.name,
        tags=job.tags,
        job_code_origin=JobPythonOrigin(job.name, repository),
    )
    context = launcher.get_container_context_for_run(run)
    kubernetes_job = construct_dagster_k8s_job(
        job_config=context.get_k8s_job_config(repository.container_image, launcher),
        args=["dagster", "api", "execute_run"],
        job_name="dagster-run-test",
        user_defined_k8s_config=context.run_k8s_config,
    )
    return list(kubernetes_job.spec.template.spec.containers[0].env)


# What the code server holds: what it needs to load, which a run's process needs too.
LOADING_ENV = [
    {
        "name": "HEARTBEAT_URL",
        "valueFrom": {"secretKeyRef": {"name": "pipeline-heartbeat", "key": "url"}},
    }
]
WRITER_SECRETS = {
    "PG_URI": ("postgres-app", "uri"),
    "LAGOM_R2_ACCESS_KEY_ID": ("r2-lake", "ACCESS_KEY_ID"),
    "LAGOM_R2_SECRET_ACCESS_KEY": ("r2-lake", "ACCESS_SECRET_KEY"),
    "LAGOM_R2_ENDPOINT": ("r2-lake", "ENDPOINT"),
}


def test_the_writing_jobs_pods_get_the_owner_and_write_credentials(defs) -> None:  # type: ignore[no-untyped-def]
    """The tag adds to the code server's environment; it does not replace it."""
    for job in defs.resolve_all_job_defs():
        if job.name not in {"usgs", "lake_maintenance"}:
            continue
        env = {e.name: e for e in _run_pod_env(job, LOADING_ENV)}
        for name, (secret, key) in WRITER_SECRETS.items():
            ref = env[name].value_from.secret_key_ref
            assert (ref.name, ref.key) == (secret, key), (job.name, name)
        # The code server's own variable is still there, and the composed
        # variables follow the Secrets they read: Kubernetes expands $(NAME)
        # only from variables defined above it.
        assert env["HEARTBEAT_URL"].value_from.secret_key_ref.name == "pipeline-heartbeat"
        names = list(env)
        for name, value in {k: v.value for k, v in env.items() if v.value}.items():
            for reference in re.findall(r"\$\((\w+)\)", value):
                assert names.index(reference) < names.index(name), (job.name, name)


def test_a_code_server_that_still_holds_the_credentials_adds_no_duplicates(defs) -> None:  # type: ignore[no-untyped-def]
    """The rollout's first step (runbook): the image has the tags, the values still the variables.

    The two lists name the same variables with the same values; the merge must
    keep one of each, or the Pod's environment would define a name twice.
    """
    from lagom_orchestration.definitions import WRITER_ENV

    for job in defs.resolve_all_job_defs():
        if job.name not in {"usgs", "lake_maintenance"}:
            continue
        names = [e.name for e in _run_pod_env(job, [*WRITER_ENV, *LOADING_ENV])]
        assert len(names) == len(set(names)), job.name


def test_a_pod_built_from_the_code_servers_environment_alone_has_no_credentials(defs) -> None:  # type: ignore[no-untyped-def]
    """An implicit asset job has no tags: its Pod holds only the code server's variables."""
    from dagster import define_asset_job

    untagged = define_asset_job("untagged", selection="*").resolve(
        asset_graph=defs.resolve_asset_graph()
    )
    env = {e.name: e for e in _run_pod_env(untagged, LOADING_ENV)}
    assert not set(WRITER_SECRETS) & set(env)
    assert not any(name.startswith("DESTINATION__") for name in env)
    assert "LAGOM_LAKE_CATALOG" not in env


INFRA = Path(__file__).resolve().parents[2] / "infra"
# Every variable a pipeline Pod had before the credentials moved to the jobs.
PIPELINE_ENV_NAMES = {
    "PG_URI",
    "PGSSLMODE",
    "LAGOM_R2_ACCESS_KEY_ID",
    "LAGOM_R2_SECRET_ACCESS_KEY",
    "LAGOM_R2_ENDPOINT",
    "DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG",
    "DESTINATION__DUCKLAKE__CREDENTIALS__DUCKLAKE_NAME",
    "DESTINATION__DUCKLAKE__CREDENTIALS__METADATA_SCHEMA",
    "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__BUCKET_URL",
    "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__AWS_ACCESS_KEY_ID",
    "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__AWS_SECRET_ACCESS_KEY",
    "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__ENDPOINT_URL",
    "DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__CREDENTIALS__REGION_NAME",
    "LAGOM_LAKE_CATALOG",
    "LAGOM_LAKE_DATA_PATH",
    "LAGOM_LAKE_METADATA_SCHEMA",
    "HEARTBEAT_URL",
}


def _code_server_env(environment: str):  # type: ignore[no-untyped-def]
    import yaml

    values = yaml.safe_load((INFRA / f"dagster-{environment}" / "values.yaml").read_text())
    deployment = values["dagster-user-deployments"]["deployments"][0]
    assert deployment["includeConfigInLaunchedRuns"]["enabled"] is True
    return deployment["env"]


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_the_code_server_holds_no_credential_but_the_ping_url(defs, environment) -> None:  # type: ignore[no-untyped-def]
    """The real values of each environment, merged with the real tags (ADR-0044)."""
    code_server = _code_server_env(environment)
    secrets = {
        e["name"]: e["valueFrom"]["secretKeyRef"]["name"] for e in code_server if "valueFrom" in e
    }
    assert secrets == {"HEARTBEAT_URL": "pipeline-heartbeat"}
    assert not any("$(" in e.get("value", "") for e in code_server)

    # A Pod from the code server's environment alone: no owner, no R2 token.
    only_the_code_server = [e["name"] for e in code_server]
    assert not {"PG_URI", "LAGOM_LAKE_CATALOG"} & set(only_the_code_server)
    assert not any("R2" in name or "AWS" in name for name in only_the_code_server)
    from dagster import define_asset_job

    untagged = define_asset_job("untagged", selection="*").resolve(
        asset_graph=defs.resolve_asset_graph()
    )
    alone = {e.name for e in _run_pod_env(untagged, code_server)}
    assert not alone & {"PG_URI", "LAGOM_R2_ACCESS_KEY_ID", "LAGOM_R2_SECRET_ACCESS_KEY"}
    assert not any(name.endswith(("CATALOG", "AWS_SECRET_ACCESS_KEY")) for name in alone)

    bucket = f"lagom-{environment}-lake"
    for job in defs.resolve_all_job_defs():
        if job.name not in {"usgs", "lake_maintenance"}:
            continue
        pod = _run_pod_env(job, code_server)
        names = [e.name for e in pod]
        assert len(names) == len(set(names)), job.name
        assert PIPELINE_ENV_NAMES <= set(names), (job.name, PIPELINE_ENV_NAMES - set(names))
        values = {e.name: e.value for e in pod}
        assert values["DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__BUCKET_URL"] == f"s3://{bucket}"
        assert values["LAGOM_LAKE_DATA_PATH"] == f"s3://{bucket}/"
        # Every $(NAME) reads a variable defined above it, across both lists.
        for index, (name, value) in enumerate(values.items()):
            for reference in re.findall(r"\$\((\w+)\)", value or ""):
                assert reference in names[:index], (job.name, name, reference)


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


# What the dashboards job's Pod may hold (ADR-0044, decisions 7 to 9), beyond
# what every run Pod has (the ping URL, Dagster's own database password): the
# read-only role's password, a read-only R2 token on the lake, and a token
# scoped to the dashboards bucket.
READER_SECRETS = {
    "PGPASSWORD": ("lake-reader-credentials", "password"),
    "LAGOM_R2_ACCESS_KEY_ID": ("r2-lake-reader", "ACCESS_KEY_ID"),
    "LAGOM_R2_SECRET_ACCESS_KEY": ("r2-lake-reader", "ACCESS_SECRET_KEY"),
    "LAGOM_R2_ENDPOINT": ("r2-lake-reader", "ENDPOINT"),
    "LAGOM_DASHBOARDS_ACCESS_KEY_ID": ("r2-dashboards", "ACCESS_KEY_ID"),
    "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY": ("r2-dashboards", "ACCESS_SECRET_KEY"),
    "LAGOM_DASHBOARDS_ENDPOINT": ("r2-dashboards", "ENDPOINT"),
}


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_the_dashboards_pod_holds_the_readers_credentials_and_no_writers(  # type: ignore[no-untyped-def]
    defs, environment
) -> None:
    """The real values of each environment, merged with the dashboards job's real tag."""
    job = next(j for j in defs.resolve_all_job_defs() if j.name == "dashboards")
    pod = _run_pod_env(job, _code_server_env(environment))
    names = [e.name for e in pod]
    assert len(names) == len(set(names))
    env = {e.name: e for e in pod}

    # Every Secret the Pod reads is one of these four: nothing from the
    # catalog owner (postgres-app) or the lake's write token (r2-lake).
    secrets = {
        e.name: (e.value_from.secret_key_ref.name, e.value_from.secret_key_ref.key)
        for e in pod
        if e.value_from
    }
    # DAGSTER_PG_PASSWORD is the run launcher's own, in every run Pod: the
    # run's process writes its events to Dagster's metadata database.
    assert secrets == {
        **READER_SECRETS,
        "HEARTBEAT_URL": ("pipeline-heartbeat", "url"),
        "DAGSTER_PG_PASSWORD": ("dagster-db", "postgresql-password"),
    }
    assert {name for name, _ in secrets.values()}.isdisjoint({"postgres-app", "r2-lake"})
    # No variable of the writers' set: not the owner's string, not dlt's
    # catalog or its storage keys.
    assert "PG_URI" not in env
    assert not [
        n
        for n in names
        if n.startswith("DESTINATION__")
        and n.endswith(("CATALOG", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "ENDPOINT_URL"))
    ]

    # The catalog string names the read-only role, the read-write Service of
    # the cluster, and carries no password: libpq reads PGPASSWORD.
    catalog = env["LAGOM_LAKE_CATALOG"].value
    assert "user=lake_reader" in catalog and "host=postgres-rw" in catalog
    assert "dbname=lagom" in catalog and "sslmode=require" in catalog
    assert "password" not in catalog.lower() and "$(" not in catalog
    assert not [e for e in pod if "$(" in (e.value or "")]

    # The bucket is the environment's own, and not the lake's.
    assert env["LAGOM_DASHBOARDS_BUCKET"].value == f"lagom-{environment}-dashboards"
    assert env["LAGOM_LAKE_DATA_PATH"].value == f"s3://lagom-{environment}-lake/"


def test_the_dashboards_job_is_queued_with_the_pipeline_and_in_its_own_group(defs) -> None:  # type: ignore[no-untyped-def]
    """Same tag as `usgs`: the run queue never runs the render beside it."""
    graph = defs.resolve_asset_graph()
    key = AssetKey(["dashboards", "pages"])
    assert graph.get(key).group_name == "dashboards"
    # Not downstream of the pipeline: `usgs` selects `.downstream()` and would
    # run the render with the writers' credentials.
    assert not graph.get(key).parent_keys
    usgs = next(j for j in defs.resolve_all_job_defs() if j.name == "usgs")
    assert key not in usgs.asset_layer.executable_asset_keys
    job = next(j for j in defs.resolve_all_job_defs() if j.name == "dashboards")
    assert job.tags["lagom/pipeline"] == "usgs"
    assert job.asset_layer.executable_asset_keys == {key}


def _import_with(env_extra: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Import the code location in a process of its own, with the ping URL and `env_extra`."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(sys.path),
        "HEARTBEAT_URL": "https://hc-ping.invalid/test",
        **env_extra,
    }
    script = (
        "from lagom_orchestration.definitions import defs\n"
        "print(sorted(j.name for j in defs.resolve_all_job_defs()))\n"
        "print(sorted((s.job_name, s.cron_schedule) for s in defs.schedules))\n"
    )
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)


def test_the_streaming_jobs_are_absent_unless_the_environment_has_the_showcase(defs) -> None:  # type: ignore[no-untyped-def]
    """Prod has no `serving` until ADR-0047's last steps: no job, no schedule, no alarm."""
    for name in ("streaming", "streaming_archive"):
        assert name not in {j.name for j in defs.resolve_all_job_defs()}
        assert name not in {s.job_name for s in defs.schedules}  # type: ignore[union-attr]


def test_the_streaming_jobs_run_hourly_where_the_showcase_is(defs, tmp_path) -> None:  # type: ignore[no-untyped-def]
    done = _import_with({"LAGOM_STREAMING": "true"}, tmp_path)
    assert done.returncode == 0, done.stderr
    jobs, schedules = done.stdout.splitlines()
    assert "'streaming'" in jobs and "'streaming_archive'" in jobs
    assert "('streaming', '45 * * * *')" in schedules
    assert "('streaming_archive', '20 * * * *')" in schedules


@pytest.mark.parametrize("value", ["True", "yes", "1", ""])
def test_a_wrong_streaming_switch_stops_the_code_location(defs, tmp_path, value) -> None:  # type: ignore[no-untyped-def]
    done = _import_with({"LAGOM_STREAMING": value}, tmp_path)
    assert done.returncode != 0
    assert "LAGOM_STREAMING must be 'true' or 'false'" in done.stderr


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_the_streaming_pod_holds_the_serving_owner_and_nothing_of_the_lake(  # type: ignore[no-untyped-def]
    defs, environment
) -> None:
    """The real values of each environment, merged with the streaming job's real tag."""
    from lagom_orchestration.definitions import RUN_RESOURCES, STREAMING_TAGS, streaming

    pod = _run_pod_env(streaming, _code_server_env(environment))
    names = [e.name for e in pod]
    assert len(names) == len(set(names))
    secrets = {
        e.name: (e.value_from.secret_key_ref.name, e.value_from.secret_key_ref.key)
        for e in pod
        if e.value_from
    }
    assert secrets == {
        "LAGOM_PG_USER": ("serving-credentials", "username"),
        "LAGOM_PG_PASSWORD": ("serving-credentials", "password"),
        "HEARTBEAT_URL": ("pipeline-heartbeat", "url"),
        "DAGSTER_PG_PASSWORD": ("dagster-db", "postgresql-password"),
    }
    values = {e.name: e.value for e in pod}
    assert values["LAGOM_PG_HOST"] == "postgres-rw"
    assert values["LAGOM_PG_DBNAME"] == "serving"
    assert values["LAGOM_PG_SSLMODE"] == "require"
    assert "PG_URI" not in values and "LAGOM_LAKE_CATALOG" not in values
    assert not any("R2" in n or "AWS" in n for n in names)
    # One run's resources, and no pipeline tag: it waits in the global slot,
    # not in the tag's.
    assert STREAMING_TAGS["dagster-k8s/config"]["container_config"]["resources"] == RUN_RESOURCES
    assert "lagom/pipeline" not in streaming.tags


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_the_archive_pod_holds_the_lake_writer_and_the_serving_owner_only(  # type: ignore[no-untyped-def]
    defs, environment
) -> None:
    """It moves rows from `serving` to the lake: both owners, nothing of the readers or dlt."""
    from lagom_orchestration.definitions import ARCHIVE_TAGS, RUN_RESOURCES, streaming_archive

    pod = _run_pod_env(streaming_archive, _code_server_env(environment))
    names = [e.name for e in pod]
    assert len(names) == len(set(names))
    secrets = {
        e.name: (e.value_from.secret_key_ref.name, e.value_from.secret_key_ref.key)
        for e in pod
        if e.value_from
    }
    assert secrets == {
        **WRITER_SECRETS,
        "LAGOM_PG_USER": ("serving-credentials", "username"),
        "LAGOM_PG_PASSWORD": ("serving-credentials", "password"),
        "HEARTBEAT_URL": ("pipeline-heartbeat", "url"),
        "DAGSTER_PG_PASSWORD": ("dagster-db", "postgresql-password"),
    }
    values = {e.name: e.value for e in pod}
    assert values["LAGOM_PG_DBNAME"] == "serving" and values["LAGOM_PG_HOST"] == "postgres-rw"
    assert values["LAGOM_LAKE_CATALOG"] == "ducklake:postgres:$(PG_URI)?sslmode=require"
    assert values["LAGOM_LAKE_DATA_PATH"] == f"s3://lagom-{environment}-lake/"
    # dlt's composed credentials are not needed; the code server's own
    # (non-secret) DESTINATION__ settings may stay.
    assert not [n for n in names if n.startswith("DESTINATION__") and "CREDENTIALS__CATALOG" in n]
    assert not [n for n in names if "AWS_SECRET_ACCESS_KEY" in n or "AWS_ACCESS_KEY_ID" in n]
    for index, (name, value) in enumerate(values.items()):
        for reference in re.findall(r"\$\((\w+)\)", value or ""):
            assert reference in names[:index], (name, reference)
    assert ARCHIVE_TAGS["dagster-k8s/config"]["container_config"]["resources"] == RUN_RESOURCES
    assert "lagom/pipeline" not in streaming_archive.tags
