import pytest

from lagom_orchestration.lake import _execute, _literal, maintenance_statements


def test_maintenance_runs_merge_then_expiry_then_cleanup() -> None:
    statements = maintenance_statements("lake")
    assert [s.split("(")[0] for s in statements] == [
        "CALL ducklake_merge_adjacent_files",
        "CALL ducklake_expire_snapshots",
        "CALL ducklake_cleanup_old_files",
    ]
    assert "INTERVAL '7 days'" in statements[1]


def test_literal_escapes_quotes() -> None:
    assert _literal("it's") == "'it''s'"


def test_maintenance_quotes_the_alias() -> None:
    assert maintenance_statements("lake")[0] == "CALL ducklake_merge_adjacent_files('lake')"
    assert "o''lake" in maintenance_statements("o'lake")[0]


class _Connection:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def execute(self, statement: str) -> None:
        raise self.error


def test_execute_rewrites_an_error_that_contains_a_password() -> None:
    error = RuntimeError(
        'Unable to connect at "postgresql://lagom:s3cret-password@127.0.0.1:5432/lagom"'
    )
    with pytest.raises(RuntimeError, match="127.0.0.1") as raised:
        _execute(_Connection(error), "ATTACH")  # type: ignore[arg-type]
    assert "s3cret-password" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_execute_keeps_an_error_without_a_secret() -> None:
    with pytest.raises(ValueError, match="disk full"):
        _execute(_Connection(ValueError("disk full")), "SELECT 1")  # type: ignore[arg-type]


def test_maintenance_runs_on_a_real_ducklake(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Attach a DuckLake on disk the way a run does, write to it, and maintain it.

    The R2 secret is created but unused: the data path is local.
    """
    from lagom_orchestration.lake import attach_lake, run_maintenance

    for name, value in {
        "HOME": str(tmp_path),  # DuckDB installs its extensions under HOME
        "LAGOM_LAKE_CATALOG": f"ducklake:{tmp_path / 'catalog.ducklake'}",
        "LAGOM_LAKE_DATA_PATH": f"{tmp_path / 'data'}/",
        "LAGOM_LAKE_METADATA_SCHEMA": "main",
        "LAGOM_R2_ACCESS_KEY_ID": "test",
        "LAGOM_R2_SECRET_ACCESS_KEY": "test",
        "LAGOM_R2_ENDPOINT": "https://test.invalid",
    }.items():
        monkeypatch.setenv(name, value)
    connection = attach_lake()
    for value in range(3):
        connection.execute("CREATE TABLE IF NOT EXISTS lake.main.t (v INTEGER)")
        connection.execute(f"INSERT INTO lake.main.t VALUES ({value})")
    run_maintenance(connection)
    assert connection.execute("SELECT count(*) FROM lake.main.t").fetchone() == (3,)
    connection.close()


def test_a_failed_attach_does_not_keep_the_catalog_password(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """DuckDB 1.5.6 echoes the catalog URI. The password must not survive the error."""
    from lagom_orchestration.lake import attach_lake

    for name, value in {
        "HOME": str(tmp_path),
        "LAGOM_LAKE_CATALOG": (
            "ducklake:postgres:postgresql://lagom:s3cret-password@127.0.0.1:1/lagom"
        ),
        "LAGOM_LAKE_DATA_PATH": f"{tmp_path / 'data'}/",
        "LAGOM_LAKE_METADATA_SCHEMA": "main",
        "LAGOM_R2_ACCESS_KEY_ID": "AKIAtestkey",
        "LAGOM_R2_SECRET_ACCESS_KEY": "supersecretvalue",
        "LAGOM_R2_ENDPOINT": "https://test.invalid",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError) as raised:
        attach_lake()
    text = str(raised.value)
    assert "s3cret-password" not in text
    assert "supersecretvalue" not in text
    assert "AKIAtestkey" not in text
    assert "127.0.0.1" in text
    assert raised.value.__cause__ is None
