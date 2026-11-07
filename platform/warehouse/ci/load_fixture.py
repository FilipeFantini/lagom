"""Load ci/bronze_usgs_events.sql into the DuckLake the CI build uses.

Reads the same LAGOM_LAKE_* variables as the dbt `lake` target, so the fixture
lands in the catalog dbt will attach. The catalog is a local file here; nothing
reaches R2. Safe to run twice: the table is replaced, not appended to.
"""

import os
import sys
from pathlib import Path

import duckdb


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    catalog = os.environ["LAGOM_LAKE_CATALOG"]
    data_path = os.environ["LAGOM_LAKE_DATA_PATH"]
    schema = os.environ["LAGOM_LAKE_METADATA_SCHEMA"]
    sql = (Path(__file__).parent / "bronze_usgs_events.sql").read_text()

    connection = duckdb.connect()
    connection.execute("INSTALL ducklake; LOAD ducklake")
    # ATTACH takes no parameters, so the values are quoted as SQL literals.
    connection.execute(
        f"ATTACH {_literal(catalog)} AS lake "
        f"(DATA_PATH {_literal(data_path)}, METADATA_SCHEMA {_literal(schema)})"
    )
    connection.execute("USE lake")
    connection.execute(sql)
    rows = connection.execute("select count(*) from bronze.usgs_events").fetchone()
    print(f"fixture loaded: {rows[0]} rows in bronze.usgs_events", file=sys.stderr)


if __name__ == "__main__":
    main()
