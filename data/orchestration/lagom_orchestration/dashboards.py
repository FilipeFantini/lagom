"""The dashboards' render and its publication (ADR-0040, ADR-0044).

One run does, in order, and stops at the first thing that is wrong:

1. `dct validate --strict --warehouse`: the boards are valid and every query
   describes against the real lake;
2. `dct render`, to HTML and to the rows the page is drawn from;
3. `check_boards.py`: the rows equal what gold holds, no query is empty;
4. the watermark: every page says, once, when it was built, and the time is
   now, so a page that is served stale or cached cannot pass as new;
5. the upload to the environment's dashboards bucket, then the removal of the
   pages that are no longer boards.

The Pod holds read-only credentials for the lake and a write token for the
dashboards bucket only (READER_ENV in definitions.py). `dct` is a tool we do
not control, so it runs with the lake's variables and nothing else: the
bucket's token never reaches it, and neither does the ping URL.

Nothing here prints the environment or a connection string. A failure's text
goes through `redact_secrets`, because DuckDB repeats the catalog URI when an
attach fails.
"""

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import botocore.session
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dagster import (
    DagsterRunStatus,
    DefaultSensorStatus,
    RunRequest,
    RunStatusSensorContext,
    SensorDefinition,
    run_status_sensor,
)

# define_asset_job returns this type; dagster does not export it at the top level.
from dagster._core.definitions.unresolved_asset_job_definition import UnresolvedAssetJobDefinition

from lagom_orchestration.redact import redact_secrets

# What `dct` and `check_boards.py` get from the Pod's environment. The lake's
# own variables (the same the dbt `lake` target reads), the catalog password
# libpq reads, and what a process needs to run. Not DO_NOT_TRACK: dbt Charts'
# telemetry stays on in the cluster (ADR-0044, decision 9), which is why every
# command below has a timeout. Not the dashboards bucket's token, not the ping
# URL.
SUBPROCESS_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "PYTHONPATH",
    "SSL_CERT_FILE",
    "PGPASSWORD",
    "PGSSLMODE",
    "LAGOM_LAKE_CATALOG",
    "LAGOM_LAKE_DATA_PATH",
    "LAGOM_LAKE_METADATA_SCHEMA",
    "LAGOM_R2_ACCESS_KEY_ID",
    "LAGOM_R2_SECRET_ACCESS_KEY",
    "LAGOM_R2_ENDPOINT",
)

# The run's Pod is stopped after 25 minutes (activeDeadlineSeconds in the
# Dagster values). A command that stalls, a telemetry request included, is
# killed well before that, and the run fails with the command's name.
VALIDATE_TIMEOUT_SECONDS = 180
RENDER_TIMEOUT_SECONDS = 360
CHECK_TIMEOUT_SECONDS = 120
S3_CONNECT_TIMEOUT_SECONDS = 10
S3_READ_TIMEOUT_SECONDS = 60
# One query at a time. `dct render` runs 8 query workers by default, and each
# attaches the lake on its own connection: the catalog role `lake_reader` is
# limited to 3 connections (postgres-*/cluster.yaml), and the first staging run
# failed with "too many connections for role" (2026-11-15). DuckDB serializes
# the queries anyway (`dct render --help`), so one worker costs no time, and it
# keeps the render's memory to one connection's.
RENDER_MAX_WORKERS = 1
# The tail of a failing command's output that goes into the error: what a
# person needs, and little enough to e-mail.
OUTPUT_TAIL_CHARS = 4000

# The line the boards print, from `style.timestamp.format` in
# data/dashboards/charts/*.yml. `dct` writes the render's clock in the
# board's time zone, UTC. A test keeps the boards and this text together.
WATERMARK = "Page built at "
_WATERMARK = re.compile(rf"{WATERMARK}(\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}) UTC")
# The stamp has the minute's precision and the render runs for seconds; a
# quarter of an hour is far more than that, and far less than a stale page.
WATERMARK_MAX_AGE = timedelta(minutes=15)
WATERMARK_MAX_AHEAD = timedelta(minutes=2)

# Short: a page is rebuilt every 30 minutes at most, and the reader should see
# the new one soon. Nothing the pages load is hashed: each page is one file
# that loads nothing from outside (the CI job `dashboards (render)` checks it),
# so there are no assets to cache for longer or to upload before the HTML.
PAGE_CONTENT_TYPE = "text/html; charset=utf-8"
PAGE_CACHE_CONTROL = "public, max-age=300"


