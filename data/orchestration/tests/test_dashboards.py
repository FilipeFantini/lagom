"""The dashboards' step: what it runs, in what order, what it refuses, what it uploads.

`dct` and `check_boards.py` are replaced by small scripts that record how they
were called, so the real subprocess code runs: the environment they get, the
timeout, the order, the way their failure is reported. The upload is checked
against botocore's own stubber, which fails a call whose parameters the S3
model rejects and a call out of the order the test expects. Nothing opens a
network connection.
"""

import json
import os
import stat
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from botocore.stub import Stubber
from dagster import (
    DagsterInstance,
    DefaultSensorStatus,
    RunRequest,
    build_run_status_sensor_context,
    define_asset_job,
    job,
    op,
)

from lagom_orchestration import dashboards, dbt_docs
from lagom_orchestration.dashboards import (
    DOCS_KEY,
    DOCS_PREFIX,
    PAGE_CACHE_CONTROL,
    PAGE_CONTENT_TYPE,
    REPORTS_PREFIX,
    WATERMARK,
    build_render_trigger,
    check_no_secrets,
    check_watermark,
    memory_peak_mib,
    publish,
    render_pages,
    run_command,
    run_docs,
    run_reports,
    s3_client,
    subprocess_env,
)

NOW = datetime(2026, 11, 14, 12, 30, tzinfo=UTC)
PAGE = f"<html><body>{WATERMARK}2026-11-14 12:29 UTC<p>data</p></body></html>"
REPO = Path(__file__).resolve().parents[3]

# A Pod's environment as the dashboards job gives it, with the values that
# must never reach `dct` or the check.
POD = {
    "PATH": os.environ["PATH"],
    "HOME": "/tmp",
    "PGPASSWORD": "reader-password",
    "PGSSLMODE": "require",
    "LAGOM_LAKE_CATALOG": "ducklake:postgres:host=postgres-rw user=lake_reader",
    "LAGOM_LAKE_DATA_PATH": "s3://lake/",
    "LAGOM_LAKE_METADATA_SCHEMA": "ducklake",
    "LAGOM_R2_ACCESS_KEY_ID": "reader-key",
    "LAGOM_R2_SECRET_ACCESS_KEY": "reader-secret",
    "LAGOM_R2_ENDPOINT": "https://lake.invalid",
    "LAGOM_DASHBOARDS_BUCKET": "bucket",
    "LAGOM_DASHBOARDS_ACCESS_KEY_ID": "bucket-key",
    "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY": "bucket-secret",
    "LAGOM_DASHBOARDS_ENDPOINT": "https://bucket.invalid",
    "HEARTBEAT_URL": "https://hc-ping.invalid/ping",
    "DO_NOT_TRACK": "1",
    "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
}

# The fake `dct`: appends one JSON line (arguments, the names of its variables,
# its working directory) to a log, then does what its mode says. The mode is
# read from a file the test writes, since the script's environment is scrubbed.
FAKE_DCT = """#!__PYTHON__
import json, os, sys, time
from pathlib import Path

root = Path("__ROOT__")
mode = (root / "mode").read_text().strip()
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"tool": "dct", "args": sys.argv[1:], "env": sorted(os.environ),
                          "cwd": os.getcwd()}) + "\\n")
args = sys.argv[1:]
if mode == "validate-fails" and args[0] == "validate":
    sys.stderr.write("attach failed: ducklake:postgres:postgresql://lake_reader:hunter2@postgres-rw/lagom\\n")
    sys.exit(1)
if mode == "hang" and args[0] == "validate":
    time.sleep(60)
if args[0] == "render":
    form = args[args.index("--format") + 1]
    pattern = args[args.index("--output") + 1]
    for board in [a for a in args[1:] if a.endswith(".yml")]:
        out = Path(pattern.replace("{stem}", Path(board).stem))
        out.write_text(Path("__PAGE__").read_text() if form == "html" else "{}")
"""
FAKE_CHECK = """import json, os, sys
from pathlib import Path

root = Path("__ROOT__")
(root / "calls.jsonl").open("a").write(
    json.dumps({"tool": "check_boards", "args": sys.argv[1:], "env": sorted(os.environ)}) + "\\n"
)
sys.exit(1 if (root / "check-fails").exists() else 0)
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with one board, a manifest, and the fake tools on PATH."""
    root = tmp_path / "repo"
    charts = root / "data" / "dashboards" / "charts"
    charts.mkdir(parents=True)
    (charts / "earthquakes.yml").write_text("id: earthquakes\n")
    (charts / "_fragment.yml").write_text("x: 1\n")
    (root / "data" / "warehouse" / "target").mkdir(parents=True)
    (root / "data" / "warehouse" / "target" / "manifest.json").write_text("{}")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "page.html").write_text(PAGE)
    dct = bin_dir / "dct"
    dct.write_text(
        FAKE_DCT.replace("__PYTHON__", sys.executable)
        .replace("__ROOT__", str(tmp_path))
        .replace("__PAGE__", str(tmp_path / "page.html"))
    )
    dct.chmod(dct.stat().st_mode | stat.S_IEXEC)
    # check_boards.py is run as `python <path>`: the script sits in the checkout.
    (root / "data" / "dashboards" / "check_boards.py").write_text(
        FAKE_CHECK.replace("__ROOT__", str(tmp_path))
    )
    (tmp_path / "mode").write_text("ok")
    return root


