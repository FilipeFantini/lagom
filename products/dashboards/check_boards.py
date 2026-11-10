"""The gate on a rendered board: what `dct render` does not fail on.

`dct render` exits 0 when a query returns no rows (a warning only), and it
cannot know whether a number on the page is the one the warehouse holds. This
reads what a render computed (`dct render --format data`) and compares it with
gold in the same lake:

- no query on the board returned zero rows;
- every column of every query the earthquakes board draws, from the headline
  figures to each circle on the map and each row of the table, equals what
  gold holds over the same window.

Only the rendered rows are compared. Not compared: the constant `usgs_page`
label of the table, the board's static text, and how a chart draws its rows
(the look is reviewed by eye).

Gold is read by queries of this file, written apart from the board's, from
`fct_earthquakes` and, where it has the figure, from the aggregate
`agg_earthquakes_daily_region`: the count of magnitude 4.0 or more, the events
per UTC day and the top regions come from the aggregate, so the board's own path
(the fact table) is checked against a second one.

Usage, with the same LAGOM_LAKE_* and LAGOM_R2_* variables the render used:

    python check_boards.py earthquakes.data.json [...]

Exit 0 when every board passes; 1 with one line per failure otherwise.
"""

import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from lagom_orchestration.lake import attach_lake

Row = dict[str, Any]
Rows = list[Row]
Queries = Mapping[str, Rows]

# The earthquakes board's window, in UTC days up to the latest earthquake in
# gold. It is written in the board's `base` query too; the check restates it
# on purpose, because the check is the second opinion.
WINDOW_DAYS = 30
FCT = "lake.gold.fct_earthquakes"
AGG = "lake.gold.agg_earthquakes_daily_region"
UTC_MINUTE = "'%Y-%m-%d %H:%M'"
NO_REGION = "'(no place given)'"
TOP_REGIONS = 15
LARGEST_ROWS = 100
# A share is a quotient of two counts; the board's SQL and this file divide in
# different types.
SHARE_TOLERANCE = 1e-9

# The queries the earthquakes board draws. One that is missing from the render
# means the board was changed without the check.
EARTHQUAKES_QUERIES = (
    "kpis",
    "daily",
    "by_magnitude_class",
    "by_depth_class",
    "top_regions",
    "on_map",
    "largest",
)


class CheckError(Exception):
    """The input is not something the check can read."""


@dataclass(frozen=True)
class Gold:
    """What gold holds for the board's window, in the shape the board draws it."""

    kpis: Row
    events_by_day: Mapping[str, int]
    daily: Rows
    by_magnitude_class: Rows
    by_depth_class: Rows
    top_regions: Rows
    on_map: Rows
    largest: Rows

    @property
    def events(self) -> int:
        return int(self.kpis["events"])


def load_rendered(path: Path) -> tuple[str, dict[str, Rows]]:
    """The board's id and its queries' rows, from a `dct render --format data` file."""
    try:
        document = json.loads(path.read_text())
        queries = {name: query["rows"] for name, query in document["queries"].items()}
        return str(document["id"]), queries
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise CheckError(f"{path}: not a dct data render ({error!r})") from error


