"""The gate on a rendered board: what `dct render` does not fail on.

`dct render` exits 0 when a query returns no rows (a warning only), and it
cannot know whether a number on the page is the one the warehouse holds. This
reads what a render computed (`dct render --format data`) and compares it with
gold in the same lake:

- no query on the board returned zero rows;
- every figure the page shows, and every chart's total, equals what gold holds.

Usage, with the same LAGOM_LAKE_* and LAGOM_R2_* variables the render used:

    python check_boards.py earthquakes.data.json [...]

Exit 0 when every board passes; 1 with one line per failure otherwise.
"""

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from lagom_orchestration.lake import attach_lake

Rows = list[dict[str, Any]]
Queries = Mapping[str, Rows]

# The earthquakes board's window, in UTC days up to the latest earthquake in
# gold. It is written in the board's `base` query too; the check restates it
# on purpose, because the check is the second opinion.
WINDOW_DAYS = 30
FCT = "lake.gold.fct_earthquakes"
AGG = "lake.gold.agg_earthquakes_daily_region"
UTC_MINUTE = "'%Y-%m-%d %H:%M'"

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
LARGEST_ROWS = 100


class CheckError(Exception):
    """The input is not something the check can read."""


@dataclass(frozen=True)
class Gold:
    """What gold holds for the board's window."""

    events: int
    largest: float | None
    m4_plus: int
    last_event: str | None
    last_revision: str | None
    events_by_day: Mapping[str, int]


def load_rendered(path: Path) -> tuple[str, dict[str, Rows]]:
    """The board's id and its queries' rows, from a `dct render --format data` file."""
    try:
        document = json.loads(path.read_text())
        queries = {name: query["rows"] for name, query in document["queries"].items()}
        return str(document["id"]), queries
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise CheckError(f"{path}: not a dct data render ({error!r})") from error


def read_gold(connection: duckdb.DuckDBPyConnection) -> Gold:
    """Gold's figures for the earthquakes board's window, computed from gold alone."""
    first_day = (
        f"(select cast(max(event_time at time zone 'UTC') as date) - {WINDOW_DAYS - 1} from {FCT})"
    )
    in_fct = f"cast(event_time at time zone 'UTC' as date) >= {first_day}"
    in_agg = f"event_date >= {first_day}"
    fct = connection.execute(
        f"select count(*), max(magnitude), "
        f"strftime(max(event_time at time zone 'UTC'), {UTC_MINUTE}), "
        f"strftime(max(updated_at at time zone 'UTC'), {UTC_MINUTE}) "
        f"from {FCT} where {in_fct}"
    ).fetchone()
    agg = connection.execute(
        f"select coalesce(sum(count_magnitude_4_plus), 0) from {AGG} where {in_agg}"
    ).fetchone()
    by_day = connection.execute(
        f"select strftime(event_date, '%Y-%m-%d'), sum(event_count) from {AGG} "
        f"where {in_agg} group by 1"
    ).fetchall()
    if fct is None or agg is None:
        raise CheckError("gold returned no row for an aggregate query")
    return Gold(
        events=int(fct[0]),
        largest=fct[1],
        m4_plus=int(agg[0]),
        last_event=fct[2],
        last_revision=fct[3],
        events_by_day={day: int(count) for day, count in by_day},
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
    kpi = kpis[0]
    failures += _differs("earthquakes (KPI)", kpi["events"], gold.events)
    failures += _differs("largest magnitude (KPI)", kpi["largest"], gold.largest)
    failures += _differs("magnitude 4.0 or more (KPI)", kpi["m4_plus"], gold.m4_plus)
    failures += _differs("latest earthquake (UTC)", kpi["last_event"], gold.last_event)
    failures += _differs("latest USGS revision (UTC)", kpi["last_revision"], gold.last_revision)

    per_day: dict[str, int] = {}
    for row in queries["daily"]:
        per_day[row["day"]] = per_day.get(row["day"], 0) + row["events"]
    failures += _differs("earthquakes per UTC day", per_day, dict(gold.events_by_day))
    for name, column in (("by_magnitude_class", "events"), ("by_depth_class", "events")):
        failures += _differs(
            f"{name}: total", sum(row[column] for row in queries[name]), gold.events
        )
    failures += _differs("map: circles", len(queries["on_map"]), gold.m4_plus)
    largest = queries["largest"]
    failures += _differs("table: rows", len(largest), min(LARGEST_ROWS, gold.events))
    if largest:
        failures += _differs("table: first row's magnitude", largest[0]["magnitude"], gold.largest)
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
            print(f"OK {board}: every query has rows and every figure matches gold")
    return 1 if failed else 0


def main(argv: Sequence[str]) -> int:
    try:
        return run([Path(arg) for arg in argv], attach_lake())
    except CheckError as error:
        print(f"check_boards: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
