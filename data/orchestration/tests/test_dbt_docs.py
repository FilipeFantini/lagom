"""dbt's documentation page: how it is generated and what refuses it.

`dbt docs generate` is replaced by a function that writes the three files it
would write, from small documents the tests shape, so the real code around it
runs: the arguments and environment of the command, and every check on what
comes out. Nothing opens a lake or a network connection.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lagom_orchestration import dbt_docs
from lagom_orchestration.dashboards import SUBPROCESS_ENV
from lagom_orchestration.dbt_docs import (
    DOCS_TIMEOUT_SECONDS,
    ICON_LINK,
    external_loads,
    generate_docs,
    set_icon,
)

MODEL = "model.lagom.fct_earthquakes"
POD = {
    "PATH": "/usr/bin",
    "HOME": "/tmp",
    "PGPASSWORD": "reader-password",
    "LAGOM_LAKE_CATALOG": "ducklake:postgres:host=postgres-rw user=lake_reader",
    "LAGOM_R2_ENDPOINT": "https://lake.invalid",
    "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY": "bucket-secret",
    "HEARTBEAT_URL": "https://hc-ping.invalid/ping",
}


def _manifest(tracking: object = False) -> dict[str, Any]:
    return {
        "metadata": {"send_anonymous_usage_stats": tracking},
        "nodes": {
            MODEL: {"unique_id": MODEL, "resource_type": "model", "config": {"enabled": True}},
            "test.lagom.not_null": {
                "unique_id": "test.lagom.not_null",
                "resource_type": "test",
                "config": {"enabled": True},
            },
        },
    }


def _catalog() -> dict[str, Any]:
    return {"errors": None, "nodes": {MODEL: {"columns": {"event_id": {"type": "text"}}}}}


# The icon link dbt 1.12's static page ships: a webpack expression never built.
DBT_ICON = """<link rel="shortcut icon" href="${require('./assets/favicons/favicon.ico')}"/>"""


# A page shaped like dbt's: its unbuilt icon link, the embedded manifest says
# what it says, and the script holds URLs as text, which load nothing.
def _page(tracking: str = "false", markup: str = "", icon: str = DBT_ICON) -> str:
    return (
        f"<html><head>{icon}</head><body>"
        f'<a href="https://example.invalid/docs">docs</a>{markup}'
        '<script>var home = "https://cdn.invalid/sp.js"; '
        f'var metadata = {{"send_anonymous_usage_stats": {tracking}}};</script>'
        "</body></html>"
    )


class FakeDbt:
    """Stands for `dbt docs generate`: records the call and writes the files it would."""

    def __init__(self, manifest: Any = None, catalog: Any = None, page: Any = None) -> None:
        self.manifest = _manifest() if manifest is None else manifest
        self.catalog = _catalog() if catalog is None else catalog
        self.page = _page() if page is None else page
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, name: str, args: list[str], *, env: dict[str, str], cwd: Path, timeout: int
    ) -> str:
        self.calls.append({"name": name, "args": args, "env": env, "cwd": cwd, "timeout": timeout})
        target = Path(args[args.index("--target-path") + 1])
        target.mkdir(parents=True)
        (target / "manifest.json").write_text(json.dumps(self.manifest))
        (target / "catalog.json").write_text(json.dumps(self.catalog))
        (target / "static_index.html").write_text(self.page)
        return ""


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def _generate(work: Path, monkeypatch: pytest.MonkeyPatch, fake: FakeDbt) -> bytes:
    monkeypatch.setattr(dbt_docs, "run_command", fake)
    return generate_docs(Path("/repo"), work, POD)


def test_the_page_is_returned_when_every_check_passes(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDbt()
    assert _generate(work, monkeypatch, fake) == _page(icon=ICON_LINK).encode()


def test_dbts_icon_link_is_replaced_by_the_embedded_icon() -> None:
    html = set_icon(_page())
    assert DBT_ICON not in html and "require(" not in html
    assert html.count(ICON_LINK) == 1
    assert ICON_LINK.startswith('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,')
    # The rest of the page is untouched.
    assert html.replace(ICON_LINK, DBT_ICON) == _page()
    # An embedded icon is not a load from outside.
    assert external_loads(html) == []


@pytest.mark.parametrize("icon", ['<link rel="icon" href="favicon.ico">', DBT_ICON * 2])
def test_a_page_without_dbts_icon_link_exactly_once_fails(
    work: Path, monkeypatch: pytest.MonkeyPatch, icon: str
) -> None:
    with pytest.raises(RuntimeError, match="icon link (0|2) times, not once"):
        _generate(work, monkeypatch, FakeDbt(page=_page(icon=icon)))


def test_the_command_reads_the_dashboards_profile_and_writes_only_under_the_work_dir(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDbt()
    _generate(work, monkeypatch, fake)
    (call,) = fake.calls
    args = call["args"]
    assert args[:4] == ["dbt", "docs", "generate", "--static"]

    def option(name: str) -> str:
        return args[args.index(name) + 1]

    assert option("--profile") == "lagom_dashboards"
    assert option("--profiles-dir") == "/repo/data/dashboards"
    assert option("--target") == "lake"
    assert option("--project-dir") == "/repo/data/warehouse"
    # The project is read-only at run time: the target and the log are in `work`.
    assert Path(option("--target-path")).is_relative_to(work)
    assert Path(option("--log-path")).is_relative_to(work)
    assert call["cwd"] == work
    assert call["timeout"] == DOCS_TIMEOUT_SECONDS == 240
    assert call["name"] == "dbt docs generate"


def test_dbt_gets_the_lakes_variables_and_do_not_track_and_nothing_else(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeDbt()
    _generate(work, monkeypatch, fake)
    env = fake.calls[0]["env"]
    assert env["DO_NOT_TRACK"] == "1"
    assert set(env) - {"DO_NOT_TRACK"} <= set(SUBPROCESS_ENV)
    assert env["PGPASSWORD"] == "reader-password"
    assert "LAGOM_DASHBOARDS_SECRET_ACCESS_KEY" not in env and "HEARTBEAT_URL" not in env


@pytest.mark.parametrize("tracking", [True, None, "false"])
def test_a_manifest_that_does_not_say_false_fails(
    work: Path, monkeypatch: pytest.MonkeyPatch, tracking: object
) -> None:
    with pytest.raises(RuntimeError, match="send_anonymous_usage_stats is not false"):
        _generate(work, monkeypatch, FakeDbt(manifest=_manifest(tracking)))


@pytest.mark.parametrize(
    "page", [_page(tracking="true"), f"<html>{DBT_ICON}no setting at all</html>"]
)
def test_a_page_that_would_load_a_tracker_or_does_not_say_otherwise_fails(
    work: Path, monkeypatch: pytest.MonkeyPatch, page: str
) -> None:
    with pytest.raises(RuntimeError, match="would load a tracker"):
        _generate(work, monkeypatch, FakeDbt(page=page))


@pytest.mark.parametrize(
    "catalog",
    [{"errors": None, "nodes": {}}, {"errors": None, "nodes": {MODEL: {"columns": {}}}}, {}],
)
def test_a_model_without_columns_in_the_catalog_fails_by_name(
    work: Path, monkeypatch: pytest.MonkeyPatch, catalog: dict[str, Any]
) -> None:
    with pytest.raises(RuntimeError, match=f"no columns for: {MODEL}"):
        _generate(work, monkeypatch, FakeDbt(catalog=catalog))


def test_a_disabled_model_needs_no_columns(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    manifest["nodes"][MODEL]["config"]["enabled"] = False
    _generate(work, monkeypatch, FakeDbt(manifest=manifest, catalog={"errors": None, "nodes": {}}))


def test_catalog_errors_fail(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = {**_catalog(), "errors": ["could not describe fct_earthquakes"]}
    with pytest.raises(RuntimeError, match="catalog.json has errors"):
        _generate(work, monkeypatch, FakeDbt(catalog=catalog))


def test_a_missing_output_fails_by_name(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class NoPage(FakeDbt):
        def __call__(self, name: str, args: list[str], **kwargs: Any) -> str:
            super().__call__(name, args, **kwargs)
            (Path(args[args.index("--target-path") + 1]) / "static_index.html").unlink()
            return ""

    with pytest.raises(RuntimeError, match="did not write static_index.html"):
        _generate(work, monkeypatch, NoPage())


@pytest.mark.parametrize(
    "markup",
    [
        '<script src="https://cdn.invalid/sp.js"></script>',
        '<script src="//cdn.invalid/sp.js"></script>',
        '<link rel="stylesheet" href="HTTP://cdn.invalid/a.css">',
        '<img src="https://cdn.invalid/p.png">',
        '<iframe src="https://cdn.invalid/"></iframe>',
        '<object data="https://cdn.invalid/x"></object>',
        '<video><source src="https://cdn.invalid/v.mp4"></video>',
    ],
)
def test_markup_that_loads_from_outside_fails(
    work: Path, monkeypatch: pytest.MonkeyPatch, markup: str
) -> None:
    with pytest.raises(RuntimeError, match="loads from outside"):
        _generate(work, monkeypatch, FakeDbt(page=_page(markup=markup)))


def test_relative_references_and_urls_in_script_text_are_not_loads() -> None:
    html = _page(
        markup=(
            '<link rel="stylesheet" href="style.css"><img src="data:image/png;base64,AAAA">'
            '<script src="app.js"></script><a href="https://example.invalid/">out</a>'
            '<img src="#self"><iframe src="about:blank"></iframe>'
        )
    )
    assert external_loads(html) == []


def test_external_loads_names_each_finding() -> None:
    found = external_loads(
        '<script src="https://a.invalid/x.js"></script><IMG SRC="//b.invalid/y.png">'
    )
    assert found == ["<script src='https://a.invalid/x.js'>", "<img src='//b.invalid/y.png'>"]


def test_the_command_line_writes_the_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dbt_docs, "run_command", FakeDbt())
    output = tmp_path / "out" / "docs" / "index.html"
    assert dbt_docs.main([str(tmp_path), str(output)]) == 0
    assert output.read_bytes() == _page(icon=ICON_LINK).encode()
    assert dbt_docs.main([]) == 2