def _env(repo: Path) -> dict[str, str]:
    return {**POD, "PATH": f"{repo.parent / 'bin'}{os.pathsep}{os.environ['PATH']}"}


def _calls(repo: Path) -> list[dict[str, Any]]:
    log = repo.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_the_step_validates_renders_then_checks_in_that_order(repo: Path, tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    pages = render_pages(repo, work, _env(repo), lambda: NOW)
    assert pages == {"earthquakes.html": PAGE.encode()}
    calls = _calls(repo)
    assert [(c["tool"], c["args"][0] if c["tool"] == "dct" else "") for c in calls] == [
        ("dct", "validate"),
        ("dct", "render"),
        ("dct", "render"),
        ("check_boards", ""),
    ]
    validate = calls[0]["args"]
    assert validate[:3] == ["validate", "--strict", "--warehouse"]
    # Fragments are not boards; every render is uncached and runs one query at
    # a time, within the catalog role's connection limit.
    renders = [c["args"] for c in calls[1:3]]
    assert [args[args.index("--format") + 1] for args in renders] == ["html", "data"]
    for args in renders:
        assert "--no-cache" in args
        assert args[args.index("--max-workers") + 1] == "1"
        assert any(a.endswith("earthquakes.yml") for a in args)
        assert not any("_fragment" in a for a in args)
    assert calls[3]["args"][0].endswith("earthquakes.data")


def test_dct_and_the_check_get_the_lakes_variables_and_nothing_else(
    repo: Path, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    render_pages(repo, work, _env(repo), lambda: NOW)
    for call in _calls(repo):
        names = set(call["env"])
        # The lake, read-only, and what a process needs.
        assert {"PGPASSWORD", "LAGOM_LAKE_CATALOG", "LAGOM_R2_ACCESS_KEY_ID"} <= names
        # The bucket's token, the ping URL: not for a tool we do not control.
        assert not {n for n in names if n.startswith("LAGOM_DASHBOARDS_")}, call["tool"]
        assert "HEARTBEAT_URL" not in names, call["tool"]
        # Telemetry stays on in the cluster (ADR-0044, decision 9): the step
        # does not turn it off, whatever the Pod's environment says.
        assert "DO_NOT_TRACK" not in names, call["tool"]
        assert "DBT_SEND_ANONYMOUS_USAGE_STATS" not in names, call["tool"]
    # Run from the scratch directory, so a stray relative write is not in the image.
    assert _calls(repo)[0]["cwd"] == str(work.resolve())


def test_subprocess_env_keeps_only_what_is_set_and_listed() -> None:
    env = subprocess_env({"PATH": "/bin", "LAGOM_DASHBOARDS_ACCESS_KEY_ID": "k", "OTHER": "1"})
    assert env == {"PATH": "/bin"}


def test_every_command_has_a_timeout(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, Any]] = []
    real = dashboards.subprocess.run

    def spy(args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((Path(args[0]).name if args[0] != "dct" else "dct", kwargs.get("timeout")))
        return real(args, **kwargs)

    monkeypatch.setattr(dashboards.subprocess, "run", spy)
    work = tmp_path / "work"
    work.mkdir()
    render_pages(repo, work, _env(repo), lambda: NOW)
    assert len(seen) == 4
    assert all(isinstance(timeout, int) and 0 < timeout < 1500 for _, timeout in seen), seen
    # Together they fit the Pod's 25-minute deadline with room for the upload.
    assert sum(timeout for _, timeout in seen) < 1500 - 120


def test_a_command_that_stalls_is_stopped_and_named(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a blocked telemetry request would look like from the outside."""
    (tmp_path / "mode").write_text("hang")
    monkeypatch.setattr(dashboards, "VALIDATE_TIMEOUT_SECONDS", 1)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match=r"dct validate did not finish within 1 s"):
        render_pages(repo, work, _env(repo), lambda: NOW)
    assert [c["args"][0] for c in _calls(repo)] == ["validate"]


def test_run_command_kills_a_process_that_outlives_its_timeout(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="sleeper did not finish within 1 s"):
        run_command(
            "sleeper",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={},
            cwd=tmp_path,
            timeout=1,
        )


def test_a_failing_command_stops_the_step_and_its_error_has_no_password(
    repo: Path, tmp_path: Path
) -> None:
    (tmp_path / "mode").write_text("validate-fails")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError) as raised:
        render_pages(repo, work, _env(repo), lambda: NOW)
    text = str(raised.value)
    assert "dct validate failed (exit 1)" in text
    assert "postgres-rw" in text and "hunter2" not in text and "***" in text
    assert raised.value.__cause__ is None
    # Nothing after the failed command ran.
    assert [c["args"][0] for c in _calls(repo)] == ["validate"]


def test_a_failing_check_stops_the_step_before_the_pages_are_returned(
    repo: Path, tmp_path: Path
) -> None:
    (tmp_path / "check-fails").write_text("")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="check_boards failed"):
        render_pages(repo, work, _env(repo), lambda: NOW)


def test_a_missing_manifest_fails_by_name(repo: Path, tmp_path: Path) -> None:
    (repo / "data" / "warehouse" / "target" / "manifest.json").unlink()
    with pytest.raises(RuntimeError, match="manifest.json is missing"):
        render_pages(repo, tmp_path, _env(repo), lambda: NOW)


def test_a_page_without_the_watermark_is_not_returned(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("<html>no stamp</html>")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(RuntimeError, match="exactly once"):
        render_pages(repo, work, _env(repo), lambda: NOW)


# The watermark ---------------------------------------------------------------


def test_the_watermark_is_accepted_once_and_recent() -> None:
    check_watermark("p.html", PAGE, NOW)


@pytest.mark.parametrize(
    ("html", "why"),
    [
        ("<html>none</html>", "0 of the text"),
        (PAGE + PAGE, "2 of the text"),
        (f"<html>{WATERMARK}yesterday</html>", "1 of the text and 0 of the whole line"),
        (f"<html>{WATERMARK}2026-11-14 12:29 UTC {WATERMARK}</html>", "2 of the text"),
        (f"<html>{WATERMARK}2026-11-14 12:29 UTC{WATERMARK}2026-11-14 12:29 UTC</html>", "2 of"),
    ],
)
def test_the_watermark_must_appear_exactly_once(html: str, why: str) -> None:
    with pytest.raises(RuntimeError, match="exactly once") as raised:
        check_watermark("p.html", html, NOW)
    assert why in str(raised.value)


def test_a_stale_or_future_watermark_is_refused() -> None:
    stale = PAGE.replace("12:29", "11:00")
    with pytest.raises(RuntimeError, match="not within"):
        check_watermark("p.html", stale, NOW)
    future = PAGE.replace("12:29", "13:30")
    with pytest.raises(RuntimeError, match="not within"):
        check_watermark("p.html", future, NOW)


def _timestamp_style(board: Path) -> dict[str, Any]:
    """The board's `style.timestamp`, from the board or what it `extends`, nearest first."""
    path = board
    for _ in range(5):
        document = yaml.safe_load(path.read_text())
        stamp = document.get("style", {}).get("timestamp")
        if stamp is not None:
            return dict(stamp)
        assert "extends" in document, f"{board.name}: no style.timestamp and nothing extended"
        path = (path.parent / document["extends"]).resolve()
    raise AssertionError(f"{board.name}: style.timestamp not found within five extends")


def test_the_boards_print_the_text_the_step_looks_for() -> None:
    """The label is in two places, the board's format and WATERMARK: they must agree.

    The format is applied with strftime, as dbt Charts does, to a known time;
    the step's own pattern must then accept the result.
    """
    boards = sorted((REPO / "data" / "dashboards" / "charts").glob("[!_]*.yml"))
    assert boards
    for board in boards:
        stamp = _timestamp_style(board)
        assert stamp.get("visible", True) is True, board.name
        assert stamp["format"].startswith(WATERMARK), board.name
        line = datetime(2026, 11, 14, 12, 29, tzinfo=UTC).strftime(stamp["format"])
        check_watermark(board.name, f"<html>{line}</html>", NOW)


# The upload ------------------------------------------------------------------


def _client() -> Any:
    return s3_client("bucket.invalid", "key", "secret")


def _put(stubber: Stubber, key: str, body: bytes) -> None:
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": "b",
            "Key": key,
            "Body": body,
            "ContentType": PAGE_CONTENT_TYPE,
            "CacheControl": PAGE_CACHE_CONTROL,
        },
    )


def _list(stubber: Stubber, prefix: str, keys: list[str], folders: Sequence[str] = ()) -> None:
    """The listing the upload expects: the prefix's own level only, as S3 does with a delimiter."""
    stubber.add_response(
        "list_objects_v2",
        {
            "Contents": [{"Key": key} for key in keys],
            "CommonPrefixes": [{"Prefix": folder} for folder in folders],
            "IsTruncated": False,
        },
        {"Bucket": "b", "Prefix": prefix, "Delimiter": "/"},
    )


def test_every_page_is_uploaded_with_its_type_and_a_short_cache_then_stale_pages_go() -> None:
    client = _client()
    with Stubber(client) as stubber:
        # The stubber expects calls in this order and with these parameters;
        # an upload after the listing, or a missing header, fails the test.
        _put(stubber, "earthquakes.html", b"<new>")
        _put(stubber, "tides.html", b"<new2>")
        # The reports' listing is the root's own level: `docs/` shows only as
        # a folder, and `docs/index.html` is not in it.
        _list(
            stubber,
            REPORTS_PREFIX,
            ["earthquakes.html", "old-board.html", "robots.txt"],
            folders=[DOCS_PREFIX],
        )
        stubber.add_response(
            "delete_objects",
            {},
            {"Bucket": "b", "Delete": {"Objects": [{"Key": "old-board.html"}]}},
        )
        published = publish(
            client,
            "b",
            REPORTS_PREFIX,
            {"tides.html": b"<new2>", "earthquakes.html": b"<new>"},
        )
        stubber.assert_no_pending_responses()
    assert published == ["earthquakes.html", "tides.html"]
    assert PAGE_CONTENT_TYPE == "text/html; charset=utf-8"
    assert PAGE_CACHE_CONTROL == "public, max-age=300"


def test_the_reports_never_delete_the_documentation_even_if_a_listing_returned_it() -> None:
    client = _client()
    with Stubber(client) as stubber:
        _put(stubber, "earthquakes.html", b"<new>")
        # A listing that wrongly holds the docs page and a nested page: the
        # deletion itself refuses what is not directly under the prefix.
        _list(stubber, REPORTS_PREFIX, ["earthquakes.html", DOCS_KEY, "docs/old.html"])
        publish(client, "b", REPORTS_PREFIX, {"earthquakes.html": b"<new>"})
        stubber.assert_no_pending_responses()


def test_the_documentation_deletes_only_stale_html_under_its_prefix() -> None:
    client = _client()
    with Stubber(client) as stubber:
        _put(stubber, DOCS_KEY, b"<docs>")
        _list(
            stubber,
            DOCS_PREFIX,
            [DOCS_KEY, "docs/old.html", "docs/notes.txt"],
            folders=["docs/sub/"],
        )
        stubber.add_response(
            "delete_objects",
            {},
            {"Bucket": "b", "Delete": {"Objects": [{"Key": "docs/old.html"}]}},
        )
        assert publish(client, "b", DOCS_PREFIX, {DOCS_KEY: b"<docs>"}) == [DOCS_KEY]
        stubber.assert_no_pending_responses()


def test_nothing_is_deleted_when_there_is_nothing_stale() -> None:
    client = _client()
    with Stubber(client) as stubber:
        _put(stubber, "earthquakes.html", b"<new>")
        _list(stubber, REPORTS_PREFIX, ["earthquakes.html"])
        publish(client, "b", REPORTS_PREFIX, {"earthquakes.html": b"<new>"})
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("prefix", "key"),
    [
        (REPORTS_PREFIX, DOCS_KEY),
        (REPORTS_PREFIX, "/x.html"),
        (DOCS_PREFIX, "earthquakes.html"),
        (DOCS_PREFIX, "docs/sub/index.html"),
        (DOCS_PREFIX, "docs/"),
        (DOCS_PREFIX, "other/index.html"),
    ],
)
def test_a_key_outside_the_prefix_or_deeper_than_one_level_is_refused_before_any_upload(
    prefix: str, key: str
) -> None:
    client = _client()
    with Stubber(client) as stubber:
        with pytest.raises(RuntimeError, match="not directly under the prefix"):
            # A valid page first: nothing of the set is uploaded when one key is wrong.
            publish(client, "b", prefix, {f"{prefix}ok.html": b"<a>", key: b"<b>"})
        stubber.assert_no_pending_responses()


