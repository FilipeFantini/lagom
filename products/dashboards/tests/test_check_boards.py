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
import yaml
from check_boards import (
    CheckError,
    Gold,
    compare_earthquakes,
    empty_queries,
    load_rendered,
    read_gold,
    run,
)

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "earthquakes.data.json"
REPOSITORY = HERE.parents[2]

# id, origin time (UTC), magnitude, class, region, place, latitude, longitude,
# depth, depth class, status, reviewed, last revision. The latest earthquake
# is on 2026-11-10, so the window opens on 2026-10-12: the 4.0 on that day is
# in, the 3.0 a minute before it is out. `b` has no region.
EARTHQUAKES = [
    ("a", "2026-11-10 12:00:00+00", 5.0, "moderate", "Alaska", "10 km N of X, Alaska",
     61.0, -150.0, 10.0, "shallow", "reviewed", True, "2026-11-10 12:30:00+00"),
    ("b", "2026-11-10 03:00:00+00", 2.0, "minor", None, None,
     -10.0, 20.0, 80.0, "intermediate", "automatic", False, "2026-11-10 03:10:00+00"),
    ("c", "2026-10-12 00:00:00+00", 4.0, "light", "Chile", "5 km S of Y, Chile",
     -33.0, -72.0, 5.0, "shallow", "reviewed", True, "2026-10-12 00:10:00+00"),
    ("d", "2026-10-11 23:59:00+00", 3.0, "minor", "Peru", "9 km E of Z, Peru",
     -12.0, -77.0, 40.0, "shallow", "reviewed", True, "2026-10-11 23:59:30+00"),
]  # fmt: skip


@pytest.fixture
def connection() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    connection.execute("ATTACH ':memory:' AS lake")
    connection.execute("CREATE SCHEMA lake.gold")
    connection.execute(
        "CREATE TABLE lake.gold.fct_earthquakes (event_id VARCHAR, event_time TIMESTAMPTZ, "
        "magnitude DOUBLE, magnitude_class VARCHAR, region VARCHAR, place VARCHAR, "
        "latitude DOUBLE, longitude DOUBLE, depth_km DOUBLE, depth_class VARCHAR, "
        "status VARCHAR, is_reviewed BOOLEAN, updated_at TIMESTAMPTZ, url VARCHAR)"
    )
    for row in EARTHQUAKES:
        connection.execute(
            "INSERT INTO lake.gold.fct_earthquakes VALUES (" + ", ".join(["?"] * 14) + ")",
            [*row, f"https://example.test/{row[0]}"],
        )
    # Gold's own aggregate, as the model builds it: per UTC day and region.
    connection.execute(
        "CREATE TABLE lake.gold.agg_earthquakes_daily_region AS "
        "SELECT cast(event_time AT TIME ZONE 'UTC' AS DATE) AS event_date, region, "
        "count(*) AS event_count, count(*) FILTER (WHERE magnitude >= 4) AS "
        "count_magnitude_4_plus FROM lake.gold.fct_earthquakes GROUP BY 1, 2"
    )
    yield connection
    connection.close()


def url(event: str) -> str:
    return f"https://example.test/{event}"


