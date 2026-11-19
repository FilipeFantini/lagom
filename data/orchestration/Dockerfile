# The platform's Dagster code location (ADR-0026): Dagster, the sources'
# assets, the dbt project and the dashboards' boards, in one image. Built from
# the repository root, because it bundles directories from data/sources/,
# data/warehouse/ and data/dashboards/:
#   docker build -f data/orchestration/Dockerfile .
# The same image runs the code server (`dagster api grpc`, set by the Helm
# chart) and every run Pod the run launcher starts (`dagster api
# execute_run`). Both pass the whole command.

FROM python:3.13-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base

FROM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

# uv exists only here: the runtime image gets the finished virtualenv.
FROM base AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY data/orchestration/pyproject.toml data/orchestration/uv.lock ./
# The dashboards group is dbt Charts (`dct`), which the `dashboards` job runs.
RUN uv sync --locked --no-dev --group dashboards

FROM base AS runtime
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin lagom
COPY --from=build /opt/venv /opt/venv
# The repository layout, kept under /app: definitions.py finds the dbt project
# and the sources by their paths relative to it.
WORKDIR /app
COPY data/orchestration/lagom_orchestration/ data/orchestration/lagom_orchestration/
COPY data/sources/usgs/ingestion/ data/sources/usgs/ingestion/
COPY data/sources/usgs/assets/ data/sources/usgs/assets/
COPY data/warehouse/dbt_project.yml data/warehouse/profiles.yml data/warehouse/
COPY data/warehouse/macros/ data/warehouse/macros/
COPY data/warehouse/models/ data/warehouse/models/
COPY data/warehouse/tests/ data/warehouse/tests/
# The dashboards' boards and the check that proves them (the `dashboards` job).
COPY data/dashboards/dbt_charts.yml data/dashboards/profiles.yml data/dashboards/check_boards.py data/dashboards/
COPY data/dashboards/charts/ data/dashboards/charts/
COPY data/dashboards/partials/ data/dashboards/partials/
# The root filesystem is read-only at run time; every writer goes to /tmp
# (an emptyDir): dlt's working files, dbt's logs and target, DuckDB's
# extensions (installed per run, under HOME).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app:/app/data/orchestration \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    DLT_DATA_DIR=/tmp/dlt \
    DBT_LOG_PATH=/tmp/dbt-logs \
    DO_NOT_TRACK=1 \
    RUNTIME__DLTHUB_TELEMETRY=false
# dagster-dbt reads the manifest when the code location loads, so it is built
# here. `dbt parse` reads the profile but opens no connection: the values below
# exist only for this step and are not in the image's environment.
RUN LAGOM_LAKE_CATALOG=ducklake:parse.ducklake LAGOM_LAKE_DATA_PATH=parse/ \
    LAGOM_LAKE_METADATA_SCHEMA=main LAGOM_R2_ACCESS_KEY_ID=parse \
    LAGOM_R2_SECRET_ACCESS_KEY=parse LAGOM_R2_ENDPOINT=https://parse.invalid \
    dbt parse --project-dir data/warehouse --profiles-dir data/warehouse \
      --target lake --log-path /tmp/dbt-logs --quiet \
    && rm -rf /tmp/dbt-logs
LABEL org.opencontainers.image.source="https://github.com/FilipeFantini/lagom"
USER 10001:10001
# No ENTRYPOINT: the Helm chart starts the code server, and the run launcher
# each run, with a full command that begins with `dagster`. An entrypoint of
# `dagster` would turn that into `dagster dagster ...` (found in review of
# PR #32).