def test_a_failed_upload_deletes_nothing_and_says_why_without_secrets() -> None:
    client = _client()
    with Stubber(client) as stubber:
        stubber.add_client_error(
            "put_object", service_error_code="AccessDenied", service_message="denied"
        )
        with pytest.raises(RuntimeError, match="upload to the dashboards bucket failed") as raised:
            publish(client, "b", REPORTS_PREFIX, {"earthquakes.html": b"<new>"})
        # No list and no delete were attempted: the old set stands.
        stubber.assert_no_pending_responses()
    assert "AccessDenied" in str(raised.value)
    assert raised.value.__cause__ is None


def test_the_client_talks_to_the_endpoint_with_or_without_its_scheme() -> None:
    assert s3_client("acct.r2.example.invalid", "k", "s").meta.endpoint_url == (
        "https://acct.r2.example.invalid"
    )
    assert s3_client("https://acct.r2.example.invalid", "k", "s").meta.endpoint_url == (
        "https://acct.r2.example.invalid"
    )
    config = _client().meta.config
    assert config.request_checksum_calculation == "when_required"
    assert config.connect_timeout == dashboards.S3_CONNECT_TIMEOUT_SECONDS
    assert config.read_timeout == dashboards.S3_READ_TIMEOUT_SECONDS


# The whole step --------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "LAGOM_DASHBOARDS_BUCKET",
        "LAGOM_DASHBOARDS_ENDPOINT",
        "LAGOM_DASHBOARDS_ACCESS_KEY_ID",
        "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY",
    ],
)
def test_a_missing_bucket_variable_fails_before_any_render(repo: Path, missing: str) -> None:
    env = {k: v for k, v in _env(repo).items() if k != missing}
    with pytest.raises(RuntimeError, match=f"{missing} is not set"):
        run_reports(repo, env)
    with pytest.raises(RuntimeError, match=f"{missing} is not set"):
        run_docs(repo, env)
    assert _calls(repo) == []


