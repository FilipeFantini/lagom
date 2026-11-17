"""The streaming showcase's archive and the live table's retention.

ADR-0045 (decisions 7 and 8) and ADR-0047 (decision 6): the events in
`serving.live.events` are copied to the lake, as Parquet in DuckLake, and a
row leaves the live table only after its copy is confirmed in the lake.

**Windows.** The archive works in windows of one hour of `received_at`, in
UTC. `received_at` is when the hub stored the event, and a replay from the hub
writes the same value again, so a window of it closes: once its hour is past,
only a replay can add a row to it. A window of `collected_at` never closes,
since the Pi delivers an outage's events hours late.

**Idempotency.** An event is identified by `event_id`. A window's rows are
inserted into the lake only where their `event_id` is not there yet, so a
second run, a replay of the hub, or a run that failed half-way inserts nothing
twice. The lookup is bounded by `collected_at`, which the identifier is made
of (ADR-0045, decision 4), so it reads only the lake files of those minutes.

**Confirmation.** After the insert, every row of the window in the live table
must be found in the lake by its `event_id`, and no `event_id` may be in the
lake twice. Anything else stops the run, with the window and the counts named,
before anything is deleted.

**Retention.** A window whose every row is older than `LIVE_RETENTION` is
deleted from the live table, by the `event_id`s just confirmed and nothing
else, in one transaction that rolls back unless it deleted exactly those. A
row that a replay adds meanwhile is not among them: it stays and is archived
by the next run. The lake keeps the raw events for `LAKE_RETENTION`, in whole
UTC days, then they are deleted from it too; `lake_maintenance` later removes
their files from R2.
"""

import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import duckdb
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import TupleRow

from lagom_orchestration.lake import _execute, _literal, _require, attach_lake
from lagom_orchestration.redact import redact_secrets

WINDOW = timedelta(hours=1)
# The live table keeps what the hub keeps (ADR-0047, decision 4): 7 days, so
# it can be rebuilt from the hub. A window is deleted once all of it is older,
# so the table holds 7 days and at most one more hour, which is in the lake.
# tests/test_archive.py fails if the hub's stream keeps less.
LIVE_RETENTION = timedelta(days=7)
# The raw events in the lake (ADR-0045, decision 8), sized against the
# showcase's 1 GB of R2 in architecture.md section 11.
LAKE_RETENTION = timedelta(days=30)
# A window is archived once its hour ended this long ago: the writer stores an
# event about a second after the hub. A row that comes later is archived by
# the next run, so this only saves work.
GRACE = timedelta(minutes=10)
# DuckDB's share of the run's 1 GiB, beside Dagster's process; above it DuckDB
# spills to /tmp (an emptyDir of 1 GiB) instead of growing.
DUCKDB_MEMORY_LIMIT = "256MB"

LIVE_TABLE = "live.events"
LAKE_SCHEMA = "bronze"
LAKE_TABLE = "streaming_events"
COLUMNS = ("event_id", "source", "signal", "collected_at", "received_at", "payload")
# The origin of the hour windows, so Postgres cuts them in UTC whatever the
# session's time zone.
_ORIGIN = "2000-01-01 00:00:00+00"


@dataclass(frozen=True)
class WindowResult:
    """What one window held, and what the run did with it."""

    start: datetime
    live: int
    lake_before: int
    inserted: int
    lake_after: int
    deleted: int

    def describe(self) -> str:
        return (
            f"window {self.start:%Y-%m-%dT%H:%MZ}: live {self.live}, lake before "
            f"{self.lake_before}, inserted {self.inserted}, lake after {self.lake_after}, "
            f"deleted from live {self.deleted}"
        )


@dataclass(frozen=True)
class ArchiveReport:
    windows: list[WindowResult]
    lake_deleted: int
    lake_rows: int
    lake_bytes: int
    bytes_by_day: dict[str, int]


class ArchiveMismatch(RuntimeError):
    """The lake does not hold what the live table holds: nothing is deleted."""


def floor_hour(moment: datetime) -> datetime:
    # A naive time would be read as the machine's local time: refused.
    if moment.tzinfo is None:
        raise ValueError(f"{moment} has no time zone")
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def floor_day(moment: datetime) -> datetime:
    return floor_hour(moment).replace(hour=0)


def closed_before(now: datetime) -> datetime:
    """Windows that end at or before this instant are closed."""
    return floor_hour(now - GRACE)


def is_expired(start: datetime, now: datetime) -> bool:
    """Every row of the window that starts at `start` is older than LIVE_RETENTION."""
    return start + WINDOW <= now - LIVE_RETENTION