def good_queries() -> dict[str, list[dict[str, Any]]]:
    """A render that agrees with `EARTHQUAKES` inside the window, written out."""
    return {
        "kpis": [
            {
                "events": 3,
                "largest": 5.0,
                "m4_plus": 2,
                "reviewed_share": 2 / 3,
                "last_event": "2026-11-10 12:00",
                "last_revision": "2026-11-10 12:30",
            }
        ],
        "daily": [
            {"day": "2026-11-10", "magnitude_class": "minor", "class_floor": 2.0, "events": 1},
            {"day": "2026-10-12", "magnitude_class": "light", "class_floor": 4.0, "events": 1},
            {"day": "2026-11-10", "magnitude_class": "moderate", "class_floor": 5.0, "events": 1},
        ],
        "by_magnitude_class": [
            {"magnitude_class": "minor", "events": 1, "lowest": 2.0},
            {"magnitude_class": "light", "events": 1, "lowest": 4.0},
            {"magnitude_class": "moderate", "events": 1, "lowest": 5.0},
        ],
        "by_depth_class": [
            {"depth_class": "shallow", "events": 2, "shallowest": 5.0},
            {"depth_class": "intermediate", "events": 1, "shallowest": 80.0},
        ],
        "top_regions": [
            {"region": "(no place given)", "events": 1},
            {"region": "Alaska", "events": 1},
            {"region": "Chile", "events": 1},
        ],
        "on_map": [
            {
                "event_id": "c", "place": "5 km S of Y, Chile", "latitude": -33.0,
                "longitude": -72.0, "magnitude": 4.0, "magnitude_class": "light",
                "depth_km": 5.0, "event_utc": "2026-10-12 00:00", "url": url("c"),
            },
            {
                "event_id": "a", "place": "10 km N of X, Alaska", "latitude": 61.0,
                "longitude": -150.0, "magnitude": 5.0, "magnitude_class": "moderate",
                "depth_km": 10.0, "event_utc": "2026-11-10 12:00", "url": url("a"),
            },
        ],
        "largest": [
            {
                "event_utc": "2026-11-10 12:00", "magnitude": 5.0, "magnitude_class": "moderate",
                "region": "Alaska", "place": "10 km N of X, Alaska", "depth_km": 10.0,
                "depth_class": "shallow", "status": "reviewed", "usgs_page": "USGS",
                "url": url("a"),
            },
            {
                "event_utc": "2026-10-12 00:00", "magnitude": 4.0, "magnitude_class": "light",
                "region": "Chile", "place": "5 km S of Y, Chile", "depth_km": 5.0,
                "depth_class": "shallow", "status": "reviewed", "usgs_page": "USGS",
                "url": url("c"),
            },
            {
                "event_utc": "2026-11-10 03:00", "magnitude": 2.0, "magnitude_class": "minor",
                "region": "(no place given)", "place": None, "depth_km": 80.0,
                "depth_class": "intermediate", "status": "automatic", "usgs_page": "USGS",
                "url": url("b"),
            },
        ],
    }  # fmt: skip


def failures_of(queries: dict[str, list[dict[str, Any]]], gold: Gold) -> list[str]:
    return compare_earthquakes(queries, gold)


@pytest.fixture
def gold(connection: duckdb.DuckDBPyConnection) -> Gold:
    return read_gold(connection)


def test_gold_is_read_for_the_window_only(gold: Gold) -> None:
    good = good_queries()
    assert gold.kpis == {**good["kpis"][0], "reviewed_share": 2 / 3}
    assert gold.events_by_day == {"2026-10-12": 1, "2026-11-10": 2}
    assert gold.daily == good["daily"]
    assert gold.by_magnitude_class == good["by_magnitude_class"]
    assert gold.by_depth_class == good["by_depth_class"]
    assert gold.top_regions == good["top_regions"]
    assert [row["event_id"] for row in gold.on_map] == ["a", "c"]
    assert [row["event_utc"] for row in gold.largest] == [
        "2026-11-10 12:00",
        "2026-10-12 00:00",
        "2026-11-10 03:00",
    ]


def test_the_window_opens_at_the_utc_day_not_at_the_clock(gold: Gold) -> None:
    # 29 days before the latest day, at its first minute, is in; the minute before is out.
    assert "2026-10-12" in gold.events_by_day
    assert "2026-10-11" not in gold.events_by_day


def test_a_render_that_agrees_with_gold_passes(gold: Gold) -> None:
    assert failures_of(good_queries(), gold) == []


def test_a_query_with_no_rows_fails_even_though_dct_exits_zero(gold: Gold) -> None:
    queries = good_queries()
    queries["top_regions"] = []
    assert empty_queries(queries) == [
        "query 'top_regions' returned no rows: dct renders an empty chart and exits 0"
    ]
    failures = failures_of(queries, gold)
    assert any("'top_regions' returned no rows" in failure for failure in failures)


def test_a_wrong_event_count_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["kpis"][0]["events"] = 4
    assert any(
        f.startswith("kpis: row 1") and "'events': 4" in f for f in failures_of(queries, gold)
    )