# The container's peak memory, from its cgroup (v2): the run's process and
# every command it started, as the kernel counted them against the limit. A
# render's Pod lives less than a minute, which `kubectl top` (a sample a
# minute) does not see. See docs/data.md, Memory of a run.
CGROUP_MEMORY_PEAK = Path("/sys/fs/cgroup/memory.peak")


def memory_peak_mib(path: Path = CGROUP_MEMORY_PEAK) -> float | None:
    """The container's peak memory in MiB, or None where the kernel does not report it.

    A measurement, not data: a kernel without the file (cgroup v1, or older
    than 5.19) gives None and the log says so; nothing is published from it.
    """
    try:
        return int(path.read_text()) / 2**20
    except (OSError, ValueError):
        return None


def subprocess_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The variables `dct` and the check run with: those in SUBPROCESS_ENV that are set."""
    return {name: environ[name] for name in SUBPROCESS_ENV if name in environ}


def run_command(
    name: str, args: list[str], *, env: Mapping[str, str], cwd: Path, timeout: int
) -> str:
    """Run a command; return its output; raise with its redacted tail if it fails.

    A command that outlives `timeout` is killed. The error carries the
    command's name and the tail of its output (redacted), never its
    environment, and is not chained to the original exception.
    """
    try:
        done = subprocess.run(
            args, env=dict(env), cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{name} did not finish within {timeout} s and was stopped") from None
    if done.returncode != 0:
        output = redact_secrets(f"{done.stderr}\n{done.stdout}".strip())
        raise RuntimeError(
            f"{name} failed (exit {done.returncode}):\n{output[-OUTPUT_TAIL_CHARS:]}"
        )
    return done.stdout


def board_files(charts: Path) -> list[Path]:
    """The boards: `*.yml` in `charts`, except fragments (a name that starts with `_`)."""
    boards = sorted(path for path in charts.glob("*.yml") if not path.name.startswith("_"))
    if not boards:
        raise RuntimeError(f"no board found in {charts}")
    return boards


def check_watermark(page: str, html: str, now: datetime) -> None:
    """Fail unless `html` says, once, that it was built within the last minutes.

    A count of the marker text and a match of the whole line must both say
    once: a page whose board lost its stamp, or shows it twice, or shows a
    time that is not now, is not published.
    """
    found = _WATERMARK.findall(html)
    if html.count(WATERMARK) != 1 or len(found) != 1:
        raise RuntimeError(
            f"{page}: the watermark '{WATERMARK}<UTC time>' must appear exactly once; "
            f"the page has {html.count(WATERMARK)} of the text and {len(found)} of the whole line"
        )
    built = datetime.strptime(found[0], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    if not now - WATERMARK_MAX_AGE <= built <= now + WATERMARK_MAX_AHEAD:
        raise RuntimeError(
            f"{page}: the watermark says {found[0]} UTC, which is not within "
            f"{WATERMARK_MAX_AGE} of now ({now:%Y-%m-%d %H:%M} UTC)"
        )


def render_pages(
    repo: Path, work: Path, environ: Mapping[str, str], now: Callable[[], datetime]
) -> dict[str, bytes]:
    """Validate, render and check the boards against the lake; return `{file name: page}`.

    The dbt manifest `dct` resolves `ref()` against is the one the image's build
    wrote (`dbt parse`, Dockerfile): the project is read-only at run time and
    the manifest is of the same code. The lake is attached read-only by
    data/dashboards/profiles.yml and by `check_boards.py`.
    """
    dashboards = repo / "data" / "dashboards"
    manifest = repo / "data" / "warehouse" / "target" / "manifest.json"
    if not manifest.is_file():
        raise RuntimeError(
            f"the dbt manifest {manifest} is missing: the image was built without it"
        )
    boards = board_files(dashboards / "charts")
    env = subprocess_env(environ)
    base = ["--project-dir", str(dashboards)]

    def dct(name: str, args: list[str], timeout: int) -> str:
        return run_command(name, ["dct", *args], env=env, cwd=work, timeout=timeout)

    dct(
        "dct validate",
        ["validate", "--strict", "--warehouse", str(dashboards / "charts"), *base],
        VALIDATE_TIMEOUT_SECONDS,
    )
    out = {"html": work / "html", "data": work / "data"}
    for form, directory in out.items():
        directory.mkdir()
        # --no-cache: a cached result is not a render of this lake. A class
        # that no earthquake in the window belongs to is expected.
        dct(
            f"dct render --format {form}",
            [
                "render",
                *[str(board) for board in boards],
                *base,
                "--no-cache",
                "--max-workers",
                str(RENDER_MAX_WORKERS),
                "--ignore-warning",
                "WARN-CATEGORY-COLOR-PIN-UNSEEN",
                "--format",
                form,
                "--output",
                f"{directory}/{{stem}}.{form}",
            ],
            RENDER_TIMEOUT_SECONDS,
        )
    rendered = sorted(out["data"].glob("*.data"))
    if len(rendered) != len(boards):
        raise RuntimeError(f"{len(boards)} boards but {len(rendered)} renders of their data")
    run_command(
        "check_boards",
        [sys.executable, str(dashboards / "check_boards.py"), *[str(path) for path in rendered]],
        env=env,
        cwd=work,
        timeout=CHECK_TIMEOUT_SECONDS,
    )
    pages: dict[str, bytes] = {}
    for board in boards:
        page = out["html"] / f"{board.stem}.html"
        # No `errors=`: a page that is not UTF-8 is an error, not a replacement.
        body = page.read_bytes()
        check_watermark(page.name, body.decode("utf-8"), now())
        pages[page.name] = body
    return pages


def s3_client(endpoint: str, key_id: str, secret: str):  # type: ignore[no-untyped-def]
    """A client of the dashboards bucket's R2 endpoint.

    `when_required`: botocore 1.36 and later add a checksum header R2 does not
    accept on an upload unless the operation demands one.
    """
    return botocore.session.get_session().create_client(
        "s3",
        endpoint_url=endpoint if endpoint.startswith("https://") else f"https://{endpoint}",
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(
            connect_timeout=S3_CONNECT_TIMEOUT_SECONDS,
            read_timeout=S3_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 3, "mode": "standard"},
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def publish(client, bucket: str, pages: Mapping[str, bytes]) -> list[str]:  # type: ignore[no-untyped-def]
    """Upload every page, then delete the `.html` objects that are not in `pages`.

    The deletion comes after the last upload, so a failure part-way leaves the
    old set (and some new pages) and never a bucket with fewer pages than
    before. Only `.html` keys are deleted: anything else in the bucket is not
    this step's to remove. An S3 error is re-raised as a plain error with the
    redacted text, from nothing.
    """
    try:
        for key in sorted(pages):
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=pages[key],
                ContentType=PAGE_CONTENT_TYPE,
                CacheControl=PAGE_CACHE_CONTROL,
            )
        stale: list[str] = []
        for listing in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in listing.get("Contents", []):
                if item["Key"].endswith(".html") and item["Key"] not in pages:
                    stale.append(item["Key"])
        for start in range(0, len(stale), 1000):
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in stale[start : start + 1000]]},
            )
    except (BotoCoreError, ClientError) as error:
        raise RuntimeError(
            f"upload to the dashboards bucket failed: {redact_secrets(str(error))}"
        ) from None
    return sorted(pages)


def _require(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def run_dashboards(
    repo: Path,
    environ: Mapping[str, str] = os.environ,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[str]:
    """The whole step: render, check, publish. Returns the names of the pages published."""
    # Read first: a missing Secret key should fail before a render is spent.
    bucket = _require(environ, "LAGOM_DASHBOARDS_BUCKET")
    client = s3_client(
        _require(environ, "LAGOM_DASHBOARDS_ENDPOINT"),
        _require(environ, "LAGOM_DASHBOARDS_ACCESS_KEY_ID"),
        _require(environ, "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY"),
    )
    with tempfile.TemporaryDirectory(prefix="dashboards-") as directory:
        pages = render_pages(repo, Path(directory), environ, now)
    return publish(client, bucket, pages)


def build_render_trigger(
    usgs_job: UnresolvedAssetJobDefinition, dashboards_job: UnresolvedAssetJobDefinition
) -> SensorDefinition:
    """After each successful `usgs` run, request one `dashboards` run.

    The run key is the `usgs` run's ID, so a tick that is evaluated twice asks
    once. The two jobs share the tag `lagom/pipeline: usgs`, which the run
    queue limits to one run, so the render waits for the next `usgs` run
    instead of overlapping it.
    """

    @run_status_sensor(
        name="render_dashboards_after_usgs",
        run_status=DagsterRunStatus.SUCCESS,
        monitored_jobs=[usgs_job],
        request_job=dashboards_job,
        default_status=DefaultSensorStatus.RUNNING,
    )
    def render_dashboards_after_usgs(context: RunStatusSensorContext) -> RunRequest:
        return RunRequest(run_key=context.dagster_run.run_id)

    return render_dashboards_after_usgs