def lake_cutoff(now: datetime) -> datetime:
    """Raw events received before this instant leave the lake: whole UTC days."""
    return floor_day(now) - LAKE_RETENTION


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _lake() -> str:
    return f"lake.{LAKE_SCHEMA}.{LAKE_TABLE}"


# Set on the lake's table only; the other tables keep DuckLake's defaults.
# No inlining: DuckLake would keep a small insert (a window with a few
# replayed rows) in the catalog's tables, which are in Postgres and, in prod,
# in its backup; every row goes to a Parquet file instead.
TABLE_OPTIONS = {"parquet_compression": "zstd", "data_inlining_row_limit": "0"}


def prepare_lake(duck: duckdb.DuckDBPyConnection) -> None:
    """Create the lake's table if it is missing, and hold its options.

    Partitioned by the UTC day of `received_at`, so a day leaves the lake as
    whole files, which the maintenance job then deletes from R2, and the hourly
    files of a day are merged into one by `ducklake_merge_adjacent_files`. The
    table and its partitioning are one transaction. DuckLake refuses an option
    on a table created in the same transaction, so the options are checked on
    every run and set when they differ: a run stopped between the two cannot
    leave the table without them.
    """
    _execute(duck, f"CREATE SCHEMA IF NOT EXISTS lake.{LAKE_SCHEMA}")
    exists = duck.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE database_name = 'lake' AND schema_name = ? AND table_name = ?",
        [LAKE_SCHEMA, LAKE_TABLE],
    ).fetchone()
    if not (exists and exists[0]):
        _execute(duck, "BEGIN")
        _execute(
            duck,
            f"CREATE TABLE {_lake()} ("
            "event_id VARCHAR NOT NULL, source VARCHAR NOT NULL, signal VARCHAR NOT NULL, "
            "collected_at TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL, "
            "payload JSON NOT NULL)",
        )
        _execute(
            duck,
            f"ALTER TABLE {_lake()} SET PARTITIONED BY "
            "(year(received_at), month(received_at), day(received_at))",
        )
        _execute(duck, "COMMIT")
    current = dict(
        duck.execute(
            "SELECT option_name, value FROM lake.options() "
            "WHERE scope = 'TABLE' AND scope_entry = ?",
            [f"{LAKE_SCHEMA}.{LAKE_TABLE}"],
        ).fetchall()
    )
    table = f"schema => {_literal(LAKE_SCHEMA)}, table_name => {_literal(LAKE_TABLE)}"
    for name, value in TABLE_OPTIONS.items():
        if current.get(name) != value:
            _execute(duck, f"CALL lake.set_option({_literal(name)}, {_literal(value)}, {table})")


def _postgres_query(sql: str) -> str:
    """A DuckDB expression that runs `sql` in the attached `serving` and returns its rows."""
    return f"postgres_query('serving', {_literal(sql)})"


def live_window_counts(duck: duckdb.DuckDBPyConnection, before: datetime) -> dict[datetime, int]:
    """The live table's rows per closed window, read in one pass on the server."""
    sql = (
        f"SELECT date_bin(interval '1 hour', received_at, timestamptz '{_ORIGIN}') AS start, "
        f"count(*) AS n FROM {LIVE_TABLE} "
        f"WHERE received_at < timestamptz '{_timestamp(before)}' GROUP BY 1"
    )
    rows = duck.execute(f"SELECT start, n FROM {_postgres_query(sql)}").fetchall()
    return {start.astimezone(UTC): n for start, n in rows}


def lake_window_counts(duck: duckdb.DuckDBPyConnection, since: datetime) -> dict[datetime, int]:
    rows = duck.execute(
        f"SELECT date_trunc('hour', received_at), count(*) FROM {_lake()} "
        "WHERE received_at >= ? GROUP BY 1",
        [since],
    ).fetchall()
    return {start.astimezone(UTC): n for start, n in rows}


def _lake_count(duck: duckdb.DuckDBPyConnection, start: datetime) -> int:
    row = duck.execute(
        f"SELECT count(*) FROM {_lake()} WHERE received_at >= ? AND received_at < ?",
        [start, start + WINDOW],
    ).fetchone()
    assert row is not None
    return int(row[0])


def _scalar(
    duck: duckdb.DuckDBPyConnection, sql: str, parameters: list[object] | None = None
) -> int:
    row = duck.execute(sql, parameters).fetchone()
    assert row is not None
    return int(row[0])


