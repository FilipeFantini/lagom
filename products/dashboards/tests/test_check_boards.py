"""The gate fails where `dct render` does not. Each test breaks one thing a
render can get wrong and shows that the check names it.

No lake and no network: gold is a small in-memory DuckDB with the same table
and column names, and the render is a dict shaped like `--format data`.
"""

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from check_boards import (
    CheckError,
    Gold,
    compare_earthquakes,
    empty_queries,
    load_rendered,
    read_gold,
    run,
)

FIXTURE = Path(__file__).parent / "fixtures" / "earthquakes.data.json"

# Origin times (UTC), magnitude, last revision. The latest earthquake is on
# 2026-11-10, so the window opens on 2026-10-12: the 4.0 on that day is in,
# the 3.0 a minute before it is out.
EARTHQUAKES = [
    ("a", "2026-11-10 12:00:00+00", 5.0, "2026-11-10 12:30:00+00"),
    ("b", "2026-11-10 03:00:00+00", 2.0, "2026-11-10 03:10:00+00"),
    ("c", "2026-10-12 00:00:00+00", 4.0, "2026-10-12 00:10:00+00"),
    ("d", "2026-10-11 23:59:00+00", 3.0, "2026-10-11 23:59:30+00"),
]


@pytest.fixture
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    connection.execute("ATTACH ':memory:' AS lake")
    connection.execute("CREATE SCHEMA lake.gold")
    connection.execute(
        "CREATE TABLE lake.gold.fct_earthquakes "
        "(event_id VARCHAR, event_time TIMESTAMPTZ, magnitude DOUBLE, updated_at TIMESTAMPTZ)"
    )
    connection.executemany("INSERT INTO lake.gold.fct_earthquakes VALUES (?, ?, ?, ?)", EARTHQUAKES)
    # Gold's own aggregate, as the model builds it: per UTC day and region.
    connection.execute(
        "CREATE TABLE lake.gold.agg_earthquakes_daily_region AS "
        "SELECT cast(event_time AT TIME ZONE 'UTC' AS DATE) AS event_date, 'Alaska' AS region, "
        "count(*) AS event_count, count(*) FILTER (WHERE magnitude >= 4) AS "
        "count_magnitude_4_plus FROM lake.gold.fct_earthquakes GROUP BY 1"
    )
    yield connection
    connection.close()


def good_queries() -> dict[str, list[dict[str, Any]]]:
    """A render that agrees with `EARTHQUAKES` inside the window."""
    return {
        "kpis": [
            {
                "events": 3,
                "largest": 5.0,
                "m4_plus": 2,
                "reviewed_share": 0.0,
                "last_event": "2026-11-10 12:00",
                "last_revision": "2026-11-10 12:30",
            }
        ],
        "daily": [
            {"day": "2026-10-12", "magnitude_class": "light", "class_floor": 4.0, "events": 1},
            {"day": "2026-11-10", "magnitude_class": "minor", "class_floor": 2.0, "events": 1},
            {"day": "2026-11-10", "magnitude_class": "moderate", "class_floor": 5.0, "events": 1},
        ],
        "by_magnitude_class": [
            {"magnitude_class": "minor", "events": 1, "lowest": 2.0},
            {"magnitude_class": "light", "events": 1, "lowest": 4.0},
            {"magnitude_class": "moderate", "events": 1, "lowest": 5.0},
        ],
        "by_depth_class": [{"depth_class": "shallow", "events": 3, "shallowest": 1.0}],
        "top_regions": [{"region": "Alaska", "events": 3}],
        "on_map": [{"event_id": "a", "magnitude": 5.0}, {"event_id": "c", "magnitude": 4.0}],
        "largest": [{"magnitude": 5.0}, {"magnitude": 4.0}, {"magnitude": 2.0}],
    }


def gold_for_good_queries() -> Gold:
    return Gold(
        events=3,
        largest=5.0,
        m4_plus=2,
        last_event="2026-11-10 12:00",
        last_revision="2026-11-10 12:30",
        events_by_day={"2026-10-12": 1, "2026-11-10": 2},
    )


def test_gold_is_read_for_the_window_only(connection: duckdb.DuckDBPyConnection) -> None:
    assert read_gold(connection) == gold_for_good_queries()


def test_the_window_opens_at_the_utc_day_not_at_the_clock(
    connection: duckdb.DuckDBPyConnection,
) -> None:
    # 29 days before the latest day, at its first minute, is in; the day before is out.
    gold = read_gold(connection)
    assert "2026-10-12" in gold.events_by_day
    assert "2026-10-11" not in gold.events_by_day


def test_a_render_that_agrees_with_gold_passes(connection: duckdb.DuckDBPyConnection) -> None:
    assert compare_earthquakes(good_queries(), read_gold(connection)) == []


def test_a_query_with_no_rows_fails_even_though_dct_exits_zero() -> None:
    queries = good_queries()
    queries["top_regions"] = []
    assert empty_queries(queries) == [
        "query 'top_regions' returned no rows: dct renders an empty chart and exits 0"
    ]
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert any("'top_regions' returned no rows" in failure for failure in failures)


def test_a_wrong_event_count_fails() -> None:
    queries = good_queries()
    queries["kpis"][0]["events"] = 4
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "earthquakes (KPI): the board has 4, gold has 3" in failures