def test_the_reports_are_published_at_the_root(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploaded: list[tuple[Any, ...]] = []
    monkeypatch.setattr(dashboards, "publish", lambda *args: uploaded.append(args) or ["x"])
    assert run_reports(repo, _env(repo), lambda: NOW) == ["x"]
    ((_, bucket, prefix, pages),) = uploaded
    assert bucket == "bucket"
    assert prefix == ""
    assert pages == {"earthquakes.html": PAGE.encode()}


def test_the_documentation_is_generated_then_published_under_its_prefix(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated: list[tuple[Path, Path, Any]] = []

    def fake_generate(repo_: Path, work: Path, environ: Any) -> bytes:
        generated.append((repo_, work, environ))
        return b"<docs>"

    uploaded: list[tuple[Any, ...]] = []
    monkeypatch.setattr(dbt_docs, "generate_docs", fake_generate)
    monkeypatch.setattr(dashboards, "publish", lambda *args: uploaded.append(args) or [DOCS_KEY])
    assert run_docs(repo, _env(repo)) == "docs/index.html"
    ((_, bucket, prefix, objects),) = uploaded
    assert (bucket, prefix, objects) == ("bucket", "docs/", {"docs/index.html": b"<docs>"})
    assert generated[0][0] == repo


def test_a_page_with_a_credential_is_not_uploaded(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploaded: list[Any] = []
    monkeypatch.setattr(dashboards, "publish", lambda *args: uploaded.append(args) or [])
    (tmp_path / "page.html").write_text(
        f"{PAGE}<!-- {POD['LAGOM_DASHBOARDS_SECRET_ACCESS_KEY']} -->"
    )
    with pytest.raises(RuntimeError, match="LAGOM_DASHBOARDS_SECRET_ACCESS_KEY"):
        run_reports(repo, _env(repo), lambda: NOW)
    monkeypatch.setattr(
        dbt_docs, "generate_docs", lambda *_: f"<p>{POD['PGPASSWORD']}</p>".encode()
    )
    with pytest.raises(RuntimeError, match="PGPASSWORD"):
        run_docs(repo, _env(repo))
    assert uploaded == []


# The leak guard --------------------------------------------------------------

SECRET_VARIABLES = [
    "PGPASSWORD",
    "LAGOM_R2_ACCESS_KEY_ID",
    "LAGOM_R2_SECRET_ACCESS_KEY",
    "LAGOM_R2_ENDPOINT",
    "LAGOM_DASHBOARDS_ACCESS_KEY_ID",
    "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY",
    "LAGOM_DASHBOARDS_ENDPOINT",
    "HEARTBEAT_URL",
]


def test_a_clean_page_passes_the_leak_guard() -> None:
    check_no_secrets({"docs/index.html": b"<html>nothing here</html>"}, POD)


@pytest.mark.parametrize("name", SECRET_VARIABLES)
def test_the_leak_guard_names_the_variable_and_the_object_and_never_the_value(name: str) -> None:
    value = POD[name]
    with pytest.raises(RuntimeError) as raised:
        check_no_secrets({"docs/index.html": f"<p>x {value} y</p>".encode()}, POD)
    text = str(raised.value)
    assert name in text and "docs/index.html" in text
    assert value not in text


@pytest.mark.parametrize("name", ["LAGOM_R2_ENDPOINT", "LAGOM_DASHBOARDS_ENDPOINT"])
def test_the_leak_guard_finds_an_endpoints_host_without_its_scheme(name: str) -> None:
    host = POD[name].removeprefix("https://")
    with pytest.raises(RuntimeError, match=name) as raised:
        check_no_secrets({"a.html": f"<a>{host}/path</a>".encode()}, POD)
    assert host not in str(raised.value)


def test_the_leak_guard_ignores_a_variable_that_is_unset_or_empty() -> None:
    # An empty value would match every page.
    environ = {**POD, "PGPASSWORD": "", "HEARTBEAT_URL": ""}
    del environ["LAGOM_R2_ENDPOINT"]
    check_no_secrets({"a.html": b"<html>any text</html>"}, environ)


# The trigger ----------------------------------------------------------------


@op
def _fine() -> None:
    pass


@job(name="usgs")
def _usgs_succeeds() -> None:
    _fine()


def test_a_successful_usgs_run_requests_one_dashboards_run() -> None:
    usgs = define_asset_job("usgs", selection="*")
    render = define_asset_job("dashboards", selection="*")
    sensor = build_render_trigger(usgs, render)
    assert sensor.default_status == DefaultSensorStatus.RUNNING
    assert [j.name for j in sensor._monitored_jobs] == ["usgs"]
    assert sensor.job_name == "dashboards"

    instance = DagsterInstance.ephemeral()
    result = _usgs_succeeds.execute_in_process(instance=instance)
    context = build_run_status_sensor_context(
        sensor_name=sensor.name,
        dagster_instance=instance,
        dagster_run=result.dagster_run,
        dagster_event=result.get_run_success_event(),
    )
    request = sensor(context)
    assert isinstance(request, RunRequest)
    # The usgs run's ID: the same success asked twice requests one run.
    assert request.run_key == result.dagster_run.run_id


def test_the_memory_peak_is_read_from_the_cgroup_in_mib(tmp_path: Path) -> None:
    peak = tmp_path / "memory.peak"
    peak.write_text("805306368\n")
    assert memory_peak_mib(peak) == 768.0


def test_a_kernel_without_the_memory_peak_gives_none(tmp_path: Path) -> None:
    assert memory_peak_mib(tmp_path / "missing") is None
    garbled = tmp_path / "memory.peak"
    garbled.write_text("max\n")
    assert memory_peak_mib(garbled) is None