def test_a_wrong_magnitude_4_plus_count_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["kpis"][0]["m4_plus"] = 3
    assert any(
        f.startswith("kpis: row 1") and "'m4_plus': 3" in f for f in failures_of(queries, gold)
    )


def test_a_wrong_largest_magnitude_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["kpis"][0]["largest"] = 4.9
    assert any(
        f.startswith("kpis: row 1") and "'largest': 4.9" in f for f in failures_of(queries, gold)
    )


def test_a_wrong_reviewed_share_fails(gold: Gold) -> None:
    # `filter (where not is_reviewed)` in the board gives 1/3.
    queries = good_queries()
    queries["kpis"][0]["reviewed_share"] = 1 / 3
    failures = failures_of(queries, gold)
    assert any(f.startswith("reviewed share (KPI)") for f in failures)


def test_a_reviewed_share_that_is_the_same_quotient_passes(gold: Gold) -> None:
    queries = good_queries()
    queries["kpis"][0]["reviewed_share"] = 0.666666666666  # within 1e-9
    assert failures_of(queries, gold) == []


def test_a_stale_freshness_line_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["kpis"][0]["last_event"] = "2026-11-09 12:00"
    assert any("'last_event': '2026-11-09 12:00'" in f for f in failures_of(queries, gold))


def test_a_day_that_does_not_add_up_to_the_aggregate_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["daily"][1]["events"] = 2  # 2026-10-12: 2 on the board, 1 in gold
    failures = failures_of(queries, gold)
    assert any(f.startswith("earthquakes per UTC day") for f in failures)


def test_a_day_missing_from_the_chart_fails(gold: Gold) -> None:
    queries = good_queries()
    del queries["daily"][1]
    failures = failures_of(queries, gold)
    assert any(f.startswith("earthquakes per UTC day") for f in failures)


def test_a_class_moved_between_days_fails_though_the_day_totals_agree(gold: Gold) -> None:
    queries = good_queries()
    queries["daily"][0]["magnitude_class"] = "light"  # same day totals, wrong segment
    failures = failures_of(queries, gold)
    assert not any(f.startswith("earthquakes per UTC day") for f in failures)
    assert any(f.startswith("daily chart: row 1") for f in failures)


def test_classes_out_of_order_fail(gold: Gold) -> None:
    queries = good_queries()
    queries["by_magnitude_class"].reverse()
    assert any(f.startswith("by magnitude class: row 1") for f in failures_of(queries, gold))


def test_a_class_chart_that_drops_events_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["by_magnitude_class"].pop()
    queries["by_depth_class"][0]["events"] = 1
    failures = failures_of(queries, gold)
    assert "by magnitude class: the board has 2 rows, gold has 3" in failures
    assert any(f.startswith("by depth class: row 1") for f in failures)


def test_top_regions_cut_to_one_row_fails(gold: Gold) -> None:
    # `limit 1` in the board.
    queries = good_queries()
    queries["top_regions"] = queries["top_regions"][:1]
    assert "top regions: the board has 1 rows, gold has 3" in failures_of(queries, gold)


def test_top_regions_with_a_wrong_count_fails(gold: Gold) -> None:
    # `count(*) + 7` in the board.
    queries = good_queries()
    for row in queries["top_regions"]:
        row["events"] += 7
    assert any(f.startswith("top regions: row 1") for f in failures_of(queries, gold))


def test_top_regions_in_another_order_fail(gold: Gold) -> None:
    queries = good_queries()
    queries["top_regions"].reverse()
    assert any(f.startswith("top regions: row 1") for f in failures_of(queries, gold))


def test_a_map_that_drops_a_circle_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["on_map"].pop()
    failures = failures_of(queries, gold)
    assert "map: circles: the board has 1, gold has 2" in failures
    assert any(f.startswith("map: the board has 1 rows") for f in failures)


def test_a_circle_in_the_wrong_place_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["on_map"][1]["latitude"] = 62.0
    assert any(f.startswith("map: row 1") for f in failures_of(queries, gold))