def _rows(connection: duckdb.DuckDBPyConnection, sql: str) -> Rows:
    cursor = connection.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def read_gold(connection: duckdb.DuckDBPyConnection) -> Gold:
    """Gold's figures for the earthquakes board's window, computed from gold alone."""
    day = "cast(event_time at time zone 'UTC' as date)"
    latest_day = f"(select cast(max(event_time at time zone 'UTC') as date) from {FCT})"
    first_day = f"({latest_day} - {WINDOW_DAYS - 1})"
    in_fct = f"{day} >= {first_day}"
    in_agg = f"event_date >= {first_day}"
    event_utc = f"strftime(event_time at time zone 'UTC', {UTC_MINUTE})"
    region = f"coalesce(region, {NO_REGION})"

    (kpi,) = _rows(
        connection,
        f"select count(*) as events, max(magnitude) as largest, "
        f"count(*) filter (where is_reviewed) as reviewed, "
        f"strftime(max(event_time) at time zone 'UTC', {UTC_MINUTE}) as last_event, "
        f"strftime(max(updated_at) at time zone 'UTC', {UTC_MINUTE}) as last_revision "
        f"from {FCT} where {in_fct}",
    )
    (agg,) = _rows(
        connection,
        f"select coalesce(sum(count_magnitude_4_plus), 0) as m4_plus from {AGG} where {in_agg}",
    )
    events = int(kpi["events"])
    kpis = {
        "events": events,
        "largest": kpi["largest"],
        "m4_plus": int(agg["m4_plus"]),
        "reviewed_share": kpi["reviewed"] / events if events else None,
        "last_event": kpi["last_event"],
        "last_revision": kpi["last_revision"],
    }
    by_day = _rows(
        connection,
        f"select strftime(event_date, '%Y-%m-%d') as day, sum(event_count) as events "
        f"from {AGG} where {in_agg} group by 1",
    )
    return Gold(
        kpis=kpis,
        events_by_day={row["day"]: int(row["events"]) for row in by_day},
        daily=_rows(
            connection,
            f"select strftime(event_time at time zone 'UTC', '%Y-%m-%d') as day, magnitude_class, "
            f"min(min(magnitude)) over (partition by magnitude_class) as class_floor, "
            f"count(*) as events from {FCT} where {in_fct} group by 1, 2 order by 3, 1",
        ),
        by_magnitude_class=_rows(
            connection,
            f"select magnitude_class, count(*) as events, min(magnitude) as lowest "
            f"from {FCT} where {in_fct} group by 1 order by 3",
        ),
        by_depth_class=_rows(
            connection,
            f"select depth_class, count(*) as events, min(depth_km) as shallowest "
            f"from {FCT} where {in_fct} group by 1 order by 3",
        ),
        # From the aggregate, summed per region: a second path to the same counts.
        top_regions=_rows(
            connection,
            f"select {region} as region, cast(sum(event_count) as bigint) as events "
            f"from {AGG} where {in_agg} group by 1 order by 2 desc, 1 limit {TOP_REGIONS}",
        ),
        on_map=_rows(
            connection,
            f"select event_id, place, latitude, longitude, magnitude, magnitude_class, "
            f"depth_km, {event_utc} as event_utc, url from {FCT} "
            f"where {in_fct} and magnitude >= 4 order by event_id",
        ),
        largest=_rows(
            connection,
            f"select {event_utc} as event_utc, magnitude, magnitude_class, {region} as region, "
            f"place, depth_km, depth_class, status, url from {FCT} where {in_fct} "
            f"order by magnitude desc, event_utc desc, event_id limit {LARGEST_ROWS}",
        ),
    )


def empty_queries(queries: Queries) -> list[str]:
    """One failure for each query that returned no rows."""
    return [
        f"query '{name}' returned no rows: dct renders an empty chart and exits 0"
        for name, rows in queries.items()
        if not rows
    ]


def _differs(what: str, shown: object, gold: object) -> list[str]:
    if shown == gold:
        return []
    return [f"{what}: the board has {shown!r}, gold has {gold!r}"]


def _differs_rows(what: str, shown: Rows, gold: Rows) -> list[str]:
    """Rows compared in order, column by column; the first difference is named."""
    if len(shown) != len(gold):
        return [f"{what}: the board has {len(shown)} rows, gold has {len(gold)}"]
    for index, (board_row, gold_row) in enumerate(zip(shown, gold, strict=True)):
        # A column gold has and the board lacks is a failure even when gold's
        # value is null: `.get` would read the missing column as that null.
        absent = sorted(set(gold_row) - set(board_row))
        if absent:
            return [f"{what}: row {index + 1}: the board has no column {', '.join(absent)}"]
        # The board may carry columns gold has no figure for (a constant label).
        trimmed = {column: board_row[column] for column in gold_row}
        if trimmed != gold_row:
            return [f"{what}: row {index + 1}: the board has {trimmed!r}, gold has {gold_row!r}"]
    return []


