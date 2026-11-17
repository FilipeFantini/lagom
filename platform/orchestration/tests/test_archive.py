"""The archive of the streaming showcase's events, and the live table's retention.

The window and retention rules are tested on their own. The archive itself
runs against a real Postgres and a DuckLake on disk: Postgres holds the live
table as dbt's `serving` target creates it, and the archive reads, compares
and deletes there exactly as in the cluster. Those tests need
LAGOM_ARCHIVE_TEST_PG, a libpq string for a throwaway database whose
`live.events` they empty; CI's `warehouse (serving target)` job sets it
against its own Postgres container. Without it they are skipped, and the
reason says where they run.
"""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import psycopg
import pytest
import yaml
from psycopg.conninfo import conninfo_to_dict

from lagom_orchestration import archive
from lagom_orchestration.archive import (
    GRACE,
    LAKE_RETENTION,
    LIVE_RETENTION,
    ArchiveMismatch,
    WindowResult,
    closed_before,
    floor_hour,
    is_expired,
    lake_cutoff,
)

DAY = datetime(2026, 11, 9, tzinfo=UTC)


def test_a_window_closes_once_its_hour_ended_more_than_the_grace_ago() -> None:
    noon = DAY.replace(hour=12)
    assert closed_before(noon + GRACE - timedelta(microseconds=1)) == DAY.replace(hour=11)
    assert closed_before(noon + GRACE) == noon


def test_a_window_expires_when_all_of_it_is_older_than_the_live_retention() -> None:
    start = DAY.replace(hour=10)
    end_plus_retention = start + timedelta(hours=1) + LIVE_RETENTION
    assert not is_expired(start, end_plus_retention - timedelta(microseconds=1))
    assert is_expired(start, end_plus_retention)


def test_the_lake_keeps_whole_utc_days() -> None:
    now = DAY.replace(hour=23, minute=59)
    assert lake_cutoff(now) == DAY - LAKE_RETENTION
    assert lake_cutoff(now).hour == 0


def test_a_time_without_a_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="no time zone"):
        floor_hour(datetime(2026, 11, 9, 10))