def test_a_map_in_another_order_passes(gold: Gold) -> None:
    queries = good_queries()
    queries["on_map"].reverse()
    assert failures_of(queries, gold) == []


def test_a_table_that_does_not_start_at_the_largest_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["largest"].reverse()
    assert any(f.startswith("table: row 1") for f in failures_of(queries, gold))


def test_a_table_cell_that_differs_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["largest"][2]["depth_km"] = 81.0
    assert any(f.startswith("table: row 3") for f in failures_of(queries, gold))


def test_a_table_cut_short_fails(gold: Gold) -> None:
    queries = good_queries()
    queries["largest"].pop()
    assert "table: the board has 2 rows, gold has 3" in failures_of(queries, gold)


def test_a_query_missing_from_the_render_fails(gold: Gold) -> None:
    queries = good_queries()
    del queries["on_map"]
    assert failures_of(queries, gold) == ["queries missing from the render: on_map"]


def test_an_empty_gold_fails_even_when_the_board_agrees() -> None:
    kpis = {
        "events": 0,
        "largest": None,
        "m4_plus": 0,
        "reviewed_share": None,
        "last_event": None,
        "last_revision": None,
    }
    gold = Gold(kpis, {}, [], [], [], [], [], [])
    queries = good_queries()
    queries["kpis"] = [kpis]
    failures = failures_of(queries, gold)
    assert "gold has no earthquakes in the window: there is nothing to show" in failures


def test_the_real_render_of_the_ci_fixture_is_readable_and_agrees_with_its_gold() -> None:
    # tests/fixtures/earthquakes.data.json is `dct render --format data` on the
    # CI fixture lake (platform/warehouse/ci): it pins the format the check reads.
    board, queries = load_rendered(FIXTURE)
    assert board == "earthquakes"
    kpi = queries["kpis"][0]
    # 11 of the fixture's 12 earthquakes are in the window: ev_win_out is a second too early.
    assert kpi["events"] == 11
    assert [row["event_id"] for row in queries["on_map"] if row["magnitude"] == 4.0] == ["ev_late"]


def test_the_ci_fixtures_window_edge_is_in_the_render() -> None:
    # ev_win_in (1.0, the first second of the first day) is drawn; ev_win_out is not.
    _, queries = load_rendered(FIXTURE)
    days = {row["day"] for row in queries["daily"]}
    assert "2025-09-11" in days
    assert "2025-09-10" not in days


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
    assert "FAIL earthquakes: kpis: row 1" in capsys.readouterr().err


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
    del queries["kpis"][0]["reviewed_share"]
    path = write_render(tmp_path / "e.json", "earthquakes", queries)
    assert run([path], connection) == 1
    assert "no column or query 'reviewed_share'" in capsys.readouterr().err


def test_run_fails_when_given_nothing_to_check(connection: duckdb.DuckDBPyConnection) -> None:
    assert run([], connection) == 1


def test_the_dashboards_profile_is_the_warehouses_lake_target_plus_a_scope() -> None:
    # profiles.yml here is a copy of the warehouse's `lake` target (dct needs a
    # scope on the secret, which the warehouse's profile does not have). Nothing
    # else may differ, or a change to one connection would miss the other.
    warehouse = yaml.safe_load((REPOSITORY / "platform/warehouse/profiles.yml").read_text())
    dashboards = yaml.safe_load((HERE.parent / "profiles.yml").read_text())
    lake = warehouse["lagom"]["outputs"]["lake"]
    copy_ = copy.deepcopy(dashboards["lagom_dashboards"]["outputs"]["lake"])
    scopes = [secret.pop("scope") for secret in copy_["secrets"] if "scope" in secret]
    assert scopes == ["{{ env_var('LAGOM_LAKE_DATA_PATH') }}"]
    assert copy_ == lake
    assert dashboards["lagom_dashboards"]["target"] == "lake"


def test_a_column_the_board_lacks_fails_even_when_gold_holds_null() -> None:
    # `.get` would read the missing column as None and call it equal to the null.
    from check_boards import _differs_rows

    failures = _differs_rows("map", [{"a": 1}], [{"a": 1, "place": None}])
    assert failures == ["map: row 1: the board has no column place"]
