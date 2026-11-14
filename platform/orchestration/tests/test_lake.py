import psycopg
import pytest

from lagom_orchestration.lake import (
    _execute,
    _literal,
    grant_reader_access,
    maintenance_statements,
    reader_grant_statements,
)


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


def test_reader_grants_cover_what_exists_and_what_is_created_later() -> None:
    assert reader_grant_statements("ducklake") == [
        'GRANT USAGE ON SCHEMA "ducklake" TO "lake_reader"',
        'GRANT SELECT ON ALL TABLES IN SCHEMA "ducklake" TO "lake_reader"',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA "ducklake" GRANT SELECT ON TABLES TO "lake_reader"',
    ]


def test_reader_grants_quote_their_identifiers() -> None:
    assert 'SCHEMA "a""b"' in reader_grant_statements('a"b')[0]


class _Postgres:
    """Stands in for psycopg.connect: records statements, fails on request."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.statements: list[str] = []
        self.uri = ""

    def connect(self, uri: str) -> "_Postgres":
        self.uri = uri
        return self

    def __enter__(self) -> "_Postgres":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, statement: str) -> None:
        if self.error:
            raise self.error
        self.statements.append(statement)


@pytest.fixture
def postgres(monkeypatch) -> _Postgres:  # type: ignore[no-untyped-def]
    fake = _Postgres()
    monkeypatch.setattr("lagom_orchestration.lake.psycopg.connect", fake.connect)
    monkeypatch.setenv("PG_URI", "postgresql://lagom:s3cret-password@postgres-rw:5432/lagom")
    monkeypatch.setenv("LAGOM_LAKE_METADATA_SCHEMA", "ducklake")
    return fake


def test_grant_reader_access_runs_the_grants_as_the_owner(postgres: _Postgres) -> None:
    grant_reader_access()
    assert postgres.uri.startswith("postgresql://lagom:")
    assert postgres.statements == reader_grant_statements("ducklake")


def test_grant_reader_access_fails_loudly_when_the_role_does_not_exist(
    postgres: _Postgres,
) -> None:
    postgres.error = psycopg.errors.UndefinedObject('role "lake_reader" does not exist')
    with pytest.raises(RuntimeError, match="lake_reader does not exist.*lake-reader-credentials"):
        grant_reader_access()


def test_grant_reader_access_does_not_swallow_other_errors(postgres: _Postgres) -> None:
    postgres.error = psycopg.errors.InvalidSchemaName('schema "ducklake" does not exist')
    with pytest.raises(RuntimeError, match='schema "ducklake" does not exist'):
        grant_reader_access()


def test_grant_reader_access_keeps_the_password_out_of_an_error(postgres: _Postgres) -> None:
    postgres.error = psycopg.OperationalError(
        'connection failed for "postgresql://lagom:s3cret-password@postgres-rw:5432/lagom"'
    )
    with pytest.raises(RuntimeError, match="postgres-rw") as raised:
        grant_reader_access()
    assert "s3cret-password" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_grant_reader_access_needs_the_owner_uri(postgres: _Postgres, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("PG_URI")
    with pytest.raises(RuntimeError, match="PG_URI is not set"):
        grant_reader_access()


class _Recorder:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def close(self) -> None:
        return None


def _lake_env(monkeypatch, catalog: str) -> None:  # type: ignore[no-untyped-def]
    for name, value in {
        "LAGOM_LAKE_CATALOG": catalog,
        "LAGOM_LAKE_DATA_PATH": "data/",
        "LAGOM_LAKE_METADATA_SCHEMA": "ducklake",
        "LAGOM_R2_ACCESS_KEY_ID": "test",
        "LAGOM_R2_SECRET_ACCESS_KEY": "test",
        "LAGOM_R2_ENDPOINT": "https://test.invalid",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(("read_only", "option"), [(False, False), (True, True)])
def test_attach_adds_the_read_only_option_only_when_asked(  # type: ignore[no-untyped-def]
    monkeypatch, read_only: bool, option: bool
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr("lagom_orchestration.lake.duckdb.connect", lambda: recorder)
    # No password in the string: libpq reads PGPASSWORD (ADR-0044).
    _lake_env(monkeypatch, "ducklake:postgres:host=postgres-rw dbname=lagom user=lake_reader")
    from lagom_orchestration.lake import attach_lake

    attach_lake(read_only=read_only)
    attach = recorder.statements[-1]
    assert attach.startswith("ATTACH 'ducklake:postgres:host=postgres-rw dbname=lagom")
    assert attach.endswith(", READ_ONLY)") is option
    assert "password" not in attach.lower()


def test_a_read_only_attach_cannot_write(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """On a real DuckLake: the first session creates it, the second only reads."""
    import duckdb

    from lagom_orchestration.lake import attach_lake

    monkeypatch.setenv("HOME", str(tmp_path))
    _lake_env(monkeypatch, f"ducklake:{tmp_path / 'catalog.ducklake'}")
    monkeypatch.setenv("LAGOM_LAKE_DATA_PATH", f"{tmp_path / 'data'}/")
    monkeypatch.setenv("LAGOM_LAKE_METADATA_SCHEMA", "main")
    writer = attach_lake()
    writer.execute("CREATE TABLE lake.main.t (v INTEGER)")
    writer.execute("INSERT INTO lake.main.t VALUES (1)")
    writer.close()
    reader = attach_lake(read_only=True)
    assert reader.execute("SELECT count(*) FROM lake.main.t").fetchone() == (1,)
    with pytest.raises(duckdb.Error):
        reader.execute("INSERT INTO lake.main.t VALUES (2)")
    with pytest.raises(duckdb.Error):
        reader.execute("CREATE TABLE lake.main.u (v INTEGER)")
    reader.close()