def test_times_in_another_zone_are_cut_in_utc() -> None:
    from zoneinfo import ZoneInfo

    local = datetime(2026, 11, 9, 7, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert floor_hour(local) == DAY.replace(hour=10)


def test_the_hub_keeps_at_least_what_the_live_table_keeps() -> None:
    """ADR-0047, decision 4: the table can always be rebuilt from the hub."""
    nats = Path(__file__).resolve().parents[2] / "infra" / "streaming-staging" / "nats.yaml"
    configmap = next(d for d in yaml.safe_load_all(nats.read_text()) if d["kind"] == "ConfigMap")
    stream = json.loads(configmap["data"]["events-stream.json"])
    assert timedelta(microseconds=stream["max_age"] / 1000) >= LIVE_RETENTION


def test_the_lake_keeps_longer_than_the_live_table() -> None:
    """Otherwise the lake would drop rows still live, and the next run copy them back."""
    assert LAKE_RETENTION > LIVE_RETENTION + timedelta(days=1)


def test_a_window_describes_itself_in_utc() -> None:
    result = WindowResult(DAY.replace(hour=10), 3, 0, 3, 3, 0)
    assert result.describe() == (
        "window 2026-11-09T10:00Z: live 3, lake before 0, inserted 3, lake after 3, "
        "deleted from live 0"
    )


def test_the_live_databases_password_is_redacted_from_a_duckdb_secret() -> None:
    from lagom_orchestration.redact import redact_secrets

    text = redact_secrets(
        "CREATE SECRET serving_pg (TYPE postgres, USER 'serving', PASSWORD 'pw1')"
    )
    assert "pw1" not in text and "serving" in text


# --- Against a real Postgres -------------------------------------------------

TEST_PG = "LAGOM_ARCHIVE_TEST_PG"
needs_postgres = pytest.mark.skipif(
    TEST_PG not in os.environ,
    reason=f"{TEST_PG} is not set: these run in CI's `warehouse (serving target)` job",
)


class Live:
    """The live table in Postgres and the lake on disk, with the archive between them."""

    def __init__(self, dsn: str, tmp_path: Path) -> None:
        self.owner = psycopg.connect(dsn, autocommit=True)
        self.owner.execute("TRUNCATE live.events")
        self.duck = duckdb.connect()
        for extension in ("ducklake", "postgres"):
            self.duck.execute(f"INSTALL {extension}; LOAD {extension}")
        archive.configure(self.duck, str(tmp_path / "spill"))
        self.duck.execute(
            f"ATTACH 'ducklake:{tmp_path / 'catalog.ducklake'}' AS lake "
            f"(DATA_PATH '{tmp_path / 'data'}/')"
        )
        settings = {key: str(value) for key, value in conninfo_to_dict(dsn).items()}
        self.pg = archive.connect_live(self.duck, settings)

    def add(self, received_at: datetime, collected_at: datetime | None = None) -> str:
        collected_at = collected_at or received_at
        event_id = f"test/health/{collected_at.isoformat()}"
        payload = {"event_id": event_id, "source": "test", "signal": "health", "load_1m": 0.5}
        self.owner.execute(
            "INSERT INTO live.events VALUES (%s, 'test', 'health', %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (event_id, collected_at, received_at, json.dumps(payload)),
        )
        return event_id

    def run(self, now: datetime) -> archive.ArchiveReport:
        return archive.run_archive(self.duck, self.pg, now)

    def live_ids(self) -> set[str]:
        return {r[0] for r in self.owner.execute("SELECT event_id FROM live.events")}

    def lake_ids(self) -> list[str]:
        rows = self.duck.execute("SELECT event_id FROM lake.bronze.streaming_events").fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self.pg.close()
        self.duck.close()
        self.owner.close()


@pytest.fixture
def live(tmp_path: Path) -> Iterator[Live]:
    store = Live(os.environ[TEST_PG], tmp_path)
    yield store
    store.close()


def _seconds(start: datetime, count: int) -> list[datetime]:
    return [start + timedelta(seconds=n) for n in range(count)]


@needs_postgres
def test_the_first_run_archives_the_closed_windows_and_deletes_nothing(live: Live) -> None:
    for moment in _seconds(DAY.replace(hour=10), 5) + _seconds(DAY.replace(hour=11), 3):
        live.add(moment)
    still_open = live.add(DAY.replace(hour=12, minute=5))

    report = live.run(DAY.replace(hour=12, minute=15))

    assert [
        (w.start.hour, w.live, w.inserted, w.lake_after, w.deleted) for w in report.windows
    ] == [
        (10, 5, 5, 5, 0),
        (11, 3, 3, 3, 0),
    ]
    assert still_open not in live.lake_ids()
    assert len(live.live_ids()) == 9
    assert report.lake_rows == 8 and report.lake_bytes > 0
    assert set(report.bytes_by_day) == {"2026-11-09"}


@needs_postgres
def test_rows_reach_the_lake_unchanged(live: Live) -> None:
    received = DAY.replace(hour=10, microsecond=123456)
    event_id = live.add(received, collected_at=received - timedelta(milliseconds=300))
    live.run(DAY.replace(hour=12))

    row = live.duck.execute(
        "SELECT event_id, source, signal, collected_at, received_at, payload "
        "FROM lake.bronze.streaming_events"
    ).fetchone()
    assert row is not None
    assert row[:3] == (event_id, "test", "health")
    assert row[3] == received - timedelta(milliseconds=300)
    assert row[4] == received
    assert json.loads(row[5])["load_1m"] == 0.5


@needs_postgres
def test_a_second_run_inserts_nothing(live: Live) -> None:
    for moment in _seconds(DAY.replace(hour=10), 5):
        live.add(moment)
    live.run(DAY.replace(hour=12))
    again = live.run(DAY.replace(hour=12, minute=30))

    assert again.windows == []
    ids = live.lake_ids()
    assert len(ids) == len(set(ids)) == 5


@needs_postgres
def test_a_row_replayed_into_an_archived_window_is_archived_once(live: Live) -> None:
    first = DAY.replace(hour=10)
    live.add(first)
    live.run(DAY.replace(hour=12))
    live.add(first)  # the same event again: the primary key keeps one
    late = live.add(DAY.replace(hour=10, minute=30))  # new to the window

    report = live.run(DAY.replace(hour=13))

    assert [(w.lake_before, w.inserted, w.lake_after) for w in report.windows] == [(1, 1, 2)]
    assert sorted(live.lake_ids()) == sorted({first_id(first), late})


def first_id(moment: datetime) -> str:
    return f"test/health/{moment.isoformat()}"


@needs_postgres
def test_a_late_event_is_archived_in_the_window_it_was_received_in(live: Live) -> None:
    received = DAY.replace(hour=10, minute=1)
    live.add(received, collected_at=received - timedelta(hours=3))
    report = live.run(DAY.replace(hour=12))
    assert [w.start for w in report.windows] == [DAY.replace(hour=10)]
    assert live.run(DAY.replace(hour=13)).windows == []
    assert len(live.lake_ids()) == 1


@needs_postgres
def test_an_expired_window_leaves_the_live_table_after_its_archive(live: Live) -> None:
    old = _seconds(DAY.replace(hour=10), 4)
    for moment in old:
        live.add(moment)
    younger = live.add(DAY.replace(hour=11, minute=10))
    live.run(DAY.replace(hour=12, minute=15))

    now = DAY.replace(hour=11, minute=30) + LIVE_RETENTION
    report = live.run(now)

    assert [(w.start.hour, w.inserted, w.deleted) for w in report.windows] == [(10, 0, 4)]
    assert live.live_ids() == {younger}
    assert len(live.lake_ids()) == 5


@needs_postgres
def test_an_expired_window_never_archived_is_archived_then_deleted(live: Live) -> None:
    for moment in _seconds(DAY.replace(hour=10), 3):
        live.add(moment)
    report = live.run(DAY.replace(hour=12) + LIVE_RETENTION)
    assert [(w.inserted, w.deleted, w.lake_after) for w in report.windows] == [(3, 3, 3)]
    assert live.live_ids() == set()


@needs_postgres
def test_a_rebuilt_table_that_holds_fewer_rows_than_the_lake_is_not_a_failure(live: Live) -> None:
    """After a crash the hub replays only its last 7 days: a window can come back partial."""
    for moment in _seconds(DAY.replace(hour=10), 4):
        live.add(moment)
    live.run(DAY.replace(hour=12))
    live.owner.execute(
        "DELETE FROM live.events WHERE collected_at < %s", (DAY.replace(hour=10, second=2),)
    )

    assert live.run(DAY.replace(hour=13)).windows == []
    report = live.run(DAY.replace(hour=12) + LIVE_RETENTION)
    assert [(w.live, w.lake_before, w.inserted, w.deleted) for w in report.windows] == [
        (2, 4, 0, 2)
    ]


@needs_postgres
def test_an_event_twice_in_the_lake_stops_the_run_before_any_delete(live: Live) -> None:
    for moment in _seconds(DAY.replace(hour=10), 2):
        live.add(moment)
    live.run(DAY.replace(hour=12))
    live.duck.execute(
        "INSERT INTO lake.bronze.streaming_events "
        "SELECT * FROM lake.bronze.streaming_events LIMIT 1"
    )

    with pytest.raises(ArchiveMismatch, match="1 event_id\\(s\\) in the lake more than once"):
        live.run(DAY.replace(hour=12) + LIVE_RETENTION)
    assert len(live.live_ids()) == 2


@needs_postgres
def test_the_delete_rolls_back_unless_it_removes_exactly_the_confirmed_rows(live: Live) -> None:
    for moment in _seconds(DAY.replace(hour=10), 3):
        live.add(moment)
    archive.prepare_lake(live.duck)
    rows, _, _ = archive.archive_window(live.duck, DAY.replace(hour=10))

    with pytest.raises(ArchiveMismatch, match="rolled back"):
        archive.delete_confirmed(live.pg, live.duck, DAY.replace(hour=10), rows + 1)
    assert len(live.live_ids()) == 3


@needs_postgres
def test_a_row_added_after_the_read_is_not_deleted(live: Live) -> None:
    """Only the identifiers confirmed in the lake are deleted, even inside the window."""
    for moment in _seconds(DAY.replace(hour=10), 3):
        live.add(moment)
    archive.prepare_lake(live.duck)
    rows, _, _ = archive.archive_window(live.duck, DAY.replace(hour=10))
    replayed = live.add(DAY.replace(hour=10, minute=40))

    assert archive.delete_confirmed(live.pg, live.duck, DAY.replace(hour=10), rows) == 3
    assert live.live_ids() == {replayed}


@needs_postgres
def test_the_lake_deletes_whole_days_past_its_retention(live: Live) -> None:
    live.add(DAY.replace(hour=10))
    kept = live.add(DAY.replace(hour=10) + timedelta(days=1))
    live.run(DAY + timedelta(days=1, hours=12))

    report = live.run(DAY + timedelta(days=1) + LAKE_RETENTION + timedelta(minutes=30))

    assert report.lake_deleted == 1
    assert live.lake_ids() == [kept]
    assert set(report.bytes_by_day) == {"2026-11-10"}
    assert live.live_ids() == set()


@needs_postgres
def test_the_lake_table_is_partitioned_by_day_in_zstd_with_nothing_inlined(live: Live) -> None:
    live.add(DAY.replace(hour=10))
    live.run(DAY.replace(hour=12))
    options = dict(
        live.duck.execute(
            "SELECT option_name, value FROM lake.options() "
            "WHERE scope = 'TABLE' AND scope_entry = 'bronze.streaming_events'"
        ).fetchall()
    )
    assert options == archive.TABLE_OPTIONS
    files = live.duck.execute(
        "SELECT data_file FROM ducklake_list_files('lake', 'streaming_events', schema => 'bronze')"
    ).fetchall()
    assert [f for (f,) in files if "/year=2026/month=11/day=9/" in f]


@needs_postgres
def test_options_lost_after_the_table_was_created_are_set_again(live: Live) -> None:
    archive.prepare_lake(live.duck)
    live.duck.execute(
        "CALL lake.set_option('parquet_compression', 'snappy', "
        "schema => 'bronze', table_name => 'streaming_events')"
    )
    archive.prepare_lake(live.duck)
    compression = live.duck.execute(
        "SELECT value FROM lake.options() WHERE option_name = 'parquet_compression' "
        "AND scope_entry = 'bronze.streaming_events'"
    ).fetchone()
    assert compression == ("zstd",)