def test_a_wrong_magnitude_4_plus_count_fails() -> None:
    queries = good_queries()
    queries["kpis"][0]["m4_plus"] = 3
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "magnitude 4.0 or more (KPI): the board has 3, gold has 2" in failures


def test_a_wrong_largest_magnitude_fails() -> None:
    queries = good_queries()
    queries["kpis"][0]["largest"] = 4.9
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "largest magnitude (KPI): the board has 4.9, gold has 5.0" in failures


def test_a_stale_freshness_line_fails() -> None:
    queries = good_queries()
    queries["kpis"][0]["last_event"] = "2026-11-09 12:00"
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert any(failure.startswith("latest earthquake (UTC)") for failure in failures)


def test_a_day_that_does_not_add_up_to_the_aggregate_fails() -> None:
    queries = good_queries()
    queries["daily"][0]["events"] = 2  # 2026-10-12: 2 on the board, 1 in gold
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert any(failure.startswith("earthquakes per UTC day") for failure in failures)


def test_a_day_missing_from_the_chart_fails() -> None:
    queries = good_queries()
    del queries["daily"][0]
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert any(failure.startswith("earthquakes per UTC day") for failure in failures)


def test_a_class_chart_that_drops_events_fails() -> None:
    queries = good_queries()
    queries["by_magnitude_class"].pop()
    queries["by_depth_class"][0]["events"] = 2
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "by_magnitude_class: total: the board has 2, gold has 3" in failures
    assert "by_depth_class: total: the board has 2, gold has 3" in failures


def test_a_map_that_drops_a_circle_fails() -> None:
    queries = good_queries()
    queries["on_map"].pop()
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "map: circles: the board has 1, gold has 2" in failures


def test_a_table_that_does_not_start_at_the_largest_fails() -> None:
    queries = good_queries()
    queries["largest"][0]["magnitude"] = 4.0
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert "table: first row's magnitude: the board has 4.0, gold has 5.0" in failures


def test_a_query_missing_from_the_render_fails() -> None:
    queries = good_queries()
    del queries["on_map"]
    failures = compare_earthquakes(queries, gold_for_good_queries())
    assert failures == ["queries missing from the render: on_map"]


def test_an_empty_gold_fails_even_when_the_board_agrees() -> None:
    gold = Gold(0, None, 0, None, None, {})
    queries = good_queries()
    queries["kpis"] = [
        {"events": 0, "largest": None, "m4_plus": 0, "last_event": None, "last_revision": None}
    ]
    failures = compare_earthquakes(queries, gold)
    assert "gold has no earthquakes in the window: there is nothing to show" in failures


def test_the_real_render_of_the_ci_fixture_is_readable_and_agrees_with_its_gold() -> None:
    # tests/fixtures/earthquakes.data.json is `dct render --format data` on the
    # CI fixture lake (platform/warehouse/ci): it pins the format the check reads.
    board, queries = load_rendered(FIXTURE)
    assert board == "earthquakes"
    gold = Gold(
        events=10,
        largest=8.2,
        m4_plus=6,
        last_event="2025-10-10 00:00",
        last_revision="2025-10-10 00:00",
        events_by_day={"2025-10-09": 9, "2025-10-10": 1},
    )
    assert compare_earthquakes(queries, gold) == []


def test_a_file_that_is_not_a_render_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"id": "earthquakes"}))
    with pytest.raises(CheckError, match="not a dct data render"):
        load_rendered(path)
    path.write_text("not json")
    with pytest.raises(CheckError, match="not a dct data render"):
        load_rendered(path)


def write_render(path: Path, board: str, queries: dict[str, list[dict[str, Any]]]) -> Path:
    path.write_text(
        json.dumps({"id": board, "queries": {n: {"rows": r} for n, r in queries.items()}})
    )
    return path


def test_run_passes_for_a_good_render_and_prints_ok(
    tmp_path: Path, connection: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_render(tmp_path / "e.json", "earthquakes", good_queries())
    assert run([path], connection) == 0
    assert capsys.readouterr().out.startswith("OK earthquakes")


def test_run_fails_with_one_line_per_failure(
    tmp_path: Path, connection: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    queries = copy.deepcopy(good_queries())
    queries["kpis"][0]["events"] = 99
    path = write_render(tmp_path / "e.json", "earthquakes", queries)
    assert run([path], connection) == 1
    assert (
        "FAIL earthquakes: earthquakes (KPI): the board has 99, gold has 3"
        in capsys.readouterr().err
    )


def test_run_fails_for_a_board_with_no_check(
    tmp_path: Path, connection: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_render(tmp_path / "n.json", "new_board", {"q": [{"x": 1}]})
    assert run([path], connection) == 1
    assert "no check for board 'new_board'" in capsys.readouterr().err


def test_run_fails_when_a_column_the_check_reads_is_gone(
    tmp_path: Path, connection: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    queries = good_queries()
    del queries["kpis"][0]["m4_plus"]
    path = write_render(tmp_path / "e.json", "earthquakes", queries)
    assert run([path], connection) == 1
    assert "no column or query 'm4_plus'" in capsys.readouterr().err


def test_run_fails_when_given_nothing_to_check(connection: duckdb.DuckDBPyConnection) -> None:
    assert run([], connection) == 1
