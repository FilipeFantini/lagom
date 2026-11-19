"""The boards read the lake through a read-only attach (ADR-0044).

`profiles.yml` is what `dct` connects with. dbt Charts is a tool we do not
control and the render runs with the lake's credentials, so the attach itself
must refuse a write, apart from what the catalog's role allows. The test
builds the ATTACH statement the profile produces, with dbt-duckdb's own code,
and runs it against a real DuckLake in a directory.
"""

import os
from pathlib import Path
from typing import Any

import duckdb
import jinja2
import pytest
import yaml
from dbt.adapters.duckdb.credentials import Attachment
from lagom_orchestration.lake import attach_lake

PROFILES = Path(__file__).parents[1] / "profiles.yml"


def _attachment(overrides: dict[str, Any] | None = None) -> Attachment:
    """The profile's one attach, its Jinja resolved from the environment as `dct` does."""
    profile = yaml.safe_load(PROFILES.read_text())
    (attach,) = profile["lagom_dashboards"]["outputs"]["lake"]["attach"]
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.globals["env_var"] = lambda name: os.environ[name]

    def render(value: Any) -> Any:
        if isinstance(value, str):
            return environment.from_string(value).render()
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value

    resolved = {**render(attach), **(overrides or {})}
    return Attachment(**resolved)


@pytest.fixture
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DuckLake with one table, made by a writer, as the pipeline would."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LAGOM_LAKE_CATALOG", f"ducklake:{tmp_path / 'catalog.ducklake'}")
    monkeypatch.setenv("LAGOM_LAKE_DATA_PATH", f"{tmp_path / 'data'}/")
    monkeypatch.setenv("LAGOM_LAKE_METADATA_SCHEMA", "main")
    monkeypatch.setenv("LAGOM_R2_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("LAGOM_R2_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("LAGOM_R2_ENDPOINT", "https://test.invalid")
    writer = attach_lake()
    writer.execute("CREATE TABLE lake.main.t (v INTEGER)")
    writer.execute("INSERT INTO lake.main.t VALUES (1)")
    writer.close()


def _connect_through(attachment: Attachment) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    for extension in ("ducklake", "httpfs"):
        connection.execute(f"INSTALL {extension}; LOAD {extension}")
    connection.execute(attachment.to_sql())
    return connection


def test_the_profile_attaches_the_lake_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the statement's text: nothing is opened.
    for name in ("LAGOM_LAKE_CATALOG", "LAGOM_LAKE_DATA_PATH", "LAGOM_LAKE_METADATA_SCHEMA"):
        monkeypatch.setenv(name, "x")
    assert "READ_ONLY" in _attachment().to_sql()


def test_a_write_through_the_profiles_attach_is_refused(lake: None) -> None:
    reader = _connect_through(_attachment())
    assert reader.execute("SELECT count(*) FROM lake.main.t").fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        reader.execute("INSERT INTO lake.main.t VALUES (2)")
    with pytest.raises(duckdb.Error):
        reader.execute("CREATE TABLE lake.main.u (v INTEGER)")
    reader.close()


def test_the_same_attach_without_the_option_can_write(lake: None) -> None:
    """The control: if this could not write, the test above would prove nothing."""
    writer = _connect_through(_attachment({"read_only": False}))
    writer.execute("INSERT INTO lake.main.t VALUES (2)")
    assert writer.execute("SELECT count(*) FROM lake.main.t").fetchone() == (2,)
    writer.close()