def compare_earthquakes(queries: Queries, gold: Gold) -> list[str]:
    """Failures of the earthquakes board against `gold`; empty when it is right."""
    failures = empty_queries(queries)
    missing = [name for name in EARTHQUAKES_QUERIES if name not in queries]
    if missing:
        return failures + [f"queries missing from the render: {', '.join(missing)}"]
    if gold.events == 0:
        failures.append("gold has no earthquakes in the window: there is nothing to show")
    kpis = queries["kpis"]
    if len(kpis) != 1:
        return failures + [f"kpis: expected one row, the board has {len(kpis)}"]
    kpi, expected = kpis[0], dict(gold.kpis)
    share = expected.pop("reviewed_share")
    failures += _differs_rows("kpis", [kpi], [expected])
    shown = kpi["reviewed_share"]
    if (shown is None) != (share is None) or (
        share is not None and not math.isclose(shown, share, rel_tol=0, abs_tol=SHARE_TOLERANCE)
    ):
        failures.append(f"reviewed share (KPI): the board has {shown!r}, gold has {share!r}")

    per_day: dict[str, int] = {}
    for row in queries["daily"]:
        per_day[row["day"]] = per_day.get(row["day"], 0) + row["events"]
    failures += _differs("earthquakes per UTC day", per_day, dict(gold.events_by_day))
    failures += _differs_rows("daily chart", queries["daily"], gold.daily)
    failures += _differs_rows(
        "by magnitude class", queries["by_magnitude_class"], gold.by_magnitude_class
    )
    failures += _differs_rows("by depth class", queries["by_depth_class"], gold.by_depth_class)
    failures += _differs_rows("top regions", queries["top_regions"], gold.top_regions)
    # The map has no order that means anything; compare by event.
    failures += _differs("map: circles", len(queries["on_map"]), int(gold.kpis["m4_plus"]))
    failures += _differs_rows(
        "map", sorted(queries["on_map"], key=lambda row: row["event_id"]), gold.on_map
    )
    failures += _differs_rows("table", queries["largest"], gold.largest)
    return failures


def check_earthquakes(queries: Queries, connection: duckdb.DuckDBPyConnection) -> list[str]:
    return compare_earthquakes(queries, read_gold(connection))


# One function per board, keyed by the board's id. A board with no entry fails
# the check: a new board brings its own comparison.
BOARD_CHECKS: dict[str, Callable[[Queries, duckdb.DuckDBPyConnection], list[str]]] = {
    "earthquakes": check_earthquakes,
}


def run(paths: Sequence[Path], connection: duckdb.DuckDBPyConnection) -> int:
    """Check every render in `paths`; print the failures; return the exit code."""
    if not paths:
        print("check_boards: no render given", file=sys.stderr)
        return 1
    failed = False
    for path in paths:
        board, queries = load_rendered(path)
        check = BOARD_CHECKS.get(board)
        if check is None:
            failures = [f"no check for board '{board}': add one to BOARD_CHECKS"]
        else:
            try:
                failures = check(queries, connection)
            except KeyError as error:
                # A column the check reads is not in the render: the board changed.
                failures = [f"the render has no column or query {error}"]
        for failure in failures:
            print(f"FAIL {board}: {failure}", file=sys.stderr)
        failed = failed or bool(failures)
        if not failures:
            print(f"OK {board}: every query has rows and every column matches gold")
    return 1 if failed else 0


def main(argv: Sequence[str]) -> int:
    try:
        return run([Path(arg) for arg in argv], attach_lake())
    except CheckError as error:
        print(f"check_boards: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
