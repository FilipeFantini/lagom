"""DuckLake maintenance: compaction and file cleanup (ADR-0023, ADR-0026).

Each 30-minute run writes a few small Parquet files. Merging adjacent files
keeps reads fast and the R2 operation count low; expiring old snapshots and
deleting the files only they referenced keeps the bucket from growing with
history nobody reads.

The same module gives the read-only catalog role its privileges (ADR-0044), as
the catalog's owner, because the owner is the one who creates the tables.
"""

import os

import duckdb
import psycopg

from lagom_orchestration.redact import redact_secrets

# Snapshots younger than this stay, so a recent version of any table can
# still be read (time travel) and a reader in flight never loses its files.
SNAPSHOT_RETENTION = "7 days"

# The catalog's read-only login (ADR-0044). CloudNativePG creates it from
# `spec.managed.roles` in postgres-<env>/cluster.yaml; the grants below are
# the owner's to give, so they live here.
READER_ROLE = "lake_reader"


def maintenance_statements(alias: str = "lake") -> list[str]:
    """The SQL run against an attached DuckLake, in order."""
    name = _literal(alias)
    retention = _literal(SNAPSHOT_RETENTION)
    return [
        f"CALL ducklake_merge_adjacent_files({name})",
        f"CALL ducklake_expire_snapshots({name}, older_than => now() - INTERVAL {retention})",
        f"CALL ducklake_cleanup_old_files({name}, cleanup_all => true)",
    ]


def _execute(connection: duckdb.DuckDBPyConnection, statement: str) -> None:
    """Run `statement`. An error that contains a credential is rewritten without it.

    DuckDB includes the catalog URI in an attach failure. Re-raising that
    exception would keep the password in Dagster's event log and, from there,
    in the alarm body. The rewritten error is not chained to the original.
    """
    try:
        connection.execute(statement)
    except Exception as error:
        message = redact_secrets(str(error))
        if message == str(error):
            raise
        raise RuntimeError(message) from None


def run_maintenance(connection: duckdb.DuckDBPyConnection, alias: str = "lake") -> None:
    for statement in maintenance_statements(alias):
        _execute(connection, statement)


def reader_grant_statements(schema: str, role: str = READER_ROLE) -> list[str]:
    """The SQL that lets `role` read the DuckLake metadata tables in `schema`.

    Postgres grants are not inherited by tables created later. DuckLake creates
    tables in its metadata schema as it goes (a table of inlined rows per
    table and schema version), so the last statement sets the default for
    what the owner creates from now on, and the second covers what exists.
    Each statement can run again: a grant already given changes nothing.
    """
    schema, role = _identifier(schema), _identifier(role)
    return [
        f"GRANT USAGE ON SCHEMA {schema} TO {role}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {role}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO {role}",
    ]


def grant_reader_access() -> None:
    """Give the read-only role its privileges on the catalog, as the catalog's owner.

    Runs against Postgres directly, not through DuckDB's attach, as the owner
    in PG_URI (libpq reads PGSSLMODE from the environment). One transaction:
    all three grants or none. A role that does not exist stops the job with a
    message that says so, because the cluster has not been given it yet (the
    sealed Secret `lake-reader-credentials` is missing) and the operator must
    see that; nothing here creates the role or skips the step.
    """
    uri = _require("PG_URI")
    statements = reader_grant_statements(_require("LAGOM_LAKE_METADATA_SCHEMA"))
    try:
        with psycopg.connect(uri) as connection:
            for statement in statements:
                connection.execute(statement)
    except psycopg.errors.UndefinedObject as error:
        # The same error class names any missing object, so the hint is given
        # only when Postgres says the missing one is this role.
        if f'role "{READER_ROLE}" does not exist' not in str(error):
            raise RuntimeError(redact_secrets(str(error))) from None
        raise RuntimeError(
            f"the role {READER_ROLE} does not exist in the catalog's Postgres: seal its "
            "Secret lake-reader-credentials so CloudNativePG creates it (ADR-0044)"
        ) from None
    except psycopg.Error as error:
        # A failed connection can name the URI; the exception is not chained.
        raise RuntimeError(redact_secrets(str(error))) from None


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"environment variable {name} is not set")
    return value


def _identifier(value: str) -> str:
    """A quoted SQL identifier."""
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    """A SQL string literal. DuckDB takes no parameters in CREATE SECRET or ATTACH."""
    return "'" + value.replace("'", "''") + "'"


def attach_lake(alias: str = "lake", *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the environment's DuckLake attached.

    `read_only` adds DuckDB's READ_ONLY attach option: the session writes
    nothing to the catalog or the data path, whatever the credentials allow.
    The catalog string is whatever LAGOM_LAKE_CATALOG says, so a reader can
    leave the password out of it and let libpq read PGPASSWORD (ADR-0044).

    Reads the same variables as the dbt `lake` target (data/warehouse/
    profiles.yml), so maintenance and transformation see the same catalog.
    The statements hold the R2 key and the catalog's password. DuckDB echoes
    the catalog URI when attach fails; `_execute` redacts that before the
    exception can reach a log or the alarm.
    """
    connection = duckdb.connect()
    for extension in ("ducklake", "httpfs", "postgres"):
        _execute(connection, f"INSTALL {extension}; LOAD {extension}")
    key_id = _literal(_require("LAGOM_R2_ACCESS_KEY_ID"))
    secret = _literal(_require("LAGOM_R2_SECRET_ACCESS_KEY"))
    endpoint = _literal(_require("LAGOM_R2_ENDPOINT").removeprefix("https://"))
    _execute(
        connection,
        f"CREATE SECRET r2 (TYPE s3, KEY_ID {key_id}, SECRET {secret}, "
        f"ENDPOINT {endpoint}, REGION 'auto', URL_STYLE 'path')",
    )
    catalog = _literal(_require("LAGOM_LAKE_CATALOG"))
    data_path = _literal(_require("LAGOM_LAKE_DATA_PATH"))
    metadata_schema = _literal(_require("LAGOM_LAKE_METADATA_SCHEMA"))
    options = f"DATA_PATH {data_path}, METADATA_SCHEMA {metadata_schema}"
    if read_only:
        options += ", READ_ONLY"
    _execute(connection, f"ATTACH {catalog} AS {alias} ({options})")
    return connection