def archive_window(duck: duckdb.DuckDBPyConnection, start: datetime) -> tuple[int, int, int]:
    """Copy one window's live rows that the lake lacks; confirm all of them are there.

    Leaves the window's rows in the temporary table `live_window`, for the
    deletion. Returns (rows in the live window, rows inserted, rows in the lake
    window before the insert).
    """
    end = start + WINDOW
    sql = (
        f"SELECT event_id, source, signal, collected_at, received_at, payload::text AS payload "
        f"FROM {LIVE_TABLE} WHERE received_at >= timestamptz '{_timestamp(start)}' "
        f"AND received_at < timestamptz '{_timestamp(end)}'"
    )
    _execute(
        duck, f"CREATE OR REPLACE TEMP TABLE live_window AS SELECT * FROM {_postgres_query(sql)}"
    )
    lake_before = _lake_count(duck, start)
    bounds = duck.execute(
        "SELECT count(*), min(collected_at), max(collected_at) FROM live_window"
    ).fetchone()
    assert bounds is not None
    live, first, last = bounds
    if not live:
        return 0, 0, lake_before
    # The lake's rows of the same seconds of event time: an event has one
    # collected_at, so a copy of it already in the lake is among them.
    in_lake = f"(SELECT event_id FROM {_lake()} WHERE collected_at BETWEEN ? AND ?)"
    inserted = _scalar(
        duck,
        f"INSERT INTO {_lake()} SELECT event_id, source, signal, collected_at, received_at, "
        f"payload::JSON FROM live_window WHERE event_id NOT IN {in_lake} "
        "ORDER BY source, signal, collected_at",
        [first, last],
    )
    missing = _scalar(
        duck, f"SELECT count(*) FROM live_window WHERE event_id NOT IN {in_lake}", [first, last]
    )
    doubled = _scalar(
        duck,
        f"SELECT count(*) FROM (SELECT event_id FROM {_lake()} WHERE collected_at BETWEEN ? AND ? "
        "GROUP BY event_id HAVING count(*) > 1)",
        [first, last],
    )
    if missing or doubled:
        raise ArchiveMismatch(
            f"window {start:%Y-%m-%dT%H:%MZ}: {live} rows live, {missing} of them not found in "
            f"the lake after the insert, {doubled} event_id(s) in the lake more than once; "
            "nothing was deleted from the live table"
        )
    return live, inserted, lake_before


def _window_ids(duck: duckdb.DuckDBPyConnection, batch: int = 10_000) -> Iterator[str]:
    cursor = duck.execute("SELECT event_id FROM live_window")
    while rows := cursor.fetchmany(batch):
        for (event_id,) in rows:
            yield event_id


def delete_confirmed(
    pg: psycopg.Connection[TupleRow],
    duck: duckdb.DuckDBPyConnection,
    start: datetime,
    expected: int,
) -> int:
    """Delete the window's rows confirmed in the lake from the live table, and only those.

    The confirmed identifiers go to a temporary table of the session, and the
    delete joins on it, inside the window. One transaction: if it removes any
    other number of rows than were confirmed, it rolls back and the run fails.
    """
    end = start + WINDOW
    with pg.transaction():
        pg.execute("CREATE TEMP TABLE archived_ids (event_id text PRIMARY KEY) ON COMMIT DROP")
        with pg.cursor().copy("COPY archived_ids (event_id) FROM STDIN") as copy:
            for event_id in _window_ids(duck):
                copy.write_row((event_id,))
        deleted = pg.execute(
            f"DELETE FROM {LIVE_TABLE} AS e USING archived_ids AS a "
            "WHERE e.event_id = a.event_id AND e.received_at >= %s AND e.received_at < %s",
            (start, end),
        ).rowcount
        if deleted != expected:
            raise ArchiveMismatch(
                f"window {start:%Y-%m-%dT%H:%MZ}: {expected} rows confirmed in the lake, but the "
                f"delete would remove {deleted}; rolled back, nothing was deleted"
            )
    return deleted


def apply_lake_retention(duck: duckdb.DuckDBPyConnection, now: datetime) -> int:
    """Delete the raw events received before the lake's cutoff. Returns the rows deleted."""
    cutoff = lake_cutoff(now)
    old = duck.execute(f"SELECT count(*) FROM {_lake()} WHERE received_at < ?", [cutoff]).fetchone()
    assert old is not None
    if not old[0]:
        return 0
    return _scalar(duck, f"DELETE FROM {_lake()} WHERE received_at < ?", [cutoff])


_DAY_IN_PATH = re.compile(r"year=(\d{4})/month=(\d{1,2})/day=(\d{1,2})/")


def lake_size(duck: duckdb.DuckDBPyConnection) -> tuple[int, int, dict[str, int]]:
    """Rows, Parquet bytes, and bytes per UTC day of the table's current files."""
    rows = _scalar(duck, f"SELECT count(*) FROM {_lake()}")
    files = duck.execute(
        "SELECT data_file, data_file_size_bytes FROM ducklake_list_files("
        f"'lake', {_literal(LAKE_TABLE)}, schema => {_literal(LAKE_SCHEMA)})"
    ).fetchall()
    by_day: dict[str, int] = {}
    for path, size in files:
        match = _DAY_IN_PATH.search(path)
        day = f"{match[1]}-{int(match[2]):02d}-{int(match[3]):02d}" if match else "unpartitioned"
        by_day[day] = by_day.get(day, 0) + int(size)
    return rows, sum(by_day.values()), dict(sorted(by_day.items()))


def run_archive(
    duck: duckdb.DuckDBPyConnection,
    pg: psycopg.Connection[TupleRow],
    now: datetime,
) -> ArchiveReport:
    """Archive every closed window the lake lacks rows of; delete the expired ones.

    `duck` has the lake attached as `lake` and the live database as `serving`;
    `pg` is a connection to the live database as its owner.
    """
    prepare_lake(duck)
    live = live_window_counts(duck, closed_before(now))
    lake = lake_window_counts(duck, min(live)) if live else {}
    results = []
    for start in sorted(live):
        expired = is_expired(start, now)
        if live[start] <= lake.get(start, 0) and not expired:
            continue
        rows, inserted, lake_before = archive_window(duck, start)
        deleted = delete_confirmed(pg, duck, start, rows) if expired else 0
        results.append(
            WindowResult(start, rows, lake_before, inserted, _lake_count(duck, start), deleted)
        )
    lake_deleted = apply_lake_retention(duck, now)
    lake_rows, lake_bytes, by_day = lake_size(duck)
    return ArchiveReport(results, lake_deleted, lake_rows, lake_bytes, by_day)


def connect_live(
    duck: duckdb.DuckDBPyConnection, settings: dict[str, str]
) -> psycopg.Connection[TupleRow]:
    """Attach the live database to `duck`, read-only, and open a connection to it for the deletes.

    `settings` holds host, port, dbname, user, password and sslmode. The
    password goes into a DuckDB secret, not the attach string, and errors are
    redacted before they can reach a log.
    """
    secret = ", ".join(
        f"{key} {_literal(settings[name])}"
        for key, name in (
            ("HOST", "host"),
            ("PORT", "port"),
            ("DATABASE", "dbname"),
            ("USER", "user"),
            ("PASSWORD", "password"),
        )
    )
    _execute(duck, f"CREATE SECRET serving_pg (TYPE postgres, {secret})")
    sslmode = _literal(f"sslmode={settings['sslmode']}")
    _execute(duck, f"ATTACH {sslmode} AS serving (TYPE postgres, SECRET serving_pg, READ_ONLY)")
    try:
        return psycopg.connect(make_conninfo(**settings), autocommit=True)
    except psycopg.Error as error:
        raise RuntimeError(redact_secrets(str(error))) from None


def configure(duck: duckdb.DuckDBPyConnection, spill_dir: str) -> None:
    """UTC for the windows and the partitions, and a memory ceiling that spills."""
    _execute(duck, "SET TimeZone = 'UTC'")
    _execute(duck, f"SET memory_limit = {_literal(DUCKDB_MEMORY_LIMIT)}")
    _execute(duck, f"SET temp_directory = {_literal(spill_dir)}")


def live_settings() -> dict[str, str]:
    """The live database's connection, from the variables of the `serving` target.

    The same `LAGOM_PG_*` the `streaming` job's dbt reads (SERVING_ENV in
    definitions.py), as the owner of `serving`, who may read and delete.
    """
    return {
        name: _require(f"LAGOM_PG_{name.upper()}")
        for name in ("host", "port", "dbname", "user", "password", "sslmode")
    }


def archive_from_environment(now: datetime) -> ArchiveReport:
    """The job's step: the environment's lake and live database, one archive pass."""
    duck = attach_lake()
    try:
        configure(duck, os.path.join(tempfile.gettempdir(), "duckdb-archive"))
        pg = connect_live(duck, live_settings())
        try:
            return run_archive(duck, pg, now)
        finally:
            pg.close()
    finally:
        duck.close()
